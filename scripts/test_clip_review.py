"""Offline rough-cut, preview and exact-item photo checks; optional real cut export."""
import copy
import io
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
import clip_edit
import product_images as photos
import output_storage
import review_tools
from PIL import Image

URL='https://www.youtube.com/watch?v=FlT0bAc9Xp4'
PHOTO='https://img.alicdn.com/imgextra/i1/example.jpg'


class ClipReviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        folder=app.ROOT/'work'/'clip_review_tests';folder.mkdir(parents=True,exist_ok=True)
        cls.folder=Path(tempfile.mkdtemp(prefix='run_',dir=folder))

    def editor(self):
        root=self.folder/self._testMethodName;root.mkdir(parents=True,exist_ok=True)
        e=app.Editor(root/'queue.json',start_worker=False)
        e.board.paste({'text':'Neumann U87 Ai | 录音电容麦克风 | '+URL})
        return e,e.state['products'][0]

    def clip(self,start=5,end=15,url=URL):return {'source_url':url,'start':start,'end':end}

    def test_time_inputs_and_float_boundaries(self):
        for text,result in [('30',30),('01:30.5',90.5),('01:00:01.123',3601.123),('0',0)]:
            self.assertEqual(clip_edit.seconds(text),result)
        for value in [True,-1,float('nan'),'1:60','1:1:60','abc','00:-01','1e2',21601]:
            with self.subTest(value=value),self.assertRaises(ValueError):clip_edit.seconds(value)
        self.assertEqual(clip_edit.normalize(self.clip(10.1,10.2),app.youtube_url)['end'],10.2)
        for start,end in [(5,5),(5,4),(0,90.1),(0,.05)]:
            with self.subTest(start=start,end=end),self.assertRaises(ValueError):clip_edit.normalize(self.clip(start,end),app.youtube_url)
        self.assertIsNone(clip_edit.normalize(self.clip(4,''),app.youtube_url)['end'])

    def test_save_clip_bound_to_source_and_restart(self):
        e,p=self.editor();e.board.save_clip(p['id'],self.clip('00:05','00:15'))
        restarted=app.Editor(e.state_file,start_worker=False)
        self.assertEqual(restarted.state['products'][0]['clip'],self.clip())
        t=restarted.board.new_task(restarted.state['products'][0],'render')
        self.assertEqual(t['clip'],self.clip());self.assertEqual(t['max_duration_seconds'],10)
        t['status']='completed'
        with self.assertRaises(ValueError):restarted.board.save_clip(p['id'],self.clip(url='https://www.youtube.com/watch?v=abcdefghijk'))
        restarted.board.set_source(p['id'],{'url':'https://www.youtube.com/watch?v=abcdefghijk'})
        self.assertNotIn('clip',restarted.state['products'][0])

    def test_clip_range_checks_real_duration_and_busy(self):
        e,p=self.editor()
        with patch.object(e.review,'source',return_value={'duration':12}),self.assertRaises(ValueError):e.board.save_clip(p['id'],self.clip(5,15))
        t=e.board.new_task(p,'collect')
        with self.assertRaises(ValueError):e.board.save_clip(p['id'],self.clip())
        with self.assertRaises(ValueError):e.board.generate(p['id'],self.clip())
        self.assertEqual(t['status'],'queued')

    def test_generate_creates_new_version_and_invalidates_only_stale_transcript(self):
        e,p=self.editor();t=e.board.new_task(p,'render');t.update(status='completed',output_url='/media/old.mp4',
            typography={'title_scale':1.3},subtitle_srt='old timing',transcription={'status':'ready','srt':'old timing'})
        before=copy.deepcopy(t)
        r=e.board.generate(p['id'],self.clip())
        new=e.state['tasks'][-1]
        self.assertEqual(t,before);self.assertNotEqual(t['id'],r['task_id'])
        self.assertEqual(new['typography'],before['typography']);self.assertEqual(new['clip'],self.clip())
        self.assertNotIn('subtitle_srt',new);self.assertNotIn('transcription',new)
        new.update(status='completed',subtitle_srt='correct timing',transcription={'status':'ready'})
        e.board.generate(p['id'],self.clip())
        self.assertEqual(e.state['tasks'][-1]['subtitle_srt'],'correct timing')

    def test_generate_save_failure_keeps_other_worker_task_references(self):
        e,p=self.editor();t=e.board.new_task(p,'collect');t['status']='needs_review';before=copy.deepcopy(e.state)
        with patch.object(e,'save',side_effect=OSError('disk full')),self.assertRaises(OSError):e.board.generate(p['id'],self.clip())
        self.assertEqual(e.state,before);self.assertIs(e.state['tasks'][0],t);self.assertIs(e.board.product(p['id']),p)

    def test_photo_hosts_and_exact_item_metadata(self):
        for value in ['http://img.alicdn.com/a.jpg','https://127.0.0.1/a.jpg','https://img.alicdn.com.evil.test/a.jpg',
                      'https://user:pass@img.alicdn.com/a.jpg','https://img.alicdn.com/a.svg','https://img.alicdn.com:444/a.jpg']:
            self.assertIsNone(photos.image_url(value))
        html='<link rel="canonical" href="https://item.taobao.com/item.htm?id=12345678"><meta property="og:image" content="'+PHOTO+'">'
        self.assertEqual(photos.parse_photo(html,'12345678'),PHOTO)
        self.assertIsNone(photos.parse_photo(html,'87654321'))
        self.assertIsNone(photos.parse_photo('<title>登录 - 淘宝</title>'+html,'12345678'))
        structured='<script type="application/ld+json">'+json.dumps({'@type':'Product','sku':'12345678','image':PHOTO})+'</script>'
        self.assertEqual(photos.parse_photo(structured,'12345678'),PHOTO)
        self.assertIsNone(photos.parse_photo(structured,'99999999'))
        with self.assertRaises(ValueError):photos.fetch_photo('https://127.0.0.1/','12345678')

    def test_photo_failure_never_substitutes_other_product_and_caches(self):
        e,p=self.editor()
        with patch.object(photos,'fetch_photo',side_effect=ValueError('需要登录')) as fetch:
            result=e.review.fetch_image(p['id'],{})
            self.assertEqual(result['image']['status'],'unavailable');self.assertFalse(result['image']['url'])
            e.review.fetch_image(p['id'],{});self.assertEqual(fetch.call_count,1)
        e.review.set_image_url(p['id'],{'url':PHOTO})
        with patch.object(photos,'fetch_photo',side_effect=ValueError('needs login')):
            result=e.review.fetch_image(p['id'],{'force':True})
        self.assertEqual(result['image']['url'],PHOTO)

    def test_photo_upload_and_manual_update_wins_network_race(self):
        e,p=self.editor();raw=io.BytesIO();Image.new('RGB',(16,16),'white').save(raw,format='PNG')
        result=e.review.upload_image(p['id'],raw.getvalue())
        self.assertTrue(result['image']['url'].startswith('/product-image/'+p['id']+'/'))
        with self.assertRaises(ValueError):e.review.upload_image(p['id'],b'<svg><script>bad</script></svg>')
        def fetch(*args):
            e.review.set_image_url(p['id'],{'url':PHOTO})
            return 'https://img.alicdn.com/other.jpg'
        with patch.object(photos,'fetch_photo',side_effect=fetch):result=e.review.fetch_image(p['id'],{'force':True})
        self.assertEqual(result['image']['url'],PHOTO)

    def test_preview_metadata_never_downloads_video(self):
        e,p=self.editor()
        with patch.object(e,'existing_source',return_value=None),patch.object(e,'command') as command:
            result=e.review.snapshot(p);command.assert_not_called()
        self.assertEqual(result['source']['url'],URL)
        self.assertFalse(result['source']['local_url'])
        self.assertIn('review',e.board.detail(p['id']))

    def test_http_preview_clip_image_security(self):
        e,p=self.editor();server=app.ThreadingHTTPServer(('127.0.0.1',0),app.Handler)
        with patch.object(app,'EDITOR',e,create=True),patch.object(app,'PORT',server.server_port):
            threading.Thread(target=server.serve_forever,daemon=True).start();base=f'http://127.0.0.1:{server.server_port}'
            try:
                with urlopen(base+'/api/source-review?url='+app.quote(URL,safe='')) as r:self.assertEqual(json.load(r)['source']['url'],URL)
                for path in ['/api/source-review?url=http://127.0.0.1/','/source-preview/..%2Fsecret.mp4','/product-image/..%2Fqueue.json']:
                    with self.assertRaises(HTTPError):urlopen(base+path)
                with urlopen(base+'/') as r:
                    self.assertIn('frame-src https://www.youtube.com',r.headers['Content-Security-Policy'])
                    self.assertEqual(r.headers['Referrer-Policy'],'strict-origin-when-cross-origin')
                request=Request(base+'/api/products/'+p['id']+'/clip',data=json.dumps(self.clip()).encode(),headers={'Content-Type':'application/json','Origin':base})
                with urlopen(request) as r:self.assertTrue(json.load(r)['ok'])
                request=Request(base+'/api/products/'+p['id']+'/generate',data=json.dumps(self.clip()).encode(),headers={'Content-Type':'application/json','Origin':'https://evil.test'})
                with self.assertRaises(HTTPError) as failure:urlopen(request)
                self.assertEqual(failure.exception.code,403)
            finally:server.shutdown();server.server_close()

    @unittest.skipUnless(os.environ.get('CLIP_REVIEW_SMOKE')=='1','optional real local-media cut')
    def test_real_start_end_cut_and_original_preservation(self):
        e,p=self.editor();original=app.ROOT/'sources'/'neumann_u87ai'/'source.mp4';stat=original.stat()
        e.storage={'output_dir':str(self.folder/'real_cut_exports')};e.state['settings']['subtitles_enabled']=False
        with patch.object(e,'existing_source',return_value=original),patch.object(e,'recognize',side_effect=AssertionError('No ASR in crop smoke')):
            e.board.generate(p['id'],self.clip('00:05','00:07'))
            task=e.state['tasks'][-1];e.process(task)
        output=Path(task['output_file']);metadata=json.loads(output.with_suffix('.json').read_text(encoding='utf-8'))
        self.assertEqual(metadata['segments_seconds'],[{'start':5.0,'end':7.0}])
        self.assertEqual(metadata['validation']['duration_seconds'],2)
        self.assertEqual(metadata['ending_effect']['duration_seconds'],1)
        self.assertEqual((original.stat().st_size,original.stat().st_mtime_ns),(stat.st_size,stat.st_mtime_ns))
        proof={'output':str(output),'segments':metadata['segments_seconds'],'validation':metadata['validation'],
               'state_file':str(e.state_file),'product_id':p['id']}
        (self.folder/'real_cut_proof.json').write_text(json.dumps(proof,ensure_ascii=False,indent=2),encoding='utf-8')
        print('CLIP_REVIEW_PROOF '+json.dumps(proof,ensure_ascii=False))


if __name__=='__main__':unittest.main(verbosity=2)
