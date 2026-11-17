"""Isolated template, Unicode naming, native attachments and real ZIP checks."""
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import unquote
from urllib.request import Request,urlopen
import uuid
import zipfile

import editor_server as app
import batch_template
import export_naming

URL='https://www.youtube.com/watch?v=FlT0bAc9Xp4'


class BatchDownloadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root=app.ROOT/'work'/'batch_download_tests';root.mkdir(parents=True,exist_ok=True)
        cls.folder=Path(tempfile.mkdtemp(prefix='run_',dir=root))

    def setUp(self):
        self.root=self.folder/self._testMethodName
        self.e=app.Editor(self.root/'queue.json',start_worker=False)
        self.e.board.paste({'text':'Neumann U87 Ai | 录音电容麦克风 | '+URL})
        self.p=self.e.state['products'][0]

    def completed(self,product=None,media=None):
        e=self.e;p=product or self.p
        t=e.board.new_task(p,'render');file=self.root/(t['id']+'.mp4')
        if media is None:file.write_bytes(b'fake test video'*200)
        else:
            # Do not copy or modify originals: registry points to the read-only sample.
            file=media
        t.update(status='completed',output_file=str(file.resolve()),
                 output_url='/media/task/'+t['id']+'/'+app.quote(file.name))
        e.save();return t,file

    def package(self,ids=None):
        r=self.e.downloads.prepare({'ids':ids or [self.p['id']]});jid=r['job']['id']
        deadline=time.monotonic()+15
        while time.monotonic()<deadline:
            job=next(j for j in self.e.state['export_batches'] if j['id']==jid)
            if job['status']!='building':break
            time.sleep(.01)
        self.assertEqual(job['status'],'ready',job['message']);return job

    def test_template_parsing_validation_compatibility(self):
        r=batch_template.normalize({'clip_start_seconds':'00:05','clip_end_seconds':'01:20'})
        self.assertEqual(r,{'clip_start_seconds':5,'clip_end_seconds':80,'max_duration_seconds':75})
        self.assertEqual(batch_template.normalize({'clip_start_seconds':12,'clip_end_seconds':''})['max_duration_seconds'],90)
        self.assertEqual(batch_template.normalize({'max_duration_seconds':20})['clip_end_seconds'],20)
        for a,b in [(1,1),(5,3),(0,91),(True,20),('1:60',150),(0,float('nan')),(-1,5)]:
            with self.subTest(a=a,b=b),self.assertRaises(ValueError):batch_template.normalize({'clip_start_seconds':a,'clip_end_seconds':b})

    def test_settings_persistence_new_tasks_individual_priority_redo(self):
        e=self.e;e.save_settings({'clip_start_seconds':5,'clip_end_seconds':7})
        t=e.board.new_task(self.p,'render');self.assertEqual(t['batch_clip'],{'start':5,'end':7})
        e.save_settings({'clip_start_seconds':15,'clip_end_seconds':25})
        self.assertEqual(t['batch_clip'],{'start':5,'end':7});t['status']='failed'
        e.board.redo(self.p['id'],{});redo=e.state['tasks'][-1];self.assertEqual(redo['batch_clip'],t['batch_clip'])
        redo['status']='failed';e.board.save_clip(self.p['id'],{'source_url':URL,'start':2,'end':3})
        new=e.board.new_task(self.p,'render');self.assertEqual(new['clip']['start'],2);self.assertEqual(new['max_duration_seconds'],1)
        e.save();restarted=app.Editor(e.state_file,start_worker=False)
        self.assertEqual(restarted.state['settings']['clip_start_seconds'],15)
        self.assertEqual(restarted.review.snapshot(restarted.state['products'][0])['batch_clip'],{'start':15,'end':25})

    def test_invalid_settings_write_rolls_back(self):
        before=copy.deepcopy(self.e.state['settings'])
        with self.assertRaises(ValueError):self.e.save_settings({'clip_start_seconds':20,'clip_end_seconds':10})
        self.assertEqual(self.e.state['settings'],before)
        with patch.object(self.e,'save',side_effect=OSError('disk full')),self.assertRaises(OSError):
            self.e.save_settings({'clip_start_seconds':5,'clip_end_seconds':10})
        self.assertEqual(self.e.state['settings'],before)

    def test_unicode_names_reserved_words_duplicates(self):
        task=self.e.parse_line('Neumann U87 Ai 录音电容麦克风')
        self.assertEqual(export_naming.product_name(task),'Neumann_U87 Ai_录音电容麦克风')
        self.assertEqual(export_naming.safe_name('CON'),'_CON')
        self.assertNotRegex(export_naming.safe_name('../A:B\\C?商品'),'[/:\\\\?]')
        used=set();self.assertEqual(export_naming.unique_filename('测试',used),'测试.mp4')
        self.assertEqual(export_naming.unique_filename('测试',used),'测试 (2).mp4')
        self.assertEqual(export_naming.product_name(self.e.parse_line('Xvive U2吉他无线接收器蓝牙乐器连接线贝斯电吉他蓝牙音频传输')),'Xvive_U2_乐器无线收发器')
        self.assertEqual(export_naming.product_name(self.e.parse_line('M-Live B.BEAT X多轨音视频播放器')),'M-Live_B.Beat X_音视频播放器')

    def test_full_100_item_zip_deduplicates_filenames(self):
        products=[self.p]
        for n in range(99):products.append(self.e.board.create(str(12000000+n),'Neumann U87 Ai 商品 '+str(n),'',source_url=URL))
        for product in products:self.completed(product)
        job=self.package([p['id'] for p in products]);package=self.e.downloads.delivery(job['id'])[0]
        with zipfile.ZipFile(package) as z:
            videos=[n for n in z.namelist() if n.endswith('.mp4')]
            self.assertEqual(len(videos),100);self.assertEqual(len(set(videos)),100)
            self.assertIn('Neumann_U87 Ai_录音电容麦克风 (100).mp4',videos)
            self.assertIsNone(z.testzip())

    def test_zip_failure_does_not_count_or_deliver(self):
        self.completed()
        with patch('export_downloads.zipfile.ZipFile',side_effect=OSError('test disk failure')):
            r=self.e.downloads.prepare({'ids':[self.p['id']]})
            deadline=time.monotonic()+3
            while self.e.downloads.busy and time.monotonic()<deadline:time.sleep(.01)
        job=self.e.state['export_batches'][-1];self.assertEqual(job['status'],'failed')
        self.assertFalse(self.e.state['export_downloads'])
        with self.assertRaises(ValueError):self.e.downloads.delivery(r['job']['id'])

    def test_zip_uses_latest_and_only_actual_files_keeps_original(self):
        t,old=self.completed();new,newfile=self.completed();before=newfile.read_bytes()
        self.e.board.paste({'text':'Shure SM7B 麦克风支架'})
        accessory=self.e.state['products'][-1]
        job=self.package([self.p['id'],self.p['id'],accessory['id'],'000000000000'])
        self.assertEqual(len(job['items']),1);self.assertEqual(job['items'][0]['task_id'],new['id']);self.assertEqual(len(job['skipped']),2)
        package=self.e.downloads.delivery(job['id'])[0]
        with zipfile.ZipFile(package) as z:
            files=[n for n in z.namelist() if n.endswith('.mp4')]
            self.assertEqual(files,['Neumann_U87 Ai_录音电容麦克风.mp4']);self.assertEqual(z.read(files[0]),before)
            self.assertIsNone(z.testzip())
        self.assertEqual(newfile.read_bytes(),before);self.assertTrue(old.is_file());self.assertFalse(self.e.state['export_downloads'])

    def test_empty_missing_tickets_and_limits(self):
        for ids in [[],['x']*101,[1],None]:
            with self.assertRaises(ValueError):self.e.downloads.prepare({'ids':ids})
        with self.assertRaises(ValueError):self.e.downloads.prepare({'ids':[self.p['id']]})
        for token in ['../queue.json','0'*32]:
            with self.assertRaises(ValueError):self.e.downloads.delivery(token)
        t,file=self.completed();ticket=self.e.downloads.video_ticket({'task_id':t['id']});file.unlink()
        with self.assertRaises(ValueError):self.e.downloads.delivery(ticket['download_url'].split('/')[-1])

    def test_records_version_specific_dedup_restart_and_search(self):
        t,_=self.completed();item,_=self.e.downloads.task_item(t['id'])
        self.e.downloads.record_sent([item],'video','token');self.e.downloads.record_sent([item],'video','token')
        self.assertEqual(len(self.e.state['export_downloads']),1);self.assertEqual(self.e.state['export_downloads'][0]['count'],2)
        new,_=self.completed();self.assertNotEqual(t['id'],new['id'])
        restarted=app.Editor(self.e.state_file,start_worker=False);records=restarted.downloads.snapshot()
        self.assertEqual(records['items'][0]['task_id'],t['id']);self.assertTrue(records['items'][0]['available'])
        self.assertEqual(restarted.downloads.snapshot({'q':['missing']})['total'],0)
        self.assertEqual(restarted.board.snapshot({})['download_count'],1)

    def test_interrupted_build_missing_zip_and_no_disk_space(self):
        t,_=self.completed()
        with patch('export_downloads.shutil.disk_usage',return_value=type('Disk',(),{'free':0})()),self.assertRaises(ValueError):self.e.downloads.prepare({'ids':[self.p['id']]})
        self.e.state['export_batches'].append({'id':uuid.uuid4().hex,'status':'building','items':[]});self.e.save()
        restarted=app.Editor(self.e.state_file,start_worker=False)
        self.assertEqual(restarted.state['export_batches'][0]['status'],'failed')
        self.e=restarted;self.p=restarted.state['products'][0]
        job=self.package();(self.e.downloads.folder/(job['id']+'.zip')).unlink()
        self.assertEqual(self.e.downloads.snapshot()['batches'][0]['status'],'missing')

    def test_http_attachment_names_media_not_counted_zip_records_after_send(self):
        t,file=self.completed();job=self.package()
        server=app.ThreadingHTTPServer(('127.0.0.1',0),app.Handler)
        with patch.object(app,'EDITOR',self.e),patch.object(app,'PORT',server.server_port):
            threading.Thread(target=server.serve_forever,daemon=True).start();base=f'http://127.0.0.1:{server.server_port}'
            def post(path,value,origin=None):return urlopen(Request(base+path,json.dumps(value).encode(),{'Content-Type':'application/json','Origin':origin or base}))
            try:
                with urlopen(base+t['output_url']) as r:r.read()
                self.assertEqual(self.e.downloads.snapshot()['count'],0)
                with self.assertRaises(HTTPError) as exc:post('/api/exports/video',{'task_id':t['id']},'https://evil.test')
                self.assertEqual(exc.exception.code,403)
                with post('/api/exports/video',{'task_id':t['id']}) as r:ticket=json.load(r)
                with urlopen(base+ticket['download_url']) as r:
                    self.assertTrue(unquote(r.headers['Content-Disposition']).endswith('Neumann_U87 Ai_录音电容麦克风.mp4'))
                    self.assertEqual(r.read(),file.read_bytes())
                # Wait only for the handler's state flush, not for any browser save.
                deadline=time.monotonic()+3
                while not self.e.state['export_downloads'] and time.monotonic()<deadline:time.sleep(.01)
                self.assertEqual(self.e.downloads.snapshot()['count'],1)
                with urlopen(base+'/export-download/'+job['id']) as r:
                    raw=r.read();self.assertEqual(r.headers['Content-Type'],'application/zip')
                with zipfile.ZipFile(io.BytesIO(raw)) as z:self.assertIsNone(z.testzip())
                deadline=time.monotonic()+3
                while not job.get('sent_at') and time.monotonic()<deadline:time.sleep(.01)
                self.assertTrue(job['sent_at']);self.assertEqual(self.e.state['export_downloads'][0]['count'],2)
                with self.assertRaises(HTTPError) as exc:urlopen(Request(base+ticket['download_url'],headers={'Range':'bytes=0-100'}))
                self.assertEqual(exc.exception.code,416)
            finally:server.shutdown();server.server_close()

    @unittest.skipUnless(os.environ.get('BATCH_EXPORT_SMOKE')=='1','optional real local-media batch template export + ZIP')
    def test_real_batch_cut_named_file_zip_and_hashes(self):
        original=app.ROOT/'sources'/'neumann_u87ai'/'source.mp4';stat=original.stat()
        self.e.storage={'output_dir':str(self.root/'成片')}
        self.e.save_settings({'clip_start_seconds':'00:05','clip_end_seconds':'00:07','subtitles':{'enabled':False}})
        task=self.e.board.new_task(self.p,'render')
        with patch.object(self.e,'existing_source',return_value=original):self.e.process(task)
        output=Path(task['output_file']);self.assertTrue(output.name.startswith('Neumann_U87 Ai_录音电容麦克风_'))
        meta=json.loads(output.with_suffix('.json').read_text(encoding='utf-8'))
        self.assertEqual(meta['segments_seconds'],[{'start':5,'end':7}]);self.assertEqual(meta['validation']['duration_seconds'],2)
        job=self.package();package=self.e.downloads.delivery(job['id'])[0]
        with zipfile.ZipFile(package) as z:
            video=z.read('Neumann_U87 Ai_录音电容麦克风.mp4')
            self.assertEqual(hashlib.sha256(video).hexdigest(),hashlib.sha256(output.read_bytes()).hexdigest())
        self.assertEqual((original.stat().st_size,original.stat().st_mtime_ns),(stat.st_size,stat.st_mtime_ns))
        proof={'output':str(output),'zip':str(package),'segments':meta['segments_seconds'],'validation':meta['validation'],
               'zip_video_sha256':hashlib.sha256(video).hexdigest(),'original_unchanged':True}
        (self.folder/'real_batch_proof.json').write_text(json.dumps(proof,ensure_ascii=False,indent=2),encoding='utf-8')
        print('BATCH_EXPORT_PROOF '+json.dumps(proof,ensure_ascii=False))


if __name__=='__main__':unittest.main(verbosity=2)
