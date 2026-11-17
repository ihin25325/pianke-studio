"""Studio upgrade regression and opt-in real local 30s rendering."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.request import Request,urlopen
from urllib.error import HTTPError
import editor_server as app
import studio
import product_render as renderer
import vision_adapter

URL='https://www.youtube.com/watch?v=FlT0bAc9Xp4'


class StudioTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root=app.ROOT/'work/studio_tests';root.mkdir(parents=True,exist_ok=True)
        cls.root=Path(tempfile.mkdtemp(prefix='run_',dir=root))

    def editor(self):
        e=app.Editor(self.root/self._testMethodName/'queue.json',start_worker=False)
        e.board.paste({'text':'Neumann U87 Ai | 录音电容麦克风 | '+URL})
        return e,e.state['products'][0]

    def test_options_validation_and_saved_settings(self):
        e,p=self.editor();old=copy.deepcopy(e.typography)
        for value in [{'mode':'xx'},{'cover_hold':float('nan')},{'cover_enabled':1},{'cover_hold':30},{'strange':1}]:
            with self.subTest(value=value),self.assertRaises(ValueError):studio.options(value)
        e.studio.save({'mode':'manual','cover_style':'coral','cover_enabled':False,'cover_hold':1})
        restarted=app.Editor(e.state_file,start_worker=False)
        self.assertEqual(restarted.state['studio_settings']['mode'],'manual');self.assertEqual(old,e.typography)

    def test_opt_in_jobs_snapshot_and_manual_tasks_unchanged(self):
        e,p=self.editor();original=e.board.new_task(p,'render')
        self.assertNotIn('director_options',original);original['status']='failed'
        e.studio.save({'mode':'smart30'})
        r=e.studio.jobs({'ids':[p['id']],'job':'render'})
        self.assertEqual(r['added'],1);t=e.state['tasks'][-1];self.assertEqual(t['director_options']['mode'],'smart30')
        e.studio.save({'mode':'manual'});self.assertEqual(t['director_options']['mode'],'smart30')
        self.assertEqual(e.studio.jobs({'ids':[p['id']],'job':'analyze'})['added'],0)

    def test_analysis_job_and_approval_per_version(self):
        e,p=self.editor();r=e.studio.jobs({'ids':[p['id']],'job':'analyze'})
        t=e.state['tasks'][-1];self.assertEqual(t['job_type'],'analyze')
        t['status']='analyzed';self.assertEqual(e.board.view(p)['status'],'ready')
        task=e.board.new_task(p,'render');file=e.state_file.parent/'video.mp4';file.write_bytes(b'x'*2048)
        task.update(status='completed',output_file=str(file),output_url='/media/task/'+task['id']+'/video.mp4')
        e.studio.approval(p['id'],{'task_id':task['id'],'decision':'approved'})
        self.assertFalse(e.board.view(p)['needs_approval'])
        self.assertEqual(e.board.snapshot({'view':['review']})['total'],0)
        e.board.redo(p['id'],{});new=e.state['tasks'][-1]
        new.update(status='completed',output_file=str(file),output_url='/media/task/'+new['id']+'/video.mp4')
        self.assertTrue(e.board.view(p)['needs_approval'])
        with self.assertRaises(ValueError):e.studio.approval(p['id'],{'task_id':task['id'],'decision':'approved'})

    def test_cover_manual_choice_bound_to_source(self):
        e,p=self.editor()
        with patch.object(e.review,'source',return_value={'duration':43}):
            e.studio.cover_choice(p['id'],{'time':'00:12.5'})
            self.assertEqual(p['cover_choice']['time'],12.5)
            with self.assertRaises(ValueError):e.studio.cover_choice(p['id'],{'time':44})

    def test_asset_access_is_registered_and_scoped(self):
        e,p=self.editor()
        for path in ['/director/../../queue.json','/director/'+'a'*64+'/report.json','/director/'+'a'*64+'/storyboard.jpg']:
            self.assertIsNone(e.studio.resolve_asset(path))

    def test_cover_graph_preserves_audio_and_fade(self):
        graph=renderer.make_graph([(4,34)],1,1,2,.6)
        self.assertIn('[2:v:0]',graph);self.assertIn("enable='lt(t,0.600000)'",graph)
        self.assertIn('afade=t=out:st=29.000000',graph);self.assertIn('fade=t=out:st=29.000000',graph)
        self.assertIn('[aout]',graph)
        with self.assertRaises(ValueError):renderer.make_graph([(0,30)],0,1,1,4)

    def test_cloud_disabled_no_key_lookup_or_network(self):
        with patch.object(vision_adapter,'urlopen',side_effect=AssertionError('must not call network')):
            with self.assertRaises(ValueError):vision_adapter.assess(Path('unused.jpg'),{})

    def test_studio_http_routes_origin_and_private_assets(self):
        e,p=self.editor()
        server=app.ThreadingHTTPServer(('127.0.0.1',0),app.Handler)
        with patch.object(app,'EDITOR',e),patch.object(app,'PORT',server.server_port):
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            base='http://127.0.0.1:'+str(server.server_port)
            def post(path,payload,origin):
                return urlopen(Request(base+path,data=json.dumps(payload).encode(),
                    headers={'Content-Type':'application/json','Origin':origin}),timeout=10)
            try:
                with urlopen(base+'/api/studio',timeout=10) as r:
                    state=json.load(r);self.assertFalse(state['cloud_enabled']);self.assertEqual(len(state['presets']),5)
                with self.assertRaises(HTTPError) as error:
                    post('/api/studio/settings',{'mode':'manual'},'https://untrusted.example')
                self.assertEqual(error.exception.code,403)
                with post('/api/studio/settings',{'mode':'manual'},base) as r:self.assertTrue(json.load(r)['ok'])
                with post('/api/studio/jobs',{'ids':[p['id']],'job':'analyze'},base) as r:self.assertEqual(json.load(r)['added'],1)
                for path in ['/font-assets/not-a-font/data.ttf','/director/'+'a'*64+'/storyboard.jpg']:
                    with self.assertRaises(HTTPError) as error:urlopen(base+path,timeout=10)
                    self.assertEqual(error.exception.code,404)
                if (app.ROOT/'assets/fonts/Inter/Inter-Regular.ttf').is_file():
                    with urlopen(base+'/font-assets/Inter/Inter-Regular.ttf',timeout=10) as r:
                        self.assertEqual(r.status,200);self.assertGreater(len(r.read()),10000)
                else:
                    with self.assertRaises(HTTPError) as error:urlopen(base+'/font-assets/Inter/Inter-Regular.ttf',timeout=10)
                    self.assertEqual(error.exception.code,404)
            finally:server.shutdown();server.server_close();thread.join(3)

    def test_local_window_exclusion_logic_in_separate_runtime(self):
        if not (app.ROOT/'tools/vision_packages/cv2').is_dir():self.skipTest('Optional local vision environment not installed.')
        # The standalone process isolates NumPy/OpenCV from speech dependencies.
        code="""import sys
