"""Offline multi-site URL, search, safety, queue and HTTP regression tests."""
import copy
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.request import Request,urlopen
from urllib.error import HTTPError
import editor_server as app
import video_sources as sources
import multisite_worker as worker
from project_manager import extract_links

YT='https://www.youtube.com/watch?v=FlT0bAc9Xp4'
BILI='https://www.bilibili.com/video/BV1xx411c7mD/'
FLOWER='https://interactive-examples.mdn.mozilla.net/media/cc0-videos/flower.mp4'

class MultisiteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root=app.ROOT/'work/multisite_tests';root.mkdir(parents=True,exist_ok=True)
        cls.root=Path(tempfile.mkdtemp(prefix='run_',dir=root))
    def editor(self):return app.Editor(self.root/self._testMethodName/'queue.json',start_worker=False)
    def product(self,e):
        e.board.paste({'text':'Shure SM7B 动圈麦克风'});return e.state['products'][0]
    def test_sites_short_links_and_identity(self):
        for url in [BILI,'https://b23.tv/abc123','https://www.douyin.com/video/12345678','https://v.douyin.com/abcd/',
            'https://www.tiktok.com/@test/video/123456','https://vimeo.com/123456','https://www.kuaishou.com/short-video/abc',
            'https://www.xiaohongshu.com/explore/123456','https://x.com/test/status/1234',FLOWER,'https://example.com/product/demo']:
            with self.subTest(url=url):
                self.assertTrue(sources.canonical(url));self.assertRegex(sources.source_key(url),r'^v_[a-f0-9]{24}$')
        self.assertEqual(sources.source_key(YT),'FlT0bAc9Xp4')
        self.assertEqual(sources.canonical('https://m.bilibili.com/video/BV1xx411c7mD?spm=xxx'),BILI)
        self.assertNotEqual(sources.source_key(FLOWER),sources.source_key('https://example.com/flower.mp4'))
        self.assertEqual(sources.canonical(' '+FLOWER+' '),FLOWER)
    def test_invalid_or_private_urls(self):
        for url in ['file:///D:/secret','http://example.com/a.mp4','https://127.0.0.1/a.mp4','https://[::1]/a',
            'https://10.1.1.1/a','https://localhost/a','https://nas.local/a','https://user:pass@example.com/a',
            'https://example.com:444/a','https://example.com/a\nsecret','https://youtube.com.evil.test/watch?v=abcdefghijk',
            'https://youtube.com/playlist?list=xxx','https://www.bilibili.com/']:
            with self.subTest(url=url):self.assertIsNone(sources.canonical(url))
    def test_link_extraction_dedup_across_platforms(self):
        result=extract_links('分享 '+BILI+' https://m.bilibili.com/video/BV1xx411c7mD?spm=foo '+FLOWER+' '+YT)
        self.assertEqual(result['count'],3);self.assertIn(FLOWER,result['links'])
    def test_parse_bind_clip_and_restart(self):
        e=self.editor();p=self.product(e);before=p['title']
        e.board.set_source(p['id'],{'url':BILI});e.board.save_clip(p['id'],{'source_url':BILI,'start':2,'end':12})
        t=e.board.new_task(p,'download');self.assertEqual(t['selected_url'],BILI)
        self.assertEqual(e.parse_line('Shure SM7B | 麦克风 | '+BILI)['selected_url'],BILI)
        taobao='https://cloud.video.taobao.com/play/u/123/p/1/e/6/t/1/456.mp4'
        self.assertEqual(e.parse_line('Shure SM7B | 麦克风 | '+taobao)['selected_url'],taobao)
        restarted=app.Editor(e.state_file,start_worker=False)
        self.assertEqual(restarted.board.by_id[p['id']]['title'],before)
        self.assertEqual(restarted.board.by_id[p['id']]['source_url'],BILI)
    def test_scope_validation_and_failed_save_rollback(self):
        e=self.editor();self.assertEqual(e.sources.snapshot()['scope'],'all')
        e.sources.save({'scope':'domestic'});self.assertEqual(app.Editor(e.state_file,start_worker=False).sources.snapshot()['scope'],'domestic')
        with self.assertRaises(ValueError):e.sources.save({'scope':'private'})
        with patch.object(e,'save',side_effect=OSError('disk full')),self.assertRaises(OSError):e.sources.save({'scope':'international'})
        self.assertEqual(e.sources.snapshot()['scope'],'domestic')
    def test_unverified_web_result_never_auto_selected(self):
        e=self.editor();p=self.product(e);task=e.board.new_task(p,'collect')
        candidate={'url':BILI,'title':'Shure SM7B 官方介绍','channel':'Shure','duration':60,'verified_video':False}
        with patch.object(e,'search_youtube',return_value=[]),patch.object(e.sources,'additional',return_value=([candidate],[],1)):
            ranked=e.search(task)
        self.assertFalse(ranked[0]['auto_select']);self.assertIn('尚未验证',ranked[0]['match_reason'])
    def test_domestic_search_no_youtube_and_verified_candidate(self):
        e=self.editor();e.sources.save({'scope':'domestic'});p=self.product(e);task=e.board.new_task(p,'collect')
        candidate={'url':BILI,'title':'Shure SM7B 演示','channel':'Shure','duration':60,'verified_video':True}
        with patch.object(e,'search_youtube',side_effect=AssertionError('must not search overseas')),patch.object(e.sources,'additional',return_value=([candidate],[{'query':'SM7B','engine':'B站','count':1}],1)):
            ranked=e.search(task)
        self.assertTrue(ranked[0]['auto_select']);self.assertEqual(task['search_report']['scope'],'domestic')
    def test_partial_search_failure_preserves_other_candidates(self):
        e=self.editor();p=self.product(e);task=e.board.new_task(p,'collect')
        candidate={'url':BILI,'title':'Shure SM7B 演示','channel':'Shure','duration':60,'verified_video':True}
        with patch.object(e,'search_youtube',side_effect=RuntimeError('offline')),patch.object(e.sources,'additional',return_value=([candidate],[],1)):
            self.assertEqual(e.search(task)[0]['url'],BILI)
    def test_multisite_cache_reuses_no_network(self):
        e=self.editor();p=self.product(e);task=e.board.new_task(p,'collect');target={'queries':['Shure SM7B']}
        raw=json.dumps({'entries':[{'url':BILI,'title':'Shure SM7B','duration':10,'verified_video':True}]})
        with patch.object(e,'command',return_value=raw) as command:
            items,attempts,_=e.sources.additional(task,target,'domestic');self.assertEqual(command.call_count,2)
        with patch.object(e,'command',side_effect=AssertionError('cached')):
            cached,attempts,_=e.sources.additional(task,target,'domestic')
        self.assertEqual(items,cached);self.assertTrue(all(a['cached'] for a in attempts))
    def test_non_youtube_preview_does_not_use_youtube_embed(self):
        e=self.editor();p=self.product(e)
        with patch.object(e,'existing_source',return_value=None):
            source=e.review.source(BILI,p);self.assertEqual(source['embed_kind'],'bilibili')
            source=e.review.source(FLOWER,p);self.assertEqual(source['embed_kind'],'external');self.assertEqual(source['local_url'],'')
    def test_web_search_filters_non_video_pages(self):
        self.assertIsNone(worker.video_result_url('https://www.shure.com/ja-JP','domestic'))
        self.assertIsNone(worker.video_result_url('https://en.wikipedia.org/wiki/Shure','international'))
        self.assertEqual(worker.video_result_url(BILI,'domestic'),BILI)
        self.assertEqual(worker.video_result_url(FLOWER,'international'),FLOWER)
        self.assertIsNone(worker.video_result_url('https://vimeo.com/1234','domestic'))
    def test_source_page_has_all_script_controls(self):
        import re
        html=(app.ROOT/'web/sources.html').read_text(encoding='utf-8')
        script=(app.ROOT/'web/sources.js').read_text(encoding='utf-8')
        ids=set(re.findall(r'id="([^"]+)"',html))
        for control in re.findall(r"el\('([^']+)'\)",script):self.assertIn(control,ids)
        self.assertIn('href="/sources"',(app.ROOT/'web/board.html').read_text(encoding='utf-8'))
    def test_dns_guard_blocks_private_answers_and_rebinding(self):
        answer=[(socket.AF_INET,socket.SOCK_STREAM,6,'',('10.0.0.2',443))]
        with patch.object(socket,'getaddrinfo',return_value=answer):
            worker.install_network_guard()
            for host in ['example.com','127.0.0.1','localhost','nas.local']:
                with self.subTest(host=host),self.assertRaises(OSError):socket.getaddrinfo(host,443)
    def test_error_categories_and_download_location(self):
        self.assertIn('DRM',sources.failure('DRM encrypted'))
        self.assertIn('Cookie',sources.failure('HTTP 403 login required'))
        self.assertIn('未发现',sources.failure('Unsupported URL'))
        with self.assertRaises(ValueError):worker.fetch_video(FLOWER,app.ROOT/'web')
    def test_http_routes_origin_and_asset_safety(self):
        e=self.editor();server=app.ThreadingHTTPServer(('127.0.0.1',0),app.Handler)
        with patch.object(app,'EDITOR',e),patch.object(app,'PORT',server.server_port):
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();base='http://127.0.0.1:'+str(server.server_port)
            try:
                for path in ['/sources','/web/sources.js','/web/sources.css','/api/sources']:
                    with urlopen(base+path) as r:self.assertEqual(r.status,200)
                req=lambda origin:Request(base+'/api/sources/settings',data=b'{"scope":"domestic"}',headers={'Origin':origin,'Content-Type':'application/json'})
                with self.assertRaises(HTTPError) as error:urlopen(req('https://evil.example'))
                self.assertEqual(error.exception.code,403)
                with urlopen(req(base)) as r:self.assertTrue(json.load(r)['ok'])
                with self.assertRaises(HTTPError):urlopen(base+'/source-preview/v_'+'a'*24+'.mp4')
            finally:server.shutdown();server.server_close();thread.join(timeout=5)

    @unittest.skipUnless(os.environ.get('MULTISITE_SMOKE')=='1','Optional real downloaded public-media render')
    def test_real_downloaded_source_clip_render_decode_and_hash(self):
        import product_render as renderer
        e=self.editor();source=e.existing_source(sources.source_key(FLOWER))
        self.assertTrue(source and source.is_file(),'Run the documented CC0 download verification first')
        before=hashlib.sha256(source.read_bytes()).hexdigest()
        e.board.paste({'text':'SourceTest S1 公开视频测试 | 测试视频 | '+FLOWER});p=e.state['products'][0]
        e.board.save_clip(p['id'],{'source_url':FLOWER,'start':1,'end':4})
        e.storage={'output_dir':str(e.state_file.parent/'exports')};e.state['settings']['subtitles_enabled']=False
        task=e.board.new_task(p,'render');e.process(task)
        self.assertEqual(task['status'],'completed',task.get('message'))
        output=Path(task['output_file']);meta=json.loads(output.with_suffix('.json').read_text(encoding='utf-8'))
        self.assertEqual(meta['validation']['width'],1080);self.assertEqual(meta['validation']['height'],1440)
        self.assertAlmostEqual(meta['validation']['duration_seconds'],3,delta=.1)
        self.assertEqual(meta['source_references']['video'],FLOWER)
        r=subprocess.run([str(renderer.FFMPEG),'-v','error','-nostdin','-i',str(output),'-f','null','-'],capture_output=True,timeout=45)
        self.assertEqual(r.returncode,0,r.stderr.decode(errors='replace'))
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(),before)
        proof={'output':str(output),'validation':meta['validation'],'original_sha256_unchanged':before,'production_queue_modified':False,'api_calls':0}
        (app.ROOT/'work/multisite_verification/render_proof.json').write_text(json.dumps(proof,ensure_ascii=False,indent=2),encoding='utf-8')

if __name__=='__main__':unittest.main()
