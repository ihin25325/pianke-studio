"""Isolated regression checks for the product board; no network or original media edits."""
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
import excel_import
from project_manager import extract_links


class BoardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        folder=app.ROOT/'work'/'board_tests';folder.mkdir(parents=True,exist_ok=True)
        cls.root=Path(tempfile.mkdtemp(prefix='run_',dir=folder))
        cls.workbook=app.ROOT/'副本需要添加首图视频的商品 11-6.xlsx'

    def editor(self): return app.Editor(self.root/self._testMethodName/'queue.json',start_worker=False)

    def test_real_workbook_rules(self):
        if not self.workbook.is_file():self.skipTest('Private legacy workbook is not distributed; synthetic spreadsheet tests still run.')
        e=self.editor();uploaded=e.board.upload(self.workbook.name,self.workbook.read_bytes())
        self.assertEqual(uploaded['rule'],'all');self.assertEqual(uploaded['summary']['source_rows'],818)
        # "All products" and "include hidden rows" are independent choices.
        preview=e.board.analyze({'token':uploaded['token'],'include_hidden':True})
        self.assertEqual(preview['summary']['selected'],818)
        missing=e.board.analyze({'token':preview['token'],'rule':'missing_video','include_hidden':True})
        self.assertEqual(missing['summary']['selected'],328)
        self.assertEqual(len(e.state['tasks']),0)
        result=e.board.commit({'token':preview['token'],'include_hidden':True})
        self.assertEqual(result['added'],818);self.assertEqual(len(e.state['tasks']),0)
        again=e.board.commit({'token':preview['token'],'include_hidden':True});self.assertEqual(again['added'],0)
        self.assertEqual(len(e.state['products']),818)
        self.assertEqual(len(e.state['imports']),1)
        e2=app.Editor(e.state_file,start_worker=False);self.assertEqual(len(e2.state['products']),818)

    def test_migration_group_and_redo_preserves_original(self):
        seed=self.editor();task=seed.parse_line('Neumann U87 Ai | 麦克风 | https://youtu.be/FlT0bAc9Xp4')
        task.update(id='202aa0895ba4',status='completed',output_url='/media/isolated_test.mp4')
        file=self.root/self._testMethodName/'queue.json';file.parent.mkdir(parents=True,exist_ok=True)
        file.write_text(json.dumps({'tasks':[task],'settings':{}},ensure_ascii=False),encoding='utf-8')
        e=app.Editor(file,start_worker=False);pid=e.state['products'][0]['id']
        with patch.object(e.board,'output_exists',return_value=True):
            self.assertEqual(e.board.detail(pid)['product']['status'],'done')
        before=copy.deepcopy(e.state['tasks'][0]);result=e.board.redo(pid,{})
        self.assertNotEqual(result['task_id'],task['id']);self.assertEqual(e.state['tasks'][0],before)
        self.assertEqual(e.state['tasks'][1]['board_product_id'],pid)
        self.assertEqual(e.board.detail(pid)['product']['status'],'working')
        with self.assertRaises(ValueError):e.board.redo(pid,{})
        self.assertTrue(list((file.parent/'backups').glob('*.json')))

    def test_completed_link_import_not_completion(self):
        e=self.editor();data='商品ID,商品名称,已有视频链接\n12345678,Shure SM58,https://video.test/a.mp4\n12345678,Shure SM58,\n'
        p=e.board.upload('test.csv',data.encode());self.assertEqual(p['summary']['duplicates'],1)
        e.board.commit({'token':p['token']});product=e.state['products'][0]
        self.assertEqual(e.board.view(product)['status'],'todo')
        e.board.mark(product['id'],{'state':'done'});self.assertEqual(e.board.view(product)['status'],'done')
        self.assertFalse(e.board.view(product)['output_url'])
        e.board.mark(product['id'],{'state':'todo'});self.assertEqual(e.board.view(product)['status'],'todo')

    def test_existing_completed_association(self):
        e=self.editor()
        e.by_id={'10000001':{'product_id':'10000001','title':'Neumann U87 Ai'}}
        e.add({'text':'10000001'})
        task=e.state['tasks'][0];task.update(status='completed',output_url='/media/example.mp4')
        with patch.object(e.board,'output_exists',return_value=True):
            p=e.board.upload('a.csv','商品ID,商品名称\n10000001,Neumann U87 Ai\n'.encode())
            self.assertEqual(p['summary']['completed'],1)
            result=e.board.commit({'token':p['token']});self.assertEqual(result['added'],0)
            self.assertEqual(len(e.state['products']),1)

    def test_paste_dedup_search_and_pagination(self):
        e=self.editor();r=e.board.paste({'text':'Neumann U87 Ai\nNeumann U87 Ai'})
        self.assertEqual((r['added'],r['duplicates']),(1,1));self.assertFalse(e.state['tasks'])
        for i in range(40):e.board.create(str(12345600+i),'商品 '+str(i))
        d=e.board.snapshot({'view':['all'],'page':['2']});self.assertEqual(len(d['items']),16)
        self.assertEqual(d['counts']['all'],41)
        self.assertEqual(e.board.snapshot({'view':['all'],'q':['U87']})['total'],1)

    def test_links_only_youtube_and_no_duplicates(self):
        text='https://youtu.be/FlT0bAc9Xp4 https://www.youtube.com/watch?v=FlT0bAc9Xp4 https://youtube.com.evil.test/watch?v=FlT0bAc9Xp4 file:///D:/secret'
        self.assertEqual(extract_links(text)['count'],1)
        e=self.editor();e.board.paste({'text':'Neumann U87 Ai'})
        pid=e.state['products'][0]['id'];e.board.set_source(pid,{'url':'https://youtu.be/FlT0bAc9Xp4'})
        self.assertFalse(e.state['tasks'])
        with self.assertRaises(ValueError):e.board.set_source(pid,{'url':'http://127.0.0.1/secret'})

    def test_collect_stops_before_download(self):
        e=self.editor();e.board.paste({'text':'Neumann U87 Ai'})
        product=e.state['products'][0];task=e.board.new_task(product,'collect')
        with patch.object(e,'search',return_value=[{'url':'https://www.youtube.com/watch?v=FlT0bAc9Xp4','score':95,'auto_select':True,'title':'test','channel':'Neumann'}]),patch.object(e,'command') as command:
            e.process(task);command.assert_not_called()
        self.assertEqual(task['status'],'links_ready');self.assertFalse(task.get('output_url'))
        self.assertEqual(e.board.view(product)['status'],'linked')

    def test_download_stops_before_render(self):
        e=self.editor();e.board.paste({'text':'Neumann U87 Ai | 麦克风 | https://youtu.be/FlT0bAc9Xp4'})
        task=e.board.new_task(e.state['products'][0],'download')
        with patch.object(e,'existing_source',return_value=app.ROOT/'sources'/'neumann_u87ai'/'source.mp4'),patch.object(e,'command') as command:
            e.process(task);command.assert_not_called()
        self.assertEqual(task['status'],'downloaded')

    def test_ambiguous_collect_searches_but_requires_confirmation(self):
        e=self.editor();e.board.paste({'text':'Shure SM58 SM57 麦克风'})
        task=e.board.new_task(e.state['products'][0],'collect')
        with patch.object(e,'search',return_value=[]) as search:e.process(task);search.assert_called_once_with(task)
        self.assertEqual(task['status'],'needs_review')

    def test_redo_new_link_clears_old_subtitles(self):
        e=self.editor();e.add({'text':'Neumann U87 Ai | 麦克风 | https://youtu.be/FlT0bAc9Xp4'})
        task=e.state['tasks'][0];task.update(status='failed',subtitle_srt='old',transcription={'status':'ready','srt':'old'})
        pid=task['board_product_id'];original=copy.deepcopy(task)
        e.board.set_source(pid,{'url':'https://youtu.be/cvVtE8pqUCA'});e.board.redo(pid,{})
        new=e.state['tasks'][-1];self.assertEqual(new['selected_url'],'https://www.youtube.com/watch?v=cvVtE8pqUCA')
        self.assertNotIn('subtitle_srt',new);self.assertNotIn('transcription',new);self.assertEqual(task,original)

    def test_mapping_validation_and_no_formula_execution(self):
        table=excel_import.read_table('名称,商品ID\n=HYPERLINK("https://evil.test"),12345678'.encode(),'a.csv')
        prepared=excel_import.prepare(table);self.assertTrue(prepared['records'][0]['title'].startswith('='))
        with self.assertRaises(ValueError):excel_import.prepare(table,{'title':900})
        with self.assertRaises(ValueError):excel_import.read_table(b'bad','a.xlsx')
        with self.assertRaises(ValueError):excel_import.read_table(b'bad','a.xls')

    def test_hidden_rows_are_skipped_and_source_row_numbers_preserved(self):
        table={'sheets':['商品'],'sheet':'商品','rows':[['商品ID','商品名称'],['12345678','隐藏商品'],['23456789','可见商品'],['34567890','隐藏商品二']],
               'row_numbers':[1,12,20,35],'hidden_rows':[12,35]}
        prepared=excel_import.prepare(table)
        self.assertEqual(len(prepared['records']),1);self.assertEqual(prepared['records'][0]['row'],20)
        self.assertEqual((prepared['source_rows'],prepared['hidden_rows'],prepared['hidden_excluded']),(3,2,2))
        all_rows=excel_import.prepare(table,include_hidden=True)
        self.assertEqual([r['row'] for r in all_rows['records']],[12,20,35]);self.assertEqual(all_rows['hidden_excluded'],0)
        with self.assertRaises(ValueError):excel_import.prepare(table,include_hidden='false')

    def test_hidden_blank_rows_do_not_inflate_counts(self):
        table={'sheets':['商品'],'sheet':'商品','rows':[['商品名称'],['可见商品'],[''],['隐藏商品']],
               'row_numbers':[1,2,3,4],'hidden_rows':[3,4]}
        p=excel_import.prepare(table);self.assertEqual((p['source_rows'],p['hidden_rows'],len(p['records'])),(2,1,1))

    def test_csv_visible_default_is_unchanged(self):
        p=excel_import.prepare(excel_import.read_table('商品名称\n商品一\n商品二'.encode(),'a.csv'))
        self.assertEqual(len(p['records']),2);self.assertEqual(p['hidden_excluded'],0)
        self.assertEqual([r['row'] for r in p['records']],[2,3])

    def test_real_filtered_workbook_imports_64_not_818(self):
        file=app.ROOT/'data'/'editor'/'imports'/'31224376e3644f12a91c539b83f31651.xlsx'
        if not file.is_file():self.skipTest('Original filtered regression sample is not available.')
        e=self.editor();preview=e.board.upload('filtered.xlsx',file.read_bytes())
        self.assertEqual(preview['summary']['selected'],64)
        self.assertEqual((preview['summary']['source_rows'],preview['summary']['hidden_excluded']),(818,754))
        self.assertFalse(preview['include_hidden']);self.assertEqual(preview['preview'][0]['source_row'],152)
        full=e.board.analyze({'token':preview['token'],'include_hidden':True})
        self.assertEqual(full['summary']['selected'],818);self.assertEqual(full['summary']['hidden_excluded'],0)
        result=e.board.commit({'token':preview['token']});self.assertEqual(result['added'],64)
        self.assertEqual(e.board.snapshot({'view':['all']})['total'],64);self.assertFalse(e.state['tasks'])
        self.assertEqual(e.board.snapshot({'view':['done']})['counts']['skipped'],11)
        self.assertEqual(e.state['imports'][0]['hidden_excluded'],754)
        # Full-table import is an explicit, different operation, not an idempotent match.
        include=e.board.commit({'token':preview['token'],'include_hidden':True})
        self.assertEqual(include['added'],754);self.assertNotEqual(result['batch_id'],include['batch_id'])
        self.assertEqual(e.board.commit({'token':preview['token']})['added'],0)

    def test_queue_limit_and_invalid_version(self):
        e=self.editor();e.board.paste({'text':'Neumann U87 Ai'});pid=e.state['products'][0]['id']
        with self.assertRaises(ValueError):e.board.redo(pid,{'source_task_id':'0'*12})
        result=e.board.bulk({'ids':[pid,pid],'job':'collect'});self.assertEqual(result['added'],1)
        self.assertEqual(e.board.bulk({'ids':[pid],'job':'collect'})['added'],0)
        with self.assertRaises(ValueError):e.board.bulk({'ids':[pid]*101,'job':'render'})

    def test_http_upload_security_and_board(self):
        e=self.editor();old_editor,old_port=app.EDITOR,app.PORT
        server=ThreadingHTTPServer(('127.0.0.1',0),app.Handler);app.EDITOR=e;app.PORT=server.server_port
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        base=f'http://127.0.0.1:{app.PORT}'
        def upload(origin):
            return urlopen(Request(base+'/api/imports/preview',data='商品名称\n测试商品'.encode(),headers={'Origin':origin,'Content-Type':'application/octet-stream','X-Upload-Filename':'a.csv'}))
        try:
            with self.assertRaises(HTTPError) as error:upload('https://evil.test')
            self.assertEqual(error.exception.code,403)
            with upload(base) as r:self.assertEqual(json.load(r)['summary']['selected'],1)
            with urlopen(Request(base+'/api/imports/recent',data=b'{}',headers={'Origin':base,'Content-Type':'application/json'})) as r:
                self.assertEqual(json.load(r)['summary']['selected'],1)
            with urlopen(base+'/api/board?view=all') as r:self.assertEqual(json.load(r)['counts']['all'],0)
            with urlopen(base+'/') as r:self.assertIn(b'/web/board.js',r.read())
        finally:
            server.shutdown();server.server_close();app.EDITOR,app.PORT=old_editor,old_port

    def test_reset_backs_up_and_preserves_settings(self):
        e=self.editor();e.board.paste({'text':'Neumann U87 Ai\nShure SM58'})
        e.state['imports']=[{'id':'testbatch','filename':'test.xlsx'}]
        e.add({'text':'Neumann U87 Ai'});e.state['tasks'][0]['status']='completed'
        e.state['paused']=True;e.save();before=copy.deepcopy(e.state);style=copy.deepcopy(e.typography)
        preview=e.board.reset_preview();self.assertEqual(preview['counts'],{'products':2,'tasks':1,'imports':1})
        result=e.board.reset({'confirmation':'清空所有清单','revision':preview['revision']})
        self.assertEqual(json.loads(Path(result['backup_file']).read_text(encoding='utf-8')),before)
        self.assertEqual(e.state['settings'],before['settings']);self.assertEqual(e.typography,style)
        self.assertFalse(e.state['paused']);self.assertFalse(e.board.by_id);self.assertFalse(e.board.by_key)
        restarted=app.Editor(e.state_file,start_worker=False)
        self.assertEqual(restarted.board.snapshot({'view':['all']})['counts']['all'],0)
        self.assertFalse(restarted.state['tasks']);self.assertFalse(restarted.state['imports'])

    def test_reset_requires_confirmation_and_fresh_preview(self):
        e=self.editor();e.board.paste({'text':'Neumann U87 Ai'});preview=e.board.reset_preview()
        with self.assertRaises(ValueError):e.board.reset({'confirmation':'yes','revision':preview['revision']})
        e.board.paste({'text':'Shure SM58'})
        with self.assertRaises(ValueError):e.board.reset({'confirmation':'清空所有清单','revision':preview['revision']})
        self.assertEqual(len(e.state['products']),2)

    def test_reset_blocks_running_but_allows_waiting_queue(self):
        e=self.editor();e.add({'text':'Neumann U87 Ai'});task=e.state['tasks'][0]
        for busy,status,speech in [(True,'queued',''),(False,'rendering',''),(False,'completed','transcribing')]:
            e.worker_busy=busy;task['status']=status;task['transcription']={'status':speech}
            preview=e.board.reset_preview();self.assertFalse(preview['can_clear'])
            with self.assertRaises(ValueError):e.board.reset({'confirmation':'清空所有清单','revision':preview['revision']})
        e.worker_busy=False;task['status']='queued';task['transcription']={'status':'queued'};e.state['paused']=True
        preview=e.board.reset_preview();self.assertTrue(preview['can_clear'])
        e.board.reset({'confirmation':'清空所有清单','revision':preview['revision']});self.assertFalse(e.state['tasks'])

    def test_reset_backup_and_save_failures_do_not_lose_state(self):
        e=self.editor();e.board.paste({'text':'Neumann U87 Ai'});e.save();before=copy.deepcopy(e.state)
        payload={'confirmation':'清空所有清单','revision':e.board.reset_preview()['revision']}
        with patch('project_manager.os.fsync',side_effect=OSError('disk failure')):
            with self.assertRaises(OSError):e.board.reset(payload)
        self.assertEqual(e.state,before)
        with patch.object(e,'save',side_effect=OSError('save failure')):
            with self.assertRaises(OSError):e.board.reset(payload)
        self.assertEqual(e.state,before);self.assertEqual(len(e.board.by_id),1)
        self.assertEqual(json.loads(e.state_file.read_text(encoding='utf-8')),before)

    def test_reset_reimport_starts_fresh_and_empty_is_idempotent(self):
        e=self.editor();raw='商品ID,商品名称\n6870009836,Neumann U87 Ai\n'.encode()
        uploaded=e.board.upload('a.csv',raw);e.board.commit({'token':uploaded['token']})
        e.board.mark(e.state['products'][0]['id'],{'state':'done'})
        e.board.reset({'confirmation':'清空所有清单','revision':e.board.reset_preview()['revision']})
        again=e.board.reset({'confirmation':'清空所有清单','revision':e.board.reset_preview()['revision']})
        self.assertNotIn('backup_file',again)
        preview=e.board.analyze({'token':uploaded['token']});self.assertEqual(preview['summary']['new'],1);self.assertEqual(preview['summary']['completed'],0)
        self.assertEqual(e.board.commit({'token':uploaded['token']})['added'],1)
        self.assertEqual(e.board.snapshot({'view':['todo']})['total'],1)

    def test_reset_http_origin_and_confirmation(self):
        e=self.editor();e.board.paste({'text':'Neumann U87 Ai'});old_editor,old_port=app.EDITOR,app.PORT
        server=ThreadingHTTPServer(('127.0.0.1',0),app.Handler);app.EDITOR=e;app.PORT=server.server_port
        threading.Thread(target=server.serve_forever,daemon=True).start();base=f'http://127.0.0.1:{app.PORT}'
        def request(payload,origin=base):
            return urlopen(Request(base+'/api/board/reset',data=json.dumps(payload).encode(),headers={'Origin':origin,'Content-Type':'application/json'}))
        try:
            with urlopen(base+'/api/board/reset-preview') as r:preview=json.load(r)
            with self.assertRaises(HTTPError) as error:request({},'https://evil.test')
            self.assertEqual(error.exception.code,403)
            with self.assertRaises(HTTPError) as error:request({})
            self.assertEqual(error.exception.code,400)
            self.assertEqual(len(e.state['products']),1)
            with request({'confirmation':'清空所有清单','revision':preview['revision']}) as r:self.assertTrue(json.load(r)['ok'])
            with urlopen(base+'/api/board?view=all') as r:self.assertEqual(json.load(r)['counts']['all'],0)
        finally:
            server.shutdown();server.server_close();app.EDITOR,app.PORT=old_editor,old_port


if __name__=='__main__':unittest.main()
