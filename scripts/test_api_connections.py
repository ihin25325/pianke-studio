"""All external calls mocked. Fake keys only; no paid/model requests."""
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
import uuid

import editor_server as app
import api_connections as api

FAKE='sk-local-test-not-valid-network'


class ApiConnectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        base=app.ROOT/'work/api_connection_tests';base.mkdir(parents=True,exist_ok=True)
        cls.folder=Path(tempfile.mkdtemp(prefix='run_',dir=base))

    def editor(self):
        e=app.Editor(self.folder/self._testMethodName/'queue.json',start_worker=False)
        return e

    def connected(self,e,enabled=False):
        e.connections.save({'api_key':FAKE})
        with patch.object(api,'request_json',return_value={'data':[{'id':api.MODEL,'input_modalities':['text','image']}]}):e.connections.check()
        if enabled:e.connections.save({'enabled':True,'consent_upload':True})

    def report(self):
        from PIL import Image
        key=uuid.uuid4().hex*2;folder=app.ROOT/'data/director_cache'/key;folder.mkdir(parents=True)
        frames=[]
        for i in range(5):
            name=f'frame_{i+1:03}.jpg';Image.new('RGB',(240,135),(40+i*20,80,100)).save(folder/name)
            frames.append({'file':name,'time':i*10,'score':70+i})
        first={'start':0,'end':30,'score':70,'blocked_ratio':0};second={'start':10,'end':40,'score':69,'blocked_ratio':0}
        return {'key':key,'frames':frames,'selected':first,'alternatives':[first,second],'warnings':[],
                'needs_review':False,'cover_time':20,'cover_source_frame':frames[2]['file'],'reason':'local','api_calls':0}

    def response(self,**changes):
        value={'summary':'商品主体清晰','window_index':1,'cover_index':2,'needs_review':False,'warnings':[]};value.update(changes)
        return {'choices':[{'message':{'content':json.dumps(value,ensure_ascii=False)}}],
                'usage':{'prompt_tokens':100,'completion_tokens':20,'total_tokens':120,'secret':'must not save'}}

    @unittest.skipUnless(os.name=='nt','Windows DPAPI')
    def test_dpapi_real_roundtrip_and_wrong_blob(self):
        encrypted=api.crypt(FAKE.encode());self.assertNotIn(FAKE.encode(),encrypted)
        self.assertEqual(api.crypt(encrypted,True),FAKE.encode())
        with self.assertRaises(ValueError):api.crypt(b'invalid',True)

    def test_save_encrypted_no_network_and_restart(self):
        e=self.editor()
        with patch.object(api,'request_json',side_effect=AssertionError('No network when saving')):
            value=e.connections.save({'api_key':FAKE})
        self.assertTrue(value['key_saved']);self.assertFalse(value['enabled']);self.assertIsNone(e.connections.policy())
        self.assertNotIn(FAKE,e.connections.config_file.read_text(encoding='utf-8'))
        self.assertNotIn(FAKE,json.dumps(e.snapshot()));self.assertNotIn('secret',json.dumps(value))
        restarted=app.Editor(e.state_file,start_worker=False)
        self.assertEqual(restarted.connections.key(restarted.connections.config()),FAKE)

    def test_consent_and_model_verification(self):
        e=self.editor();e.connections.save({'api_key':FAKE})
        with self.assertRaises(ValueError):e.connections.save({'enabled':True,'consent_upload':True})
        with patch.object(api,'request_json',return_value={'data':[{'id':'deepseek-v4-pro','input_modalities':['text']}]}),self.assertRaises(ValueError):e.connections.check()
        self.connected(e)
        with self.assertRaises(ValueError):e.connections.save({'enabled':True})
        with self.assertRaises(ValueError):e.connections.save({'daily_limit':True})
        with self.assertRaises(ValueError):e.connections.save({'base_url':'https://evil.test'})
        e.connections.save({'enabled':True,'consent_upload':True})
        self.assertEqual(e.connections.policy()['provider'],'deepseek')
        e.connections.save({'clear_key':True})
        self.assertFalse(e.connections.snapshot()['key_saved']);self.assertIsNone(e.connections.policy())

    def test_disabled_and_old_jobs_do_not_call(self):
        e=self.editor();r=self.report();before=copy.deepcopy(r)
        with patch.object(api,'request_json',side_effect=AssertionError('No cloud')):
            self.assertEqual(e.connections.enhance({},r),before)
            self.assertEqual(e.connections.enhance({'cloud_policy':{'consent_upload':True}},r)['selected'],before['selected'])
            with self.assertRaises(ValueError):e.connections._assess(r,'product','manual',False)

    def test_vision_request_bounded_and_usage_sanitized(self):
        e=self.editor();self.connected(e);report=self.report()
        with patch.object(api,'request_json',return_value=self.response()) as call:
            result=e.connections._assess(report,'Audio interface','manual',True)
        self.assertEqual(result['selected']['start'],10);self.assertEqual(result['cover_time'],20)
        self.assertEqual(result['usage']['total_tokens'],120);self.assertNotIn('secret',result['usage'])
        path,key,body=call.call_args.args;self.assertEqual(path,'/chat/completions');self.assertEqual(body['model'],api.MODEL)
        self.assertEqual(body['thinking']['type'],'disabled');self.assertEqual(body['max_tokens'],1000)
        self.assertNotIn('tools',body);self.assertEqual(e.connections.snapshot()['today_calls'],1)
        self.assertNotIn(FAKE,e.connections.ledger_file.read_text(encoding='utf-8'))

    def test_invalid_model_response_counts_once_no_retry(self):
        e=self.editor();self.connected(e,True);report=self.report()
        with patch.object(api,'request_json',return_value=self.response(window_index=999)) as call:
            result=e.connections.enhance({'cloud_policy':e.connections.policy()},report)
        self.assertEqual(call.call_count,1);self.assertEqual(result['cloud']['status'],'fallback')
        self.assertEqual(result['selected']['start'],0);self.assertEqual(e.connections.snapshot()['today_tokens'],120)
        self.assertEqual(e.connections.ledger()[-1]['status'],'failed')

    def test_daily_cap_blocks_extra_request(self):
        e=self.editor();self.connected(e);e.connections.save({'daily_limit':1});report=self.report()
        with patch.object(api,'request_json',return_value=self.response()) as call:
            e.connections._assess(report,'product','manual',True)
            with self.assertRaises(ValueError):e.connections._assess(report,'product','manual',True)
        self.assertEqual(call.call_count,1)

    def test_cloud_cannot_override_known_bad_or_manual_cover(self):
        e=self.editor();self.connected(e);report=self.report();report['frames'][2]['watermark']=True
        with patch.object(api,'request_json',return_value=self.response()):result=e.connections._assess(report,'product','manual',True)
        self.assertNotEqual(result['cover_time'],20);self.assertTrue(result['warnings'])

    def test_only_new_smart_jobs_snapshot_opt_in(self):
        e=self.editor();e.board.paste({'text':'Neumann U87 Ai | 录音电容麦克风 | https://www.youtube.com/watch?v=FlT0bAc9Xp4'})
        e.studio.save({'mode':'smart30'})
        p=e.state['products'][0];e.studio.jobs({'ids':[p['id']]});old=e.state['tasks'][-1];old['status']='failed'
        self.connected(e,True);e.studio.jobs({'ids':[p['id']]});new=e.state['tasks'][-1]
        self.assertNotIn('cloud_policy',old);self.assertEqual(new['cloud_policy']['provider'],'deepseek');new['status']='failed'
        e.studio.jobs({'ids':[p['id']],'options':{'mode':'manual'}})
        self.assertNotIn('cloud_policy',e.state['tasks'][-1]);self.assertNotIn(FAKE,e.state_file.read_text(encoding='utf-8'))

    def test_http_origin_and_private_files(self):
        e=self.editor();server=app.ThreadingHTTPServer(('127.0.0.1',0),app.Handler)
        with patch.object(app,'EDITOR',e),patch.object(app,'PORT',server.server_port):
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();base='http://127.0.0.1:'+str(server.server_port)
            try:
                with urlopen(base+'/api-settings') as r:self.assertIn('DeepSeek',r.read().decode())
                with self.assertRaises(HTTPError) as error:urlopen(Request(base+'/api/connections',data=json.dumps({'api_key':FAKE}).encode(),headers={'Content-Type':'application/json','Origin':'https://evil.test'}))
                self.assertEqual(error.exception.code,403);self.assertFalse(e.connections.config_file.exists())
                with urlopen(Request(base+'/api/connections',data=json.dumps({'api_key':FAKE}).encode(),headers={'Content-Type':'application/json','Origin':base})) as r:result=json.load(r)
                self.assertTrue(result['key_saved']);self.assertNotIn(FAKE,json.dumps(result))
                with urlopen(base+'/api/connections') as r:self.assertNotIn(FAKE,r.read().decode())
                with self.assertRaises(HTTPError) as error:urlopen(base+'/data/editor/private_api/deepseek.json')
                self.assertEqual(error.exception.code,404)
            finally:server.shutdown();server.server_close();thread.join(3)


if __name__=='__main__':unittest.main()
