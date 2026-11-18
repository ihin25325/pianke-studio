"""Subtitle layout regression tests; optional real-media smoke stays in project work/."""
import copy
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import editor_server as app
import product_render as renderer
import speech_subtitles as speech
import typography


class SubtitlePositionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        folder=app.ROOT/'work'/'subtitle_position_tests'
        folder.mkdir(parents=True,exist_ok=True)
        cls.folder=Path(tempfile.mkdtemp(prefix='run_',dir=folder))

    def editor(self):
        return app.Editor(self.folder/(self._testMethodName+'.json'),start_worker=False)

    def test_old_style_keeps_original_position(self):
        old={k:v for k,v in typography.DEFAULT_STYLE.items() if not k.startswith('subtitle_')}
        style=typography.normalize_style(old)
        self.assertEqual((style['subtitle_align'],style['subtitle_y']),('center',994))
        e=self.editor()
        e.add({'text':'Shure SM7B'})
        e.state['tasks'][0]['typography']=old
        self.assertEqual(e.typography_state(e.state['tasks'][0]['id'])['style'],style)

    def test_invalid_coordinates_and_alignment(self):
        for value in [True,None,'780',179,1381,float('nan'),float('inf')]:
            with self.subTest(value=value),self.assertRaises(ValueError):
                typography.normalize_style({'subtitle_y':value})
        for value in [None,[],{},'top',r'center\pos(0,0)']:
            with self.subTest(value=value),self.assertRaises(ValueError):
                typography.normalize_style({'subtitle_align':value})
        for value in [180,994,1380]:
            self.assertEqual(typography.normalize_style({'subtitle_y':value})['subtitle_y'],value)

    def test_alignment_and_y_in_actual_ass(self):
        cues=[{'start':0,'end':1,'text':'中文测试字幕'}]
        for align,anchor,x in [('left',1,70),('center',2,540),('right',3,1010)]:
            with self.subTest(align=align):
                result=speech.speech_ass(cues,1,{'subtitle_align':align,'subtitle_y':780})
                self.assertIn(f'\\an{anchor}\\pos({x},780)',result)
        self.assertIn(r'\an2\pos(540,994)',speech.speech_ass(cues,1))
        self.assertEqual(speech.speech_ass(cues,0), '')

    def test_renderer_uses_position_and_warns_on_overlap(self):
        config=renderer.read_json(app.ROOT/'configs'/'defaults.json')
        config.update(subtitles_enabled=True,subtitle_srt='1\n00:00:00,000 --> 00:00:01,000\n这是字幕\n',
                      typography=typography.normalize_style({'subtitle_y':780,'subtitle_align':'right'}))
        layout={}
        self.assertIn(r'\an3\pos(1010,780)',renderer.make_overlay(config,1,layout))
        self.assertEqual(layout['boxes']['subtitle']['bottom'],780)
        self.assertFalse(any('重叠' in s for s in layout['warnings']))
        config['typography']['subtitle_y']=int(layout['boxes']['title']['bottom'])
        layout={}
        renderer.make_overlay(config,1,layout)
        self.assertTrue(any('字幕' in s and '重叠' in s for s in layout['warnings']))

    def test_save_restart_and_task_snapshot(self):
        file=self.folder/'position_style.json'
        style=typography.normalize_style({'subtitle_align':'left','subtitle_y':860})
        with patch.object(app,'STYLE_FILE',file),patch.object(typography,'STYLE_FILE',file):
            e=self.editor()
            e.save_typography({'style':style})
            restarted=app.Editor(e.state_file,start_worker=False)
            self.assertEqual(restarted.typography_state()['style'],style)
            restarted.add({'text':'Shure SM7B'})
            original=restarted.state['tasks'][0]
            before=copy.deepcopy(original)
            edited_style=dict(style,subtitle_y=590,subtitle_align='right')
            restarted.edited_copy(original['id'],{'style':edited_style,
                'text':{'brand_title':'SHURE','product_title':'SM7B','description':'动圈麦克风'},
                'subtitles':{'enabled':True,'srt':'1\n00:00:00,000 --> 00:00:01,000\n这条保留原字幕\n'}})
            self.assertEqual(original,before)
            task=restarted.state['tasks'][-1]
            self.assertEqual(task['typography'],edited_style)
            self.assertIn('这条保留原字幕',task['subtitle_srt'])
            restarted.typography['subtitle_y']=994
            self.assertEqual(task['typography']['subtitle_y'],590)

    @unittest.skipUnless(os.environ.get('SUBTITLE_POSITION_SMOKE')=='1','optional real FFmpeg check')
    def test_real_preview_and_short_export(self):
        e=self.editor()
        before=copy.deepcopy(e.state)
        payload={'style':dict(e.typography,subtitle_align='left',subtitle_y=780),
                 'text':{'brand_title':'NEUMANN','product_title':'U87 Ai','description':'录音电容麦克风'},
                 'subtitles':{'enabled':False},'subtitle_sample':True}
        preview=e.preview_typography(payload)
        self.assertEqual(preview['speech_preview_kind'],'sample')
        self.assertEqual(preview['layout']['subtitle']['bottom'],780)
        self.assertEqual(e.state,before)
        folder=app.ROOT/'work'/'style_previews'/Path(preview['url']).stem
        self.assertIn(r'\an1\pos(70,780)',(folder/'overlay.ass').read_text(encoding='utf-8-sig'))
        no_sample=e.preview_typography(dict(payload,subtitle_sample=False))
        self.assertFalse(no_sample['speech_preview'])
        self.assertNotIn('subtitle',no_sample['layout'])
        config=dict(e.sample,typography=payload['style'],subtitles_enabled=True,
                    subtitle_srt='1\n00:00:00,000 --> 00:00:01,500\n字幕位置测试：左对齐，画面中间\n',
                    max_duration_seconds=1.5,segments=[{'start':0,'end':1.5}],output_prefix='subtitle_position_smoke')
        config_file=self.folder/'smoke_config.json'
        config_file.write_text(json.dumps(config,ensure_ascii=False,indent=2),encoding='utf-8')
        self.assertEqual(renderer.render(config_file),0)
        metadata=max((app.ROOT/'outputs').glob('subtitle_position_smoke_*.json'),key=lambda p:p.stat().st_mtime_ns)
        result=json.loads(metadata.read_text(encoding='utf-8'))
        self.assertEqual(result['status'],'success')
        self.assertEqual(result['typography']['editor_settings']['subtitle_y'],780)
        self.assertEqual(result['subtitle_count'],1)
        self.assertNotIn('这是语音字幕位置预览',Path(result['subtitle_file']).read_text(encoding='utf-8-sig'))
        frame=self.folder/'export_frame.png'
        decoded=subprocess.run([str(renderer.FFMPEG),'-nostdin','-v','error','-n','-ss','0.5','-i',result['output'],
                                '-frames:v','1',str(frame)],capture_output=True,timeout=30)
        self.assertEqual(decoded.returncode,0,decoded.stderr.decode('utf-8',errors='replace'))
        self.assertTrue(frame.is_file())
        proof={'preview':str(folder/'preview.png'),'output':result['output'],'frame':str(frame),'validation':result['validation']}
        (self.folder/'smoke_result.json').write_text(json.dumps(proof,ensure_ascii=False,indent=2),encoding='utf-8')
        print('SUBTITLE_SMOKE_RESULT='+json.dumps(proof,ensure_ascii=False))


if __name__=='__main__':
    unittest.main(verbosity=2)
