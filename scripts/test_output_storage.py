"""Isolated output-folder and serving tests; optional real local FFmpeg export."""
import copy
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.request import Request,urlopen
from urllib.error import HTTPError

import editor_server as app
import output_storage as storage
import product_render as renderer


class OutputStorageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        folder=app.ROOT/'work'/'storage_tests';folder.mkdir(parents=True,exist_ok=True)
        cls.folder=Path(tempfile.mkdtemp(prefix='run_',dir=folder))

    def setUp(self):
        self.root=self.folder/self._testMethodName;self.root.mkdir()
        self.settings=self.root/'storage.json'
        self.patcher=patch.object(storage,'SETTINGS_FILE',self.settings);self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.e=app.Editor(self.root/'queue.json',start_worker=False)

    def test_default_and_absolute_path_validation(self):
        self.assertEqual(self.e.storage['output_dir'],str(storage.DEFAULT_DIR.resolve()))
        for value in [None,'','outputs','D:relative',r'\\server\share','D:/','D:/../x',str(app.ROOT),
                      str(app.ROOT/'configs'/'exports'),str(app.ROOT/'sources'/'exports'),r'C:\Windows\exports',
                      r'D:\CON',r'D:\bad?.folder','D:/a\nb']:
            with self.subTest(value=value),self.assertRaises(ValueError):storage.normalize_directory(value)
        path=self.root/'中文 成片'
        self.assertEqual(storage.normalize_directory('"'+str(path)+'"'),path.resolve())

    def test_save_restart_keeps_queue_and_probe_leaves_no_file(self):
        self.e.add({'text':'Shure SM7B'})
        before=copy.deepcopy(self.e.state)
        path=self.root/'中文 成片'
        saved=self.e.save_storage({'output_dir':str(path)})
        self.assertEqual(saved['output_dir'],str(path.resolve()))
        self.assertTrue(path.is_dir());self.assertFalse(list(path.iterdir()))
        self.assertEqual(before,self.e.state)
        restarted=app.Editor(self.e.state_file,start_worker=False)
        self.assertEqual(restarted.storage,self.e.storage)

    def test_busy_and_failed_save_preserve_preferences(self):
        original=copy.deepcopy(self.e.storage)
        self.e.worker_busy=True
        with self.assertRaises(ValueError):self.e.save_storage({'output_dir':str(self.root/'busy')})
        self.assertFalse((self.root/'busy').exists())
        self.e.worker_busy=False
        with patch.object(storage,'prepare_directory',side_effect=ValueError('no access')),self.assertRaises(ValueError):
            self.e.save_storage({'output_dir':str(self.root/'blocked')})
        self.assertEqual(self.e.storage,original)
        with patch.object(storage.os,'replace',side_effect=OSError('disk full')),self.assertRaises(OSError):
            self.e.save_storage({'output_dir':str(self.root/'full')})
        self.assertEqual(self.e.storage,original);self.assertFalse(self.settings.exists())
        self.assertFalse(list(self.root.glob('*.tmp')))

    def register(self):
        self.e.add({'text':'Shure SM7B'})
        task=self.e.state['tasks'][-1]
        output=self.root/'custom exports'/'中文 成片.mp4';output.parent.mkdir()
        output.write_bytes(b'x'*2048);output.with_suffix('.json').write_text('{}',encoding='utf-8')
        output.with_suffix('.srt').write_text('subtitle',encoding='utf-8')
        prefix='/media/task/'+task['id']+'/'
        urls={key:prefix+app.quote(file.name) for key,file in [('output_url',output),('metadata_url',output.with_suffix('.json')),('subtitle_url',output.with_suffix('.srt'))]}
        task.update(status='completed',output_file=str(output),**urls);self.e.save()
        return task,output

    def test_custom_history_resolves_after_path_change_and_restart(self):
        task,output=self.register()
        before=copy.deepcopy(task)
        self.e.save_storage({'output_dir':str(self.root/'next exports')})
        restarted=app.Editor(self.e.state_file,start_worker=False)
        self.assertEqual(restarted.resolve_media(task['output_url']),output)
        self.assertTrue(restarted.board.output_exists(task))
        self.assertEqual(restarted.board.view(restarted.state['products'][0])['status'],'done')
        self.assertEqual(restarted.state['tasks'][0],before)
        self.assertEqual(output.read_bytes(),b'x'*2048)

    def test_only_registered_files_are_served(self):
        task,output=self.register()
        (output.parent/'private.json').write_text('private',encoding='utf-8')
        for value in ['/media/task/'+task['id']+'/private.json',
                      '/media/task/'+task['id']+'/..%2Fqueue.json',
                      '/media/task/ffffffffffff/'+app.quote(output.name),
                      '/media/..%2Fdata%2Fproducts.json','/media/task/'+task['id']+'/'+app.quote(output.name)+'.log']:
            with self.subTest(value=value):self.assertIsNone(self.e.resolve_media(value))
        output.unlink();self.assertIsNone(self.e.resolve_media(task['output_url']))

    def test_http_custom_media_range_and_storage_origin(self):
        task,output=self.register()
        server=app.ThreadingHTTPServer(('127.0.0.1',0),app.Handler)
        with patch.object(app,'EDITOR',self.e,create=True),patch.object(app,'PORT',server.server_port):
            threading.Thread(target=server.serve_forever,daemon=True).start()
            base=f'http://127.0.0.1:{server.server_port}'
            try:
                with urlopen(base+'/api/storage') as response:self.assertTrue(json.load(response)['can_save'])
                request=Request(base+task['output_url'],headers={'Range':'bytes=20-49'})
                with urlopen(request) as response:
                    self.assertEqual(response.status,206);self.assertEqual(len(response.read()),30)
                    self.assertIn('filename*=',response.headers['Content-Disposition'])
                payload=json.dumps({'output_dir':str(self.root/'via http')}).encode()
                for origin,success in [('https://untrusted.test',False),(base,True)]:
                    request=Request(base+'/api/storage',data=payload,headers={'Content-Type':'application/json','Origin':origin})
                    if success:
                        with urlopen(request) as response:self.assertTrue(json.load(response)['ok'])
                    else:
                        with self.assertRaises(HTTPError) as error:urlopen(request)
                        self.assertEqual(error.exception.code,403)
                with urlopen(base+task['output_url']) as response:self.assertEqual(response.read(),b'x'*2048)
            finally:server.shutdown();server.server_close()

    @unittest.skipUnless(os.environ.get('STORAGE_SMOKE')=='1','optional real FFmpeg export')
    def test_real_workflow_custom_export_then_play_after_restart(self):
        e=self.e
        export_dir=self.root/'真实测试 新成片'
        e.save_storage({'output_dir':str(export_dir)})
        e.add({'text':'Neumann U87 Ai | 录音电容麦克风 | https://youtu.be/FlT0bAc9Xp4'})
        task=e.state['tasks'][-1];task['max_duration_seconds']=2;task['subtitles_enabled']=True
        task['subtitle_srt']='1\n00:00:00,000 --> 00:00:02,000\n自定义保存路径测试\n'
        with (patch.object(e,'search',side_effect=AssertionError('No search during storage test')),
              patch.object(e,'recognize',side_effect=AssertionError('No ASR during storage test')),
              patch.object(e,'existing_source',return_value=app.ROOT/'sources'/'neumann_u87ai'/'source.mp4')):
            e.process(task)
        self.assertEqual(task['status'],'completed')
        output=Path(task['output_file'])
        self.assertEqual(output.parent,export_dir.resolve())
        for suffix in ('.mp4','.json','.log','.srt'):self.assertTrue(output.with_suffix(suffix).is_file())
        self.assertFalse(list(export_dir.glob('*.partial.mp4')))
        self.assertEqual(renderer.validate_output(output,2,True)['duration_seconds'],2)
        e.save_storage({'output_dir':str(self.root/'下一批目录')})
        restarted=app.Editor(e.state_file,start_worker=False)
        self.assertEqual(restarted.resolve_media(task['output_url']),output)
        proof={'output':str(output),'playback_url':task['output_url'],'state_file':str(e.state_file),
               'stored_next_output_dir':restarted.storage['output_dir'],'duration_seconds':2}
        (self.root/'proof.json').write_text(json.dumps(proof,ensure_ascii=False,indent=2),encoding='utf-8')
        print('STORAGE_PROOF '+json.dumps(proof,ensure_ascii=False))


if __name__=='__main__':unittest.main(verbosity=2)
