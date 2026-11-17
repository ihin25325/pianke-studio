"""Brand library safety, matching and local real-media smoke tests."""
import io
import hashlib
import json
import os
from pathlib import Path
import tempfile
import subprocess
import threading
import unittest
from unittest.mock import patch
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from PIL import Image,ImageDraw
import brand_logos as logos
import editor_server as app
import product_render as renderer


class BrandLogoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root=app.ROOT/'work/brand_logo_tests';root.mkdir(parents=True,exist_ok=True)
        cls.root=Path(tempfile.mkdtemp(prefix='run_',dir=root))

    def editor(self):return app.Editor(self.root/self._testMethodName/'queue.json',start_worker=False)

    def test_relative_state_paths_are_resolved(self):
        from types import SimpleNamespace
        library=logos.BrandLogos(SimpleNamespace(state_file=Path('work/brand_logo_verification/relative/queue.json')))
        self.assertTrue(library.folder.is_absolute())

    def test_generated_cached_and_nonofficial(self):
        e=self.editor();a=e.logos.generate('Xvive');b=e.logos.generate('XVIVE')
        self.assertEqual(a['id'],b['id']);self.assertEqual(a['kind'],'generated')
        config=e.logos.apply({'brand_title':'Xvive'})
        self.assertIs(config['brand_logo']['official_claim'],False)
        self.assertGreater(logos.asset_image(config).width,50)

    def test_pending_confirmation_and_aliases(self):
        e=self.editor();a=e.logos.add('Xvive',Image.new('RGBA',(100,50),'red'),'uploaded')
        self.assertFalse(a['approved'])
        with self.assertRaises(ValueError):e.logos.save({'brand':'Xvive','selected':a['id']})
        e.logos.save({'brand':'Xvive','selected':a['id'],'confirmed':True,'aliases':['爱克斯维']})
        self.assertEqual(e.logos.apply({'brand_title':'爱克斯维'})['brand_logo']['id'],a['id'])
        e.logos.generate('Neumann')
        with self.assertRaises(ValueError):e.logos.save({'brand':'Neumann','aliases':['XVIVE']})
        self.assertEqual(app.Editor(e.state_file,start_worker=False).logos.apply({'brand_title':'Xvive'})['brand_logo']['id'],a['id'])

    def test_first_export_lookup_once_and_image_deduplication(self):
        e=self.editor()
        with patch.object(e.logos,'discover',return_value={'message':'test'} ) as discover:
            e.logos.prepare({'brand_title':'Xvive'});e.logos.prepare({'brand_title':'XVIVE'})
            self.assertEqual(discover.call_count,1)
        im=Image.new('RGBA',(120,80),'blue')
        self.assertEqual(e.logos.add('Xvive',im,'web')['id'],e.logos.add('Xvive',im,'web')['id'])
        e.logos.save({'auto_find':False})
        with patch.object(e.logos,'discover',side_effect=AssertionError('network disabled')):e.logos.prepare({'brand_title':'Shure'})

    def test_disable_and_no_stale_assets(self):
        e=self.editor();config=e.logos.apply({'brand_title':'Unknown'})
        e.logos.save({'brand':'Unknown','selected':'','fallback':False})
        e.logos.apply(config);self.assertNotIn('brand_logo',config)
        e.logos.save({'enabled':False});self.assertNotIn('brand_logo',e.logos.apply(config))

    def test_crop_preserves_original_and_enclosed_white(self):
        e=self.editor();im=Image.new('RGBA',(100,100),'white');d=ImageDraw.Draw(im)
        d.rectangle((20,20,80,80),fill='black');d.rectangle((40,40,60,60),fill='white')
        stream=io.BytesIO();im.save(stream,format='PNG');a=e.logos.upload('Test',stream.getvalue())['asset']
        original=e.logos.resolve(a['url']).read_bytes()
        b=e.logos.crop({'brand':'Test','asset_id':a['id'],'rect':[0,0,1,1],'background':'white'})['asset']
        with Image.open(e.logos.resolve(b['url'])) as cropped:
            self.assertEqual(cropped.size,(61,61));self.assertEqual(cropped.getpixel((30,30)),(255,255,255,255))
        self.assertEqual(e.logos.resolve(a['url']).read_bytes(),original)
        with self.assertRaises(ValueError):logos.transform(im,[0,0,.001,.001],'white')
        for rect in [[-1,0,1,1],[0,0,float('nan'),1],[0,0,2,1]]:
            with self.assertRaises(ValueError):logos.transform(im,rect)

    def test_svg_rejects_active_content_and_external_resources(self):
        for contents in ['<script>alert(1)</script>','<image href="file:///C:/secret"/>','<rect fill="url(https://example.org/a)"/>','<use href="https://example.org/a"/>']:
            with self.subTest(contents=contents),self.assertRaises(ValueError):logos.raster(('<svg xmlns="http://www.w3.org/2000/svg">'+contents+'</svg>').encode())

    def test_real_official_svg_conversion(self):
        file=app.ROOT/'work/brand_logo_verification/research/xvive_official_inline.svg'
        if not file.is_file():self.skipTest('Official research fixture is local-only')
        image=logos.raster(file.read_bytes());self.assertGreater(image.width,1000)
        self.assertLess(image.getchannel('A').getextrema()[0],255)

    def test_public_url_and_registered_assets_only(self):
        for url in ['http://example.org','https://localhost','https://127.0.0.1/a','https://user:password@example.org','https://example.org:123/a']:
            with self.subTest(url=url),self.assertRaises(ValueError):logos.public_url(url)
        with patch.object(logos.socket,'getaddrinfo',return_value=[(2,1,6,'',('10.0.0.2',443))]):
            with self.assertRaises(ValueError):logos.public_url('https://example.org')
        with patch.object(logos.socket,'getaddrinfo',return_value=[(2,1,6,'',('198.18.1.2',443))]),patch.object(logos,'proxy_public_dns',return_value=('104.16.172.24',)):
            self.assertEqual(logos.public_url('https://xvive.com')[2],'104.16.172.24')
            with self.assertRaises(ValueError):logos.public_url('https://198.18.1.2')
        with patch.object(logos.socket,'getaddrinfo',return_value=[(2,1,6,'',('198.18.1.2',443))]),patch.object(logos,'proxy_public_dns',return_value=('10.0.0.2',)):
            with self.assertRaises(ValueError):logos.public_url('https://xvive.com')
        e=self.editor()
        for path in ['/brand-logo/../../queue.json','/brand-logo/'+'a'*32+'.png']:
            self.assertIsNone(e.logos.resolve(path))

    def test_discovery_empty_unknown_and_blocked_website_fallback(self):
        e=self.editor()
        self.assertEqual(e.logos.discover({'brand':'Unknown brand'})['assets'][0]['kind'],'generated')
        with patch.object(logos,'public_url',return_value=('https://xvive.com/','xvive.com','1.1.1.1')),patch.object(logos,'download',side_effect=ValueError('HTTP 403')):
            self.assertEqual(e.logos.discover({'brand':'Xvive'})['assets'][0]['kind'],'generated')
        with self.assertRaises(ValueError):e.logos.discover({'brand':'Xvive','url':'https://localhost/'})

    def test_overlay_ass_graph_cover_audio_and_fade(self):
        e=self.editor();config=e.logos.apply({'brand_title':'Xvive','product_title':'U2','description_lines':['无线吉他系统']})
        ass=renderer.make_overlay(config,8)
        self.assertNotIn(',,Xvive',ass)
        target=self.root/'overlay.png';logos.overlay(config,target)
        with Image.open(target) as im:self.assertEqual(im.size,(1080,1440))
        graph=renderer.make_graph([(2,10)],1,1,2,.6,3)
        for term in ['[1:a:0]','[2:v:0]','[3:v:0]','[vout]','[aout]','afade=t=out:st=7.000000','fade=t=out:st=7.000000']:
            self.assertIn(term,graph)

    def test_http_routes_origin_and_private_files(self):
        e=self.editor();server=app.ThreadingHTTPServer(('127.0.0.1',0),app.Handler)
        with patch.object(app,'EDITOR',e),patch.object(app,'PORT',server.server_port):
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            base='http://127.0.0.1:'+str(server.server_port)
            try:
                for path in ['/brand-assets','/web/brand-assets.js','/web/brand-assets.css','/api/brand-logos']:
                    with urlopen(base+path,timeout=5) as r:self.assertEqual(r.status,200)
                def post(origin):return urlopen(Request(base+'/api/brand-logos/generate',data=b'{"brand":"Xvive"}',headers={'Origin':origin,'Content-Type':'application/json'}),timeout=5)
                with self.assertRaises(HTTPError) as error:post('https://evil.example')
                self.assertEqual(error.exception.code,403)
                with post(base) as r:asset=json.load(r)['asset']
                with urlopen(base+asset['url'],timeout=5) as r:self.assertEqual(r.headers['Content-Type'],'image/png')
                with self.assertRaises(HTTPError):urlopen(base+'/brand-logo/'+'a'*32+'.png')
            finally:server.shutdown();server.server_close();thread.join(timeout=5)

    @unittest.skipUnless(os.environ.get('LOGO_SMOKE')=='1','Optional two actual FFmpeg exports with original and generated brand assets')
    def test_real_source_two_exports_cover_audio_decode_original_preserved(self):
        import director_assets
        import typography
        e=self.editor();source=app.ROOT/'sources/yt_JfMF9buchrQ/source.mp4'
        self.assertTrue(source.is_file())
        digest=lambda:hashlib.sha256(source.read_bytes()).hexdigest()
        before=digest();proof=[]
        for mode in ['generated','official_candidate_test_only']:
            if mode=='generated':e.logos.generate('Xvive')
            else:
                im=logos.raster((app.ROOT/'work/brand_logo_verification/research/xvive_official_inline.svg').read_bytes())
                asset=e.logos.add('Xvive',im,'web','https://xvive.com/audio/#inline-logo')
                # Synthetic confirmation in an isolated test library, not user consent.
                e.logos.save({'brand':'Xvive','selected':asset['id'],'confirmed':True})
            config=e.logos.apply({'source':str(source),'brand_title':'Xvive','product_title':'U2','description_lines':['无线吉他系统'],
                'typography':typography.load_style(),'text_scale':1.1,'segments':[{'start':12,'end':20}],
                'output_prefix':'logo_'+mode,'output_dir':str(e.state_file.parent/'exports')})
            report={'key':hashlib.sha256((str(e.state_file)+mode).encode()).hexdigest(),'cover_time':13}
            config.update(director_assets.create(source,report,{'brand_title':'Xvive','product_title':'U2','description':'无线吉他系统'},config['typography'],brand_logo=config['brand_logo']))
            config_file=e.state_file.parent/(mode+'.json');config_file.parent.mkdir(parents=True,exist_ok=True)
            config_file.write_text(json.dumps(config,ensure_ascii=False),encoding='utf-8')
            self.assertEqual(renderer.render(config_file),0)
            output=max((e.state_file.parent/'exports').glob('logo_'+mode+'_*.mp4'),key=lambda p:p.stat().st_mtime_ns)
            metadata=json.loads(output.with_suffix('.json').read_text(encoding='utf-8'))
            self.assertEqual(metadata['validation']['duration_seconds'],8)
            self.assertEqual(metadata['validation']['audio_codec'],'aac')
            self.assertEqual(metadata['brand_logo']['kind'],'generated' if mode=='generated' else 'web')
            frames=[]
            for at,label in [(0,'cover'),(2,'body')]:
                frame=e.state_file.parent/(mode+'_'+label+'.jpg')
                subprocess.run([str(renderer.FFMPEG),'-v','error','-nostdin','-ss',str(at),'-i',str(output),'-frames:v','1','-y',str(frame)],capture_output=True,check=True,timeout=30)
                frames.append(str(frame))
            result=subprocess.run([str(renderer.FFMPEG),'-v','error','-nostdin','-i',str(output),'-f','null','-'],capture_output=True,timeout=60)
            self.assertEqual(result.returncode,0,result.stderr.decode(errors='replace'))
            proof.append({'mode':mode,'output':str(output),'frames':frames,'validation':metadata['validation'],'api_calls':0})
        self.assertEqual(before,digest())
        report={'exports':proof,'source_sha256_unchanged':before,'production_queue_changed':False,'original_logo_permission_confirmed_by_user':False}
        target=app.ROOT/'work/brand_logo_verification/real_render_proof.json'
        target.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print('REAL_LOGO_PROOF '+str(target))


if __name__=='__main__':unittest.main()