sys.path.insert(0,'scripts')
import scene_director as d
frames=[dict(time=i,score=80 if i>=5 else 10,dark=0,motion=.05) for i in range(45)]
best,_=d.choose_window(frames,45,30,[dict(start=0,end=5,kind='black')])
assert best['start']>=5 and round(best['end']-best['start'])==30,best
best,_=d.choose_window(frames[:20],20,30,[dict(start=0,end=5,kind='intro')])
assert best['start']>=5 and best['end']<=20 and best.get('shortened'),best
print('LOCAL_WINDOW_OK')
"""
        result=subprocess.run([sys.executable,'-B','-c',code],cwd=app.ROOT,capture_output=True,timeout=40)
        self.assertEqual(result.returncode,0,result.stderr.decode(errors='replace'))

    @unittest.skipUnless(os.environ.get('STUDIO_SMOKE')=='1','optional real local director + 30s cover export')
    def test_real_director_cover_first_frame_and_original_preserved(self):
        e,p=self.editor();source=app.ROOT/'sources/neumann_u87ai/source.mp4';before=source.stat()
        e.storage={'output_dir':str(e.state_file.parent/'exports')};e.state['settings']['subtitles_enabled']=False
        e.studio.jobs({'ids':[p['id']],'job':'render'});task=e.state['tasks'][-1]
        with patch.object(e,'existing_source',return_value=source):e.process(task)
        self.assertEqual(task['status'],'completed',task.get('message'))
        output=Path(task['output_file']);meta=json.loads(output.with_suffix('.json').read_text(encoding='utf-8'))
        self.assertEqual(meta['validation']['duration_seconds'],30)
        self.assertEqual(meta['validation']['audio_codec'],'aac');self.assertTrue(meta['cover'])
        self.assertEqual(meta['director']['api_calls'],0)
        self.assertTrue(meta['director']['semantics']['ready'],meta['director'].get('warnings'))
        self.assertTrue(e.resolve_media(task['cover_url']).is_file());self.assertTrue(e.resolve_media(task['title_asset_url']).is_file())
        frame=e.state_file.parent/'first_frame.jpg'
        subprocess.run([str(renderer.FFMPEG),'-v','error','-i',str(output),'-frames:v','1','-y',str(frame)],check=True,timeout=30)
        from PIL import Image,ImageChops,ImageStat
        with Image.open(frame) as a,Image.open(output.with_suffix('.cover.jpg')) as b:
            diff=ImageStat.Stat(ImageChops.difference(a.convert('RGB'),b.convert('RGB'))).mean
        self.assertLess(sum(diff)/3,10)
        self.assertEqual((source.stat().st_size,source.stat().st_mtime_ns),(before.st_size,before.st_mtime_ns))
        proof={'output':str(output),'cover':str(output.with_suffix('.cover.jpg')),'first_frame':str(frame),
               'title_asset':str(output.with_suffix('.title.png')),'first_frame_mean_error':diff,
               'validation':meta['validation'],'selection':meta['director']['selected'],'state_file':str(e.state_file),
               'product_id':p['id'],'task_id':task['id'],'source_unchanged':True}
        (self.root/'real_studio_proof.json').write_text(json.dumps(proof,ensure_ascii=False,indent=2),encoding='utf-8')
        print('STUDIO_PROOF '+json.dumps(proof,ensure_ascii=False))

    @unittest.skipUnless(os.environ.get('STUDIO_SMOKE')=='1','optional black / foreign intro fixture')
    def test_synthetic_black_intro_and_watermark_detection(self):
        folder=self.root/self._testMethodName;folder.mkdir()
        source=folder/'black_intro_fixture.mp4'
        cmd=[str(renderer.FFMPEG),'-v','error','-nostdin','-f','lavfi','-i','color=c=black:s=640x360:r=15:d=3',
             '-f','lavfi','-i',"color=c=white:s=640x360:r=15:d=4,drawtext=text='OTHER CHANNEL':fontsize=45:fontcolor=black:x=(w-tw)/2:y=(h-th)/2",
             '-f','lavfi','-i','testsrc2=s=640x360:r=15:d=38','-f','lavfi','-i','color=c=black:s=640x360:r=15:d=3',
             '-filter_complex','[0:v][1:v][2:v][3:v]concat=n=4:v=1:a=0[v]','-map','[v]','-c:v','libx264','-preset','ultrafast','-threads','2','-n',str(source)]
        result=subprocess.run(cmd,capture_output=True,timeout=60);self.assertEqual(result.returncode,0,result.stderr.decode(errors='replace'))
        output=folder/'report.json'
        result=subprocess.run([sys.executable,'-B',str(app.ROOT/'scripts/scene_director.py'),'--source',str(source),'--output',str(output),
                               '--brand','ActualBrand','--model','X1'],cwd=app.ROOT,capture_output=True,timeout=120)
        self.assertEqual(result.returncode,0,result.stderr.decode(errors='replace'))
        report=json.loads(output.read_text(encoding='utf-8'))
        self.assertTrue(any(i['kind']=='black' for i in report['excluded']))
        self.assertTrue(any(i['kind']=='intro' for i in report['excluded']))
        self.assertGreaterEqual(report['selected']['start'],6.5);self.assertLessEqual(report['selected']['end'],45)
        print('BLACK_INTRO_PROOF '+json.dumps({'report':str(output),'selected':report['selected']},ensure_ascii=False))

    @unittest.skipUnless(os.environ.get('STUDIO_SMOKE')=='1','optional persistent corner-mark fixture')
    def test_persistent_foreign_corner_mark_requires_review(self):
        folder=self.root/self._testMethodName;folder.mkdir()
        source=folder/'marked_fixture.mp4';output=folder/'report.json'
        r=subprocess.run([str(renderer.FFMPEG),'-v','error','-nostdin','-f','lavfi','-i',
                          "testsrc2=s=640x360:r=15:d=33,drawbox=x=0:y=0:w=190:h=32:color=white:t=fill,drawtext=text='OTHERCHANNEL':fontsize=19:fontcolor=black:x=5:y=4",
                          '-c:v','libx264','-preset','ultrafast','-n',str(source)],capture_output=True,timeout=60)
        self.assertEqual(r.returncode,0,r.stderr.decode(errors='replace'))
        r=subprocess.run([sys.executable,'-B',str(app.ROOT/'scripts/scene_director.py'),'--source',str(source),'--output',str(output),
                          '--brand','ActualBrand','--model','X1'],cwd=app.ROOT,capture_output=True,timeout=120)
        self.assertEqual(r.returncode,0,r.stderr.decode(errors='replace'))
        report=json.loads(output.read_text(encoding='utf-8'))
        self.assertTrue(report['ocr_ready']);self.assertTrue(any(x['kind']=='watermark' for x in report['excluded']))
        self.assertTrue(report['needs_review'],report['selected'])
        print('CORNER_MARK_PROOF '+str(output))


if __name__=='__main__':unittest.main(verbosity=2)
