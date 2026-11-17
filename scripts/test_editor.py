"""Offline regression tests; isolated state under D:/.../work, no media downloads."""
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.request import Request, urlopen
from urllib.error import HTTPError

import editor_server as app
from typography import DEFAULT_STYLE,normalize_style,normalize_text
import speech_subtitles as speech
import chinese_translation as chinese


class EditorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        folder=app.ROOT/'work'/'editor_tests'
        folder.mkdir(parents=True,exist_ok=True)
        cls.test_root=Path(tempfile.mkdtemp(prefix='run_',dir=folder))

    def editor(self):
        return app.Editor(self.test_root/(self._testMethodName+'.json'),start_worker=False)

    def test_model_identity(self):
        self.assertEqual(app.identity('Neumann U 87 Ai 电容话筒'),('Neumann',['U87AI']))
        self.assertEqual(app.identity('Shure SM7B 动圈麦克风'),('Shure',['SM7B']))
        self.assertEqual(app.identity('AKG C414 XLII'),('AKG',['C414XLII']))
        self.assertEqual(app.identity('Shure MV7 USB 麦克风'),('Shure',['MV7']))
        self.assertEqual(app.identity('Focusrite Scarlett 2i2 声卡'),('Focusrite',['2I2']))

    def test_exact_variant(self):
        self.assertFalse(app.exact_model('SM58','Shure SM58S'))
        self.assertFalse(app.exact_model('C414XLII','AKG C414 XLS'))
        self.assertFalse(app.exact_model('NT1','Rode NT1-A'))
        self.assertFalse(app.exact_model('U87','Neumann U 87 Ai'))
        self.assertTrue(app.exact_model('U87AI','Neumann U 87 Ai'))

    def test_url_safety(self):
        self.assertEqual(app.youtube_url('https://youtu.be/FlT0bAc9Xp4?t=5'),'https://www.youtube.com/watch?v=FlT0bAc9Xp4')
        for url in ['file:///D:/secret','http://127.0.0.1/','https://youtube.com.evil.test/watch?v=FlT0bAc9Xp4','https://youtube.com/playlist?list=abc','https://user:pass@youtube.com/watch?v=FlT0bAc9Xp4']:
            self.assertIsNone(app.youtube_url(url))

    def test_candidate_safety(self):
        base={'title':'Shure SM58 official product overview','channel':'Shure','duration':60}
        self.assertGreaterEqual(app.candidate_score(base,'Shure','SM58'),80)
        for changes in [{'title':'Shure SM58 vs SM58S official comparison'},{'duration':1000},{'title':'Shure SM58S official product overview'},{'duration':0}]:
            self.assertLess(app.candidate_score(dict(base,**changes),'Shure','SM58'),80)

    def test_catalog_inputs(self):
        e=self.editor()
        e.by_id={'10000001':{'product_id':'10000001','title':'Neumann U87 Ai 录音电容麦克风'}}
        for text in ['10000001','https://item.taobao.com/item.htm?id=10000001','10000001\t示例标题\t99.0']:
            task=e.parse_line(text)
            self.assertEqual(task['model'],'U87AI')
            self.assertEqual(task['product_id'],'10000001')
            self.assertEqual(task['status'],'queued')
        self.assertEqual(e.parse_line('123456789123')['status'],'needs_review')
        self.assertEqual(e.parse_line('Neumann U87 Ai U67')['status'],'needs_review')

    def test_custom_description(self):
        e=self.editor()
        task=e.parse_line('Shure SM7B | 用户核实的简介 | https://youtu.be/FlT0bAc9Xp4')
        self.assertEqual(task['description'],'用户核实的简介')
        self.assertEqual(task['selected_url'],'https://www.youtube.com/watch?v=FlT0bAc9Xp4')

    def test_category_only_defaults(self):
        e=self.editor()
        examples = [
            ('Neumann U87 Ai 电容麦克风','Neumann U87 Ai','录音电容麦克风'),
            ('Shure SM7B 动圈麦克风 人声清晰','Shure SM7B','动圈麦克风'),
            ('Focusrite Scarlett 2i2 录音声卡','Focusrite 2i2','音频接口'),
            ('Behringer X1204USB 调音台 内置效果器','Behringer X1204USB','调音台'),
            ('Arturia KeyLab 49 MIDI键盘','Arturia KeyLab49','MIDI键盘'),
        ]
        for text, title, category in examples:
            with self.subTest(text=text):
                task=e.parse_line(text)
                self.assertEqual(task['title'],title)
                self.assertEqual(task['description'],category)
                self.assertEqual(task['original_title'],text)

    def test_limits_and_dedup(self):
        e=self.editor()
        self.assertEqual(e.add({'text':'Shure SM7B\nShure SM7B'})['added'],1)
        for payload in [{'text':'x\n'*101},{'text':'x','max_duration_seconds':91},{'text':'x','text_scale':2}]:
            with self.assertRaises(ValueError): e.add(payload)

    def test_restart_preserves_originals(self):
        e=self.editor()
        e.add({'text':'Neumann U87 Ai'})
        e.state['tasks'][0]['status']='rendering'
        e.state['paused']=True
        e.save()
        restarted=app.Editor(e.state_file,start_worker=False)
        self.assertEqual(restarted.state['tasks'][0]['status'],'queued')
        self.assertTrue(restarted.state['paused'])

    def test_http_security_and_range(self):
        e=self.editor()
        app.EDITOR=e
        server=app.ThreadingHTTPServer(('127.0.0.1',0),app.Handler)
        app.PORT=server.server_port
        thread=threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        base=f'http://127.0.0.1:{app.PORT}'
        try:
            with urlopen(base+'/api/health') as response:
                self.assertTrue(json.load(response)['ok'])
            request=Request(base+'/api/tasks',data=b'{"text":"Shure SM7B"}',headers={'Content-Type':'application/json','Origin':'https://untrusted.example'})
            with self.assertRaises(HTTPError) as failure: urlopen(request)
            self.assertEqual(failure.exception.code,403)
            for path in ['/media/..%2Fdata%2Fproducts.json','/data/products.json']:
                with self.assertRaises(HTTPError) as failure: urlopen(base+path)
                self.assertEqual(failure.exception.code,404)
            media=next((app.ROOT/'outputs').glob('*.mp4'),None)
            if media:
                with urlopen(Request(base+'/media/'+app.quote(media.name),headers={'Range':'bytes=0-99'})) as response:
                    self.assertEqual(response.status,206)
                    self.assertEqual(len(response.read()),100)
                    self.assertTrue(response.headers['Content-Range'].startswith('bytes 0-99/'))
        finally:
            server.shutdown()
            server.server_close()

    def test_typography_validation(self):
        for value in [{'title_font':'bad\\fnArial'},{'title_scale':float('nan')},{'title_offset':200},{'title_color':'red'},{'title_bold':'yes'}]:
            with self.subTest(value=value),self.assertRaises(ValueError):
                normalize_style(value)
        self.assertEqual(normalize_style({'title_scale':1.2})['title_scale'],1.2)
        self.assertEqual(normalize_text({'brand_title':' A ','product_title':'B','description':''})['brand_title'],'A')

    def test_edited_copy_preserves_original_and_style_snapshot(self):
        e=self.editor()
        e.add({'text':'Neumann U87 Ai'})
        original=e.state['tasks'][0]
        original.update(status='completed',output_url='/media/original.mp4')
        before=json.loads(json.dumps(original))
        response=e.edited_copy(original['id'],{'style':dict(DEFAULT_STYLE,title_scale=1.2),
            'text':{'brand_title':'NEUMANN','product_title':'自定标题 U87 Ai','description':'录音麦克风'}})
        self.assertEqual(original,before)
        edited=e.state['tasks'][-1]
        self.assertEqual(edited['id'],response['task_id'])
        self.assertEqual(edited['render_text']['product_title'],'自定标题 U87 Ai')
        self.assertEqual(edited['typography']['title_scale'],1.2)
        self.assertEqual(edited['status'],'queued')
        e.typography['title_scale']=.8
        self.assertEqual(edited['typography']['title_scale'],1.2)

    def test_typography_persistence(self):
        e=self.editor()
        file=self.test_root/'style_settings.json'
        with patch.object(app,'STYLE_FILE',file):
            e.save_typography({'style':dict(DEFAULT_STYLE,title_scale=1.15)})
        self.assertEqual(json.loads(file.read_text(encoding='utf-8'))['title_scale'],1.15)


    def test_srt_validation_and_injection_safety(self):
        valid='1\n00:00:01,000 --> 00:00:02,500\nHello {\\pos(0,0)} world\n'
        cues=speech.parse_srt(valid)
        self.assertEqual(cues[0]['end'],2.5)
        self.assertEqual(speech.parse_srt(speech.write_srt(cues)),cues)
        self.assertIn('｛＼pos(0,0)｝',speech.speech_ass(cues,10))
        for value in ['bad text','1\n00:00:02,000 --> 00:00:01,000\nx',
                      '1\n00:00:00,000 --> 00:01:31,000\nx',
                      '1\n00:00:00,000 --> 00:00:03,000\nx\n\n2\n00:00:02,000 --> 00:00:04,000\ny']:
            with self.subTest(value=value),self.assertRaises(ValueError):speech.parse_srt(value)

    def test_subtitles_snapshot_and_original_preservation(self):
        e=self.editor()
        with patch.object(speech,'ready',return_value=True),patch.object(chinese,'ready',return_value=True):
            e.add({'text':'Neumann U87 Ai','subtitles':{'enabled':True,'language':'en'}})
        original=e.state['tasks'][0];before=json.loads(json.dumps(original))
        srt='1\n00:00:01,000 --> 00:00:02,500\n原声字幕\n'
        e.edited_copy(original['id'],{'style':DEFAULT_STYLE,'text':{'brand_title':'NEUMANN','product_title':'U87 Ai','description':'麦克风'},
            'subtitles':{'enabled':True,'language':'en','srt':srt}})
        self.assertEqual(original,before)
        self.assertEqual(e.state['tasks'][-1]['subtitle_srt'],srt)
        self.assertTrue(e.state['settings']['subtitles_enabled'])
        with patch.object(speech,'ready',return_value=False),self.assertRaises(ValueError):
            e.add({'text':'Shure SM7B','subtitles':{'enabled':True}})

    def test_subtitles_are_opt_in_and_recoverable(self):
        e=self.editor();e.add({'text':'Neumann U87 Ai'})
        task=e.state['tasks'][0];self.assertFalse(task['subtitles_enabled'])
        task.update(status='completed',transcription={'status':'transcribing','progress':10});e.save()
        restarted=app.Editor(e.state_file,start_worker=False)
        self.assertEqual(restarted.state['tasks'][0]['status'],'completed')
        self.assertEqual(restarted.state['tasks'][0]['transcription']['status'],'queued')

    def test_chinese_translation_timing_and_no_truncation(self):
        text='这是有源DI盒，支持XLR输出。'*8
        cues=chinese.timed_captions(text,{'start':1.1,'end':12.4})
        self.assertEqual(''.join(cue['text'] for cue in cues),text)
        self.assertEqual(cues[0]['start'],1.1)
        self.assertEqual(cues[-1]['end'],12.4)
        self.assertEqual(len(speech.parse_srt(speech.write_srt(cues))),len(cues))
        phrases=chinese.units([{'start':1,'end':2,'text':'This is'},{'start':2,'end':3,'text':'a microphone.'},{'start':6,'end':7,'text':'Next.'}])
        self.assertEqual(len(phrases),2)
        self.assertEqual(phrases[0]['text'],'This is a microphone.')

    def test_chinese_identity_and_original_modes(self):
        original={'language':'zh','srt':'1\n00:00:01,000 --> 00:00:02,000\n麦克风\n','segments':[{'start':1,'end':2,'text':'麦克风'}]}
        with patch.object(chinese,'ready',return_value=False):
            result=chinese.translate(original,'zh')
            self.assertEqual(result['srt'],original['srt'])
            self.assertFalse(result['translation_used'])
            self.assertEqual(chinese.translate(dict(original,language='en'),'original')['srt'],original['srt'])
            with self.assertRaises(ValueError): chinese.translate(dict(original,language='en'),'zh')
        with self.assertRaises(ValueError): chinese.target_code('bad')

    def test_chinese_default_and_task_isolation(self):
        e=self.editor()
        self.assertEqual(e.typography_state()['subtitles']['target'],'zh')
        with patch.object(speech,'ready',return_value=True),patch.object(chinese,'ready',return_value=True):
            e.add({'text':'Neumann U87 Ai','subtitles':{'enabled':True,'target':'zh'}})
        self.assertEqual(e.state['tasks'][0]['subtitle_target'],'zh')
        e.state['settings']['subtitle_target']='original'
        self.assertEqual(e.state['tasks'][0]['subtitle_target'],'zh')


if __name__=='__main__':
    unittest.main(verbosity=2)
