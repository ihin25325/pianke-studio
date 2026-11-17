"""Offline contract/security tests. Fake keys only; never call a paid API.

SNAPANY_CDN_SMOKE=1 additionally downloads a real public-domain CC0 file,
using a fixture API response (not the SnapAny API) and the production worker.
"""
import copy
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch, Mock
from urllib.request import Request, urlopen
from urllib.error import HTTPError
import editor_server as app
import snapany as api
import snapany_worker as worker

FAKE = 'sk_snapany_local_test_not_a_real_key'
BILI = 'https://www.bilibili.com/video/BV1xx411c7mD/'
FLOWER = 'https://interactive-examples.mdn.mozilla.net/media/cc0-videos/flower.mp4'


def post():
    return {'title': '测试商品', 'medias': [{'media_type': 'video', 'duration': 5.005, 'resource_url': FLOWER,
                                        'headers': {'User-Agent': '', 'Referer': 'https://example.com/'}}]}


class SnapAnyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        folder = app.ROOT / 'work/snapany_tests'
        folder.mkdir(parents=True, exist_ok=True)
        cls.root = Path(tempfile.mkdtemp(prefix='run_', dir=folder))

    def editor(self):
        return app.Editor(self.root / self._testMethodName / 'queue.json', start_worker=False)

    def connected(self, e, mode='off', limit=10):
        e.snapany.save({'api_key': FAKE, 'daily_limit': limit})
        with patch.object(api, 'request_json', return_value={'balance': 50}) as request:
            e.snapany.check()
            self.assertEqual(request.call_args.args[1], '/credits/balance')
        if mode != 'off':
            e.snapany.save({'mode': mode, 'consent': True})

    def test_save_encryption_no_network_snapshot_restart(self):
        e = self.editor()
        with patch.object(api, 'request_json', side_effect=AssertionError('Saving must not call network')):
            s = e.snapany.save({'api_key': FAKE})
        self.assertTrue(s['key_saved'])
        self.assertEqual(s['mode'], 'off')
        self.assertNotIn(FAKE, e.snapany.config_file.read_text(encoding='utf-8'))
        self.assertNotIn(FAKE, json.dumps(e.snapshot()))
        self.assertNotIn('secret', json.dumps(s))
        again = app.Editor(e.state_file, start_worker=False)
        self.assertEqual(again.snapany.key(again.snapany.config()), FAKE)

    def test_modes_validation_consent_and_reset(self):
        e = self.editor()
        with self.assertRaises(ValueError):
            e.snapany.save({'api_key': 'sk-not-snapany-key'})
        with self.assertRaises(ValueError):
            e.snapany.save({'mode': 'prefer', 'consent': True})
        self.connected(e)
        for invalid in [{'mode': 'prefer'}, {'daily_limit': 101}, {'daily_limit': True}, {'endpoint': 'https://evil.com'}, {'proxy': True}]:
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                e.snapany.save(invalid)
        e.snapany.save({'mode': 'fallback', 'consent': True})
        self.assertEqual(e.snapany.mode(), 'fallback')
        e.snapany.save({'endpoint': 'global'})
        self.assertEqual(e.snapany.mode(), 'off')
        self.assertFalse(e.snapany.snapshot()['verified'])
        e.snapany.save({'clear_key': True})
        self.assertFalse(e.snapany.snapshot()['key_saved'])

    def test_real_dpapi_and_write_failure_keeps_previous_configuration(self):
        e = self.editor()
        self.connected(e)
        before = e.snapany.config_file.read_bytes()
        with patch.object(api, 'write_json', side_effect=OSError('disk full')), self.assertRaises(OSError):
            e.snapany.save({'daily_limit': 12})
        self.assertEqual(e.snapany.config_file.read_bytes(), before)

    def test_balance_free_check_schema_and_key_race(self):
        e = self.editor()
        e.snapany.save({'api_key': FAKE})
        for value in [{'balance': '50'}, {'balance': -1}, {'balance': True}, {}]:
            with patch.object(api, 'request_json', return_value=value), self.assertRaises(ValueError):
                e.snapany.check()
        def change(*args):
            e.snapany.save({'clear_key': True})
            return {'balance': 50}
        with patch.object(api, 'request_json', side_effect=change), self.assertRaises(ValueError):
            e.snapany.check()

    def test_api_request_contract_key_not_forwarded_on_redirect(self):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = b'{"balance":50}'
        opener = Mock()
        opener.open.return_value = response
        with patch.object(api, 'build_opener', return_value=opener) as build:
            self.assertEqual(api.request_json('cn', '/credits/balance', FAKE)['balance'], 50)
            request = opener.open.call_args.args[0]
            self.assertEqual(request.full_url, 'https://api-cn.snapany.com/openapi/v1/credits/balance')
            self.assertEqual(request.get_header('Authorization'), 'Bearer ' + FAKE)
            self.assertIsNone(request.data)
            self.assertTrue(any(isinstance(x, api.NoRedirect) for x in build.call_args.args))
            api.request_json('global', '/extract/post', FAKE, {'url': BILI})
            self.assertEqual(json.loads(opener.open.call_args.args[0].data), {'url': BILI})
        with self.assertRaises(ValueError):
            api.request_json('global', '/youtube/proxy-download', FAKE)
        with self.assertRaises(ValueError):
            api.request_json('https://evil.com', '/extract/post', FAKE)

    def test_api_error_redacted_no_retry(self):
        for code in [400, 401, 402, 429, 500, 302]:
            opener = Mock()
            opener.open.side_effect = HTTPError('https://api.snapany.com', code, FAKE, {}, io.BytesIO(FAKE.encode()))
            with patch.object(api, 'build_opener', return_value=opener), self.assertRaises(ValueError) as error:
                api.request_json('global', '/extract/post', FAKE, {'url': BILI})
            self.assertNotIn(FAKE, str(error.exception))
            self.assertEqual(opener.open.call_count, 1)

    def test_manual_requires_consent_and_private_url_never_calls(self):
        e = self.editor()
        self.connected(e)
        with patch.object(api, 'request_json', side_effect=AssertionError('No call')):
            for url, consent in [(BILI, False), ('https://127.0.0.1/video', True), ('file:///D:/test', True)]:
                with self.subTest(url=url), self.assertRaises(ValueError):
                    e.snapany.preview({'url': url, 'consent': consent})
        self.assertEqual(e.snapany.snapshot()['today_calls'], 0)

    def test_extract_cache_no_signed_urls_in_ledger_and_no_duplicate_fee(self):
        e = self.editor()
        self.connected(e)
        with patch.object(api, 'request_json', return_value=post()) as request:
            first = e.snapany.preview({'url': BILI, 'consent': True})
            second = e.snapany.preview({'url': BILI, 'consent': True})
        self.assertFalse(first['cached'])
        self.assertTrue(second['cached'])
        self.assertEqual(request.call_count, 1)
        self.assertEqual(e.snapany.snapshot()['today_calls'], 1)
        for value in [json.dumps(first), e.snapany.ledger_file.read_text(encoding='utf-8')]:
            self.assertNotIn(FLOWER, value)
            self.assertNotIn('Referer', value)
            self.assertNotIn(FAKE, value)

    def test_cache_expires_and_failed_requests_count_toward_cap(self):
        e = self.editor()
        self.connected(e, limit=1)
        with patch.object(api, 'request_json', side_effect=ValueError('timeout')) as request:
            for _ in range(2):
                with self.assertRaises(ValueError):
                    e.snapany.preview({'url': BILI, 'consent': True})
            self.assertEqual(request.call_count, 1)
        self.assertEqual(e.snapany.snapshot()['recent_calls'][0]['status'], 'failed_or_uncertain')
        e.snapany.save({'daily_limit': 3})
        with patch.object(api, 'request_json', return_value=post()) as request:
            e.snapany.preview({'url': BILI, 'consent': True})
            for key, value in list(e.snapany.cache.items()):
                e.snapany.cache[key] = (0, value[1])
            self.assertFalse(e.snapany.preview({'url': BILI, 'consent': True})['cached'])
            self.assertEqual(request.call_count, 2)

    def test_ledger_corruption_fails_closed(self):
        e = self.editor()
        self.connected(e)
        e.snapany.ledger_file.write_text('{}', encoding='utf-8')
        with patch.object(api, 'request_json', side_effect=AssertionError('No call')), self.assertRaises(ValueError):
            e.snapany.preview({'url': BILI, 'consent': True})

    def test_preview_then_enable_download_reuses_paid_result(self):
        e = self.editor()
        self.connected(e)
        with patch.object(api, 'request_json', return_value=post()) as request, patch.object(e.snapany, '_download'):
            e.snapany.preview({'url': BILI, 'consent': True})
            e.snapany.save({'mode': 'prefer', 'consent': True})
            local = Mock(side_effect=AssertionError('Must use selected API mode'))
            task = {}
            e.snapany.download(task, BILI, self.root, local)
            self.assertEqual(request.call_count, 1)
            self.assertTrue(task['snapany_cached'])

    def test_multiple_media_live_drm_duration_rejected(self):
        cases = [{'medias': []}, {'medias': [{'media_type': 'image'}]},
                 {'medias': [{'media_type': 'live'}]}, {'has_drm': True, **post()},
                 {'medias': post()['medias'] * 2}]
        for duration in [float('nan'), 901, -1, '5']:
            value = post()
            value['medias'][0]['duration'] = duration
            cases.append(value)
        for value in cases:
            with self.subTest(value=value), self.assertRaises(ValueError):
                api.video_summary(value, BILI)

    def test_choose_1080_and_merge_separate_audio_preserve_empty_header(self):
        value = post()
        value['medias'][0]['variants'] = [
            {'quality': 2160, 'video_url': 'https://example.com/4k'},
            {'quality': 1080, 'video_url': 'https://example.com/v?sig=a%2Fb&spm=needed', 'audio_url': 'https://example.com/a'},
            {'quality': 720, 'video_url': 'https://example.com/720'}]
        _, urls, headers = worker.choose(value, BILI)
        self.assertEqual(urls, ['https://example.com/v?sig=a%2Fb&spm=needed', 'https://example.com/a'])
        self.assertEqual(headers['User-Agent'], '')

    def test_media_headers_urls_and_redirect_safety(self):
        for headers in [{'Host': '127.0.0.1'}, {'X-Name': 'a\r\nb'}, {'Authorization': 'Bearer ' + FAKE}, {'Range': 'bytes=0-'}, {'A': None}]:
            with self.subTest(headers=headers), self.assertRaises(ValueError):
                worker.media_headers(headers)
        for url in ['http://example.com/a', 'https://127.0.0.1/a', 'https://nas.local/a', 'https://user:pass@example.com/a']:
            with self.assertRaises(ValueError):
                worker.media_url(url)
        redirect = worker.MediaRedirect()
        req = Request('https://example.com/video', headers={'Authorization': 'abc', 'Cookie': 'session=1', 'User-Agent': ''})
        moved = redirect.redirect_request(req, None, 302, '', {}, 'https://cdn.example.com/file')
        self.assertIsNone(moved.get_header('Authorization'))
        self.assertIsNone(moved.get_header('Cookie'))
        self.assertEqual(moved.get_header('User-agent'), '')
        with self.assertRaises(ValueError):
            redirect.redirect_request(req, None, 302, '', {}, 'https://127.0.0.1/file')

    def test_stream_rejects_manifest_size_and_preserves_existing(self):
        folder = self.root / self._testMethodName
        folder.mkdir()
        for content_type, length, raw in [('text/html', '2', b'ab'), ('video/mp4', str(worker.MAX_BYTES + 1), b'x'),
                                           ('video/mp4', '8', b'#EXTM3U')]:
            response = Mock()
            response.__enter__ = Mock(return_value=response)
            response.__exit__ = Mock(return_value=False)
            response.geturl.return_value = FLOWER
            response.headers = {'Content-Type': content_type, 'Content-Length': length}
            response.read.side_effect = [raw, b'']
            opener = Mock()
            opener.open.return_value = response
            with self.assertRaises(ValueError):
                worker.stream_file(FLOWER, {}, folder / str(len(list(folder.iterdir()))), [0], time.monotonic()+10, opener)
        target = folder / 'existing'
        target.write_bytes(b'original')
        response.headers = {}
        with self.assertRaises(FileExistsError):
            worker.stream_file(FLOWER, {}, target, [0], time.monotonic()+10, opener)
        self.assertEqual(target.read_bytes(), b'original')
        with self.assertRaises(ValueError):
            worker.fetch({'url': BILI, 'folder': str(app.ROOT / 'web'), 'post': post()})

    def test_modes_route_to_download_and_cached_source_does_not_pay(self):
        e = self.editor()
        local = Mock(return_value='local')
        with patch.object(api, 'request_json', side_effect=AssertionError('No pay')):
            self.assertEqual(e.snapany.download({}, BILI, self.root, local), 'local')
        self.connected(e, mode='fallback')
        local.side_effect = RuntimeError('HTTP 412')
        with patch.object(api, 'request_json', return_value=post()) as request, patch.object(e.snapany, '_download') as download:
            e.snapany.download({}, BILI, self.root, local)
            self.assertEqual(request.call_count, 1)
            self.assertEqual(download.call_count, 1)
        e.snapany.save({'mode': 'prefer', 'consent': True})
        with patch.object(api, 'request_json', return_value=post()), patch.object(e.snapany, '_download'), patch.object(e, 'update'):
            local.reset_mock()
            e.snapany.download({}, BILI, self.root, local)
            local.assert_not_called()
        e.board.paste({'text': 'Shure SM7B 麦克风'})
        p = e.state['products'][0]
        e.board.set_source(p['id'], {'url': BILI})
        task = e.board.new_task(p, 'download')
        with patch.object(e, 'existing_source', return_value=Path(__file__)), patch.object(e.snapany, 'download', side_effect=AssertionError('Reuses source')):
            e.process(task)
        self.assertEqual(task['status'], 'downloaded')

    def test_download_path_in_queue_process(self):
        e = self.editor()
        self.connected(e, mode='fallback')
        e.board.paste({'text': 'Shure SM7B 麦克风'})
        p = e.state['products'][0]
        e.board.set_source(p['id'], {'url': BILI})
        task = e.board.new_task(p, 'download')
        def downloaded(url, folder, data):
            (folder / 'source.mp4').write_bytes(b'test mock only')
        with patch.object(app, 'ROOT', self.root / 'sandbox'), patch.object(e, 'existing_source', return_value=None), \
             patch.object(e, 'command', side_effect=RuntimeError('HTTP 412')), \
             patch.object(api, 'request_json', return_value=post()), patch.object(e.snapany, '_download', side_effect=downloaded):
            e.process(task)
        self.assertEqual(task['status'], 'downloaded')
        self.assertEqual(task['download_provider'], 'snapany')

    def test_http_routes_secret_not_served_origin_enforced(self):
        e = self.editor()
        server = app.ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)
        with patch.object(app, 'EDITOR', e), patch.object(app, 'PORT', server.server_port):
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = 'http://127.0.0.1:' + str(server.server_port)
            def request(path, value, origin=None):
                return Request(base+path, data=json.dumps(value).encode(), headers={'Origin': origin or base, 'Content-Type': 'application/json'})
            try:
                with urlopen(base+'/api/snapany') as r:
                    self.assertEqual(json.load(r)['mode'], 'off')
                with self.assertRaises(HTTPError) as error:
                    urlopen(request('/api/snapany/settings', {'api_key': FAKE}, 'https://evil.example'))
                self.assertEqual(error.exception.code, 403)
                with urlopen(request('/api/snapany/settings', {'api_key': FAKE})) as r:
                    self.assertTrue(json.load(r)['key_saved'])
                with patch.object(api, 'request_json', return_value={'balance': 50}), urlopen(request('/api/snapany/check', {})) as r:
                    self.assertTrue(json.load(r)['verified'])
                with patch.object(api, 'request_json', return_value=post()), urlopen(request('/api/snapany/extract', {'url': BILI, 'consent': True})) as r:
                    self.assertNotIn(FLOWER, r.read().decode())
                for path in ['/data/editor/private_api/snapany.json', '/private_api/snapany.json']:
                    with self.assertRaises(HTTPError):
                        urlopen(base+path)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)

    @unittest.skipUnless(os.environ.get('SNAPANY_CDN_SMOKE') == '1', 'Optional real CC0 CDN download; no SnapAny API call')
    def test_real_cdn_worker_download_validate_decode_and_no_overwrite(self):
        import product_render as renderer
        e = self.editor()
        folder = e.state_file.parent / 'cdn'
        result = e.snapany._download(BILI, folder, post())
        self.assertTrue(result['ok'])
        output = folder / 'source.mp4'
        meta = json.loads((folder/'source.info.json').read_text(encoding='utf-8'))
        self.assertEqual(meta['_workbench_source_url'], BILI)
        self.assertNotIn(FLOWER, json.dumps(meta))
        self.assertAlmostEqual(meta['duration'], 5.005, delta=.1)
        digest = __import__('hashlib').sha256(output.read_bytes()).hexdigest()
        full = subprocess.run([str(renderer.FFMPEG), '-v', 'error', '-i', str(output), '-f', 'null', '-'], capture_output=True, timeout=30)
        self.assertEqual(full.returncode, 0)
        with self.assertRaises(ValueError):
            e.snapany._download(BILI, folder, post())
        self.assertEqual(__import__('hashlib').sha256(output.read_bytes()).hexdigest(), digest)
        proof = {'output': str(output), 'duration': meta['duration'], 'decoded': True, 'overwrite_refused': True,
                 'snapany_api_calls': 0, 'note': 'Real CC0 CDN media with fixture API response; not a real SnapAny API test.'}
        (self.root/'cdn_proof.json').write_text(json.dumps(proof, ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    unittest.main()
