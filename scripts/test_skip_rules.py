"""Offline accessory classification, state safety and local HTTP regression checks."""
import copy
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.request import Request,urlopen
from urllib.error import HTTPError

import editor_server as app
import skip_rules


class SkipRulesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        folder=app.ROOT/'work'/'skip_rule_tests';folder.mkdir(parents=True,exist_ok=True)
        cls.root=Path(tempfile.mkdtemp(prefix='run_',dir=folder))

    def editor(self):return app.Editor(self.root/self._testMethodName/'queue.json',start_worker=False)
    def product(self,e,title):e.board.paste({'text':title});return e.state['products'][-1]

    def test_default_accessories_and_device_exceptions(self):
        rules=skip_rules.defaults()
        for title in ['Oyaide NEO d+ USB线classB A S专业DJ声卡线解码器打印机口','Roland SP-404MKII 硬壳收纳包','雅佳 MPC Live3 PC保护盒/防尘罩','Van Damme 音频线','雅佳SOLOc喇叭改造底座']:
            self.assertIsNotNone(skip_rules.match(title,rules),title)
        for title in ['Xvive U2吉他无线接收器蓝牙乐器连接线','Xvive AT-1音频测试仪检测XLR卡农线','耳机放大器带音频线','Shure SM7B 麦克风','miniDSP 2X4HD','RADIAL StageBug SB-1 DI盒']:
            self.assertIsNone(skip_rules.match(title,rules),title)

    def test_rule_validation_and_literal_matching(self):
        rules=skip_rules.normalize({'enabled':True,'groups':[{'label':'自定义','keywords':['USB 线','usb线','a+b']}],'exceptions':[]})
        self.assertEqual(len(rules['groups'][0]['keywords']),2)
        self.assertIsNotNone(skip_rules.match('NEO usb 线',rules));self.assertIsNone(skip_rules.match('aaab',rules))
        for value in [None,{'enabled':'yes'},{'groups':[{'label':'x','keywords':['线']}]},{'groups':[{'label':'','keywords':['配件']}]},{'exceptions':['x']}]:
            with self.assertRaises(ValueError):skip_rules.normalize(value)

    def test_accessory_done_is_not_a_video(self):
        e=self.editor();p=self.product(e,'Oyaide 音频线');view=e.board.view(p)
        self.assertEqual((view['status'],view['status_label']),('done','配件跳过'))
        self.assertTrue(view['skipped']);self.assertFalse(view['output_url']);self.assertEqual(view['version_count'],0)
        counts=e.board.snapshot({'view':['done']})['counts'];self.assertEqual((counts['done'],counts['skipped']),(1,1))
        self.assertFalse(e.state['tasks'])

    def test_bulk_collect_render_download_never_queue_accessories(self):
        e=self.editor();p=self.product(e,'Roland SP-404MKII 收纳包')
        for job in ['collect','render','download']:
            result=e.board.bulk({'ids':[p['id']],'job':job})
            self.assertEqual(result['added'],0);self.assertEqual(result['skipped'],[p['id']]);self.assertFalse(result['errors'])
        self.assertFalse(e.state['tasks'])
        with self.assertRaises(ValueError):e.board.redo(p['id'],{})

    def test_worker_guard_does_not_search_or_download(self):
        e=self.editor();p=self.product(e,'Roland SP-404MKII 收纳包')
        task=e.parse_line(p['title']);task.update(status='queued');e.state['tasks'].append(task);e.board.attach(task,p)
        with patch.object(e,'search') as search,patch.object(e,'command') as command:
            e.process(task);search.assert_not_called();command.assert_not_called()
        self.assertEqual(task['status'],'skipped');self.assertFalse(task.get('output_url'))

    def test_import_preview_and_commit_classify_without_jobs(self):
        e=self.editor();p=e.board.upload('a.csv','商品名称\nRoland 收纳包\nShure SM7B\n'.encode())
        self.assertEqual(p['summary']['skip_count'],1);self.assertEqual(p['preview'][0]['status'],'配件跳过')
        e.board.commit({'token':p['token']});counts=e.board.snapshot({'view':['all']})['counts']
        self.assertEqual((counts['all'],counts['done'],counts['todo']),(2,1,1));self.assertFalse(e.state['tasks'])

    def test_single_product_override_and_restore(self):
        e=self.editor();p=self.product(e,'Roland SP-404MKII 收纳包')
        e.board.set_skip(p['id'],{'mode':'search'});self.assertFalse(e.board.view(p)['skipped']);self.assertEqual(e.board.view(p)['status'],'todo')
        e.board.new_task(p,'collect');task=e.state['tasks'][0]
        e.board.set_skip(p['id'],{'mode':'skip'});self.assertEqual(task['status'],'skipped');self.assertTrue(e.board.view(p)['skipped'])
        e.board.set_skip(p['id'],{'mode':'auto'});self.assertTrue(e.board.view(p)['skipped'])
        e.board.mark(p['id'],{'state':'todo'});self.assertFalse(e.board.view(p)['skipped'])
        self.assertEqual(task['status'],'skipped')  # Restoring does not restart canceled jobs.

    def test_manual_skip_main_device_and_global_disable(self):
        e=self.editor();p=self.product(e,'Shure SM7B')
        e.board.set_skip(p['id'],{'mode':'skip'})
        rules=skip_rules.defaults();rules['enabled']=False;e.board.save_rules({'rules':rules})
        self.assertTrue(e.board.view(p)['skipped'])
        e.board.set_skip(p['id'],{'mode':'auto'});self.assertFalse(e.board.view(p)['skipped'])

    def test_custom_rules_preview_save_and_restart(self):
        e=self.editor();p=self.product(e,'Shure SM7B');before=copy.deepcopy(e.state)
        rules={'enabled':True,'groups':[{'label':'不制作的系列','keywords':['SM7B']}],'exceptions':[]}
        preview=e.board.rules_preview({'rules':rules});self.assertEqual(preview['newly_skipped'],1);self.assertEqual(e.state,before)
        result=e.board.save_rules({'rules':rules});backup=json.loads(Path(result['backup_file']).read_text(encoding='utf-8'))
        self.assertEqual(backup,before);self.assertTrue(e.board.view(p)['skipped'])
        restarted=app.Editor(e.state_file,start_worker=False);self.assertEqual(restarted.board.snapshot({'view':['done']})['total'],1)
        self.assertEqual(restarted.state['settings'],before['settings'])

    def test_saving_rules_cancels_pending_but_retains_history(self):
        e=self.editor();p=self.product(e,'Shure SM7B');e.board.new_task(p,'render')
        old=copy.deepcopy(e.state['tasks'][0]);old.update(id='a'*12,status='completed',output_url='/media/old.mp4',subtitle_url='/media/old.srt')
        e.state['tasks'].insert(0,old);previous=copy.deepcopy(old)
        rule={'enabled':True,'groups':[{'label':'自定义','keywords':['SM7B']}],'exceptions':[]}
        e.board.save_rules({'rules':rule});self.assertEqual(old,previous);self.assertEqual(e.state['tasks'][1]['status'],'skipped')
        with patch.object(e.board,'output_exists',return_value=True):
            self.assertEqual(e.board.view(p)['output_url'],'/media/old.mp4')

    def test_rules_busy_and_write_failure_safety(self):
        e=self.editor();self.product(e,'Shure SM7B');before=copy.deepcopy(e.state);rules=skip_rules.defaults();rules['enabled']=False
        e.worker_busy=True
        with self.assertRaises(ValueError):e.board.save_rules({'rules':rules})
        e.worker_busy=False
        with patch.object(e,'save',side_effect=OSError('disk failure')):
            with self.assertRaises(OSError):e.board.save_rules({'rules':rules})
        self.assertEqual(e.state,before);self.assertEqual(len(e.board.by_id),1)

    def test_single_override_write_failure_preserves_state(self):
        e=self.editor();p=self.product(e,'Shure SM7B');before=copy.deepcopy(e.state)
        with patch.object(e,'save',side_effect=OSError('disk failure')):
            with self.assertRaises(OSError):e.board.set_skip(p['id'],{'mode':'skip'})
        self.assertEqual(e.state,before);self.assertFalse(e.board.view(e.board.product(p['id']))['skipped'])

    def test_http_rules_and_override_origin(self):
        e=self.editor();p=self.product(e,'Roland 收纳包');old_editor,old_port=app.EDITOR,app.PORT
        server=ThreadingHTTPServer(('127.0.0.1',0),app.Handler);app.EDITOR=e;app.PORT=server.server_port
        threading.Thread(target=server.serve_forever,daemon=True).start();base=f'http://127.0.0.1:{app.PORT}'
        def post(path,payload,origin=base):return urlopen(Request(base+path,data=json.dumps(payload).encode(),headers={'Origin':origin,'Content-Type':'application/json'}))
        try:
            with urlopen(base+'/api/skip-rules') as r:self.assertEqual(json.load(r)['hit_count'],1)
            with self.assertRaises(HTTPError) as error:post('/api/skip-rules',{'rules':skip_rules.defaults()},'https://evil.test')
            self.assertEqual(error.exception.code,403)
            with post('/api/skip-rules/preview',{'rules':skip_rules.defaults()}) as r:self.assertEqual(json.load(r)['hit_count'],1)
            with post('/api/products/'+p['id']+'/skip',{'mode':'search'}) as r:self.assertTrue(json.load(r)['ok'])
            self.assertFalse(e.board.view(p)['skipped'])
        finally:server.shutdown();server.server_close();app.EDITOR,app.PORT=old_editor,old_port


if __name__=='__main__':unittest.main()
