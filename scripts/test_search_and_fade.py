"""Offline search/fade regression tests. Network commands are always mocked."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import editor_server as app
import search_strategy as strategy
import product_render as renderer


class SearchAndFadeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        folder=app.ROOT/'work'/'search_upgrade_tests'
        folder.mkdir(parents=True,exist_ok=True)
        cls.folder=Path(tempfile.mkdtemp(prefix='run_',dir=folder))

    def editor(self):
        return app.Editor(self.folder/(self._testMethodName+'.json'),start_worker=False)

    def task(self,e,title='Xvive AT-1 audio tester'):
        e.board.paste({'text':title})
        return e.board.new_task(e.state['products'][-1],'collect')

    def entry(self,title='Xvive AT-1 audio tester review',duration=674,vid='abcdefghijk'):
        return {'id':vid,'title':title,'duration':duration,'channel':'Demo Studio','url':'https://www.youtube.com/watch?v='+vid}

    def test_expanded_model_recognition(self):
        cases={
            'miniDSP 2X4HD 专业分配器':('miniDSP',['2X4HD']),
            'Behringer PHARA-O MINI模拟合成器':('Behringer',['PHARAOMINI']),
            'Waldorf Blofeld Desktop white/Black':('Waldorf',['BLOFELDDESKTOP']),
            'Alctron XU-2MK3转换器USB外置声卡48V':('Alctron',['XU2MK3']),
            'Synido Gaming A7 AI 7.1 Gaming Sound Card':('Synido',['A7']),
            'Midiplus MS5 V2 监听音箱':('Midiplus',['MS5V2']),
            'Sonicake matribox II Pro电吉他':('Sonicake',['MATRIBOXIIPRO']),
            'M-Live B.beat Pro 16 16轨播放器':('M-Live',['BBEATPRO16']),
            'M Live GO.8 音频接口':('M-Live',['GO8']),
            'SOYUZ Launcher Deluxe Lakeside 麦克风话放':('SOYUZ',['LAUNCHERDELUXELAKESIDE']),
        }
        for title,expected in cases.items():
            with self.subTest(title=title):self.assertEqual(app.identity(title),expected)

    def test_multiple_variants_remain_ambiguous(self):
        for text in ['Radial StageBug SB-1/4/6 DI盒','CME Xkey Air 25/37键蓝牙',
                     'Yamaha IXO12/22 声卡','ESI NEVA UNO / DUO 录音声卡 USB3.1']:
            with self.subTest(text=text):
                target=strategy.plan({'title':text},app.identity)
                self.assertGreater(len(target['models']),1)
                self.assertLessEqual(len(target['queries']),3)
                title=target['brand']+' '+target['models'][0]
                result=strategy.evaluate(self.entry(title),target,app.identity,app.BRANDS)
                self.assertFalse(result['auto_select'])

    def test_suffix_generation_and_comparison_safety(self):
        for model,title in [('SM58','Shure SM58S'),('C414XLII','AKG C414 XLS'),('NT1','Rode NT1-A'),
                            ('MS5V2','Midiplus MS5 V3'),('U87','Neumann U87 Ai')]:
            with self.subTest(title=title):self.assertFalse(app.exact_model(model,title))
        result=strategy.evaluate(self.entry('Xvive AT-1 vs AT-2'),{'brand':'Xvive','models':['AT1']},app.identity,app.BRANDS)
        self.assertFalse(result['auto_select'])
        self.assertIn('对比',result['match_reason'])

    def test_longer_correct_video_is_eligible_but_unknown_is_not(self):
        target={'brand':'Xvive','models':['AT1']}
        for seconds in [257,674,900]:
            result=strategy.evaluate(self.entry(duration=seconds),target,app.identity,app.BRANDS)
            self.assertTrue(result['auto_select'])
            self.assertGreaterEqual(result['score'],80)
        for seconds in [0,4,901,float('nan')]:
            self.assertFalse(strategy.evaluate(self.entry(duration=seconds),target,app.identity,app.BRANDS)['auto_select'])
        for extra in [{'is_live':True},{'availability':'private'},{'title':'Other Brand AT-1 audio tester'}]:
            self.assertFalse(strategy.evaluate(dict(self.entry(),**extra),target,app.identity,app.BRANDS)['auto_select'])

    def test_multiround_dedup_cache_and_diagnostics(self):
        e=self.editor();task=self.task(e)
        outputs=[{'entries':[self.entry('Other product',vid='11111111111')]},
                 {'entries':[self.entry(),self.entry()]}]
        cache=self.folder/'rounds'
        with patch.object(app,'DATA',cache),patch.object(e,'command',side_effect=[json.dumps(o) for o in outputs]) as command:
            result=e.search(task)
            self.assertEqual(command.call_count,2)
            self.assertTrue(any('ytsearch12:' in arg for arg in command.call_args.args[0]))
            self.assertEqual(len(result),2)
            self.assertTrue(result[0]['auto_select'])
            self.assertEqual(task['search_report']['unique_candidates'],2)
        with patch.object(app,'DATA',cache),patch.object(e,'command') as command:
            self.assertEqual(e.search(task)[0]['url'],result[0]['url']);command.assert_not_called()

    def test_query_failure_does_not_discard_other_results(self):
        e=self.editor();task=self.task(e)
        with patch.object(app,'DATA',self.folder/'fallback'),patch.object(e,'command',side_effect=[RuntimeError('temporary error'),json.dumps({'entries':[self.entry()]})]):
            result=e.search(task)
        self.assertTrue(result[0]['auto_select'])
        self.assertIn('error',task['search_report']['attempts'][0])

    def test_negative_cache_expires_and_corrupt_cache_recovers(self):
        e=self.editor();task=self.task(e)
        cache=self.folder/'negative'/'search_cache_v2';cache.mkdir(parents=True)
        target=strategy.plan(task,app.identity)
        for q in target['queries']:
            key=hashlib.sha256((str(strategy.VERSION)+q).encode()).hexdigest()[:24]
            (cache/(key+'.json')).write_text(json.dumps({'version':strategy.VERSION,'created':time.time()-601,'candidates':[]}),encoding='utf-8')
        with patch.object(app,'DATA',cache.parent),patch.object(e,'command',return_value=json.dumps({'entries':[dict(self.entry(),channel='Xvive')]})) as command:
            self.assertTrue(e.search(task)[0]['auto_select']);self.assertEqual(command.call_count,1)
        first=next(cache.glob('*.json'));first.write_text('broken',encoding='utf-8')
        with patch.object(app,'DATA',cache.parent),patch.object(e,'command',return_value=json.dumps({'entries':[]})):
            self.assertIsInstance(e.search(task),list)

    def test_all_network_failures_are_reported_as_network(self):
        e=self.editor();task=self.task(e)
        with patch.object(app,'DATA',self.folder/'errors'),patch.object(e,'command',side_effect=RuntimeError('connection error')) as command:
            with self.assertRaisesRegex(RuntimeError,'连接失败'):e.search(task)
            self.assertEqual(command.call_count,3)
            self.assertEqual(len(task['search_report']['attempts']),3)

    def test_custom_search_name_and_restart_no_title_change(self):
        e=self.editor();e.board.paste({'text':'Radial SB-1/4/6 DI盒'})
        p=e.state['products'][0];title=p['title']
        e.board.set_search_name(p['id'],{'name':'Radial SB-4'})
        e=app.Editor(e.state_file,start_worker=False);p=e.state['products'][0]
        task=e.board.new_task(p,'collect')
        self.assertEqual(strategy.plan(task,app.identity)['models'],['SB4'])
        self.assertEqual(p['title'],title)
        with self.assertRaises(ValueError):e.board.set_search_name(p['id'],{'name':'Radial SB-1'})
        task['status']='needs_review'
        for invalid in ['https://youtube.com','x','x\ny',None,'x'*161]:
            with self.subTest(value=invalid),self.assertRaises(ValueError):e.board.set_search_name(p['id'],{'name':invalid})
        before=copy.deepcopy(p)
        with patch.object(e,'save',side_effect=OSError('disk full')),self.assertRaises(OSError):e.board.set_search_name(p['id'],{'name':'Radial SB-6'})
        self.assertEqual(p,before)
        e.board.set_search_name(p['id'],{'name':''})
        self.assertEqual(p['search_name'],'')

    def test_collect_researches_and_preserves_old_history(self):
        e=self.editor();e.board.paste({'text':'Xvive AT-1'})
        p=e.state['products'][0]
        e.board.set_source(p['id'],{'url':'https://youtu.be/11111111111'})
        t=e.board.new_task(p,'collect');self.assertFalse(t['selected_url'])
        candidate=dict(self.entry(),score=80,auto_select=True)
        with patch.object(e,'search',return_value=[candidate]) as search,patch.object(e,'command') as command:
            e.process(t);search.assert_called_once();command.assert_not_called()
        self.assertEqual(t['status'],'links_ready')
        self.assertEqual(p['source_url'],candidate['url'])

    def test_fade_after_all_overlays_and_joined_audio(self):
        graph=renderer.make_graph([(1,3),(7,10)],1)
        self.assertIn('fade=t=out:st=4.000000:d=1.000000',graph)
        self.assertIn('afade=t=out:st=4.000000:d=1.000000',graph)
        self.assertGreater(graph.index(',fade='),graph.index('subtitles='))
        self.assertIn('[ajoin]afade',graph)
        self.assertNotIn('afade',renderer.make_graph([(0,3)],None))
        preview=renderer.make_graph([(0,1)],None,fade_out_seconds=0)
        self.assertNotIn('fade=',preview)
        self.assertIn('st=0.000000:d=0.500000',renderer.make_graph([(0,.5)],0))
        for invalid in [True,-1,4,float('nan'),'1']:
            with self.subTest(value=invalid),self.assertRaises(ValueError):renderer.make_graph([(0,3)],0,invalid)


if __name__=='__main__':unittest.main(verbosity=2)
