"""Windows launcher, safe stop and packaging boundaries; no business data or APIs."""
import hashlib
from http.server import ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request,urlopen
import zipfile

import build_windows as build
import editor_server as app
import release_check
import verify_windows_installer as installer_verify


class WindowsDistributionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        folder=app.ROOT/'work/windows_distribution_tests'
        folder.mkdir(parents=True,exist_ok=True)
        cls.root=Path(tempfile.mkdtemp(prefix='run_',dir=folder))

    def test_official_runtime_lock_and_release_inputs(self):
        config=json.loads(build.LOCK.read_text(encoding='utf-8'))
        self.assertTrue(config['python']['url'].startswith('https://www.python.org/ftp/python/'))
        self.assertRegex(config['python']['sha256'],r'^[a-f0-9]{64}$')
        self.assertTrue(config['node']['url'].startswith('https://nodejs.org/dist/'))
        self.assertTrue(all('==' in p for p in config['wheels']))
        names={p.relative_to(app.ROOT).as_posix() for p in release_check.release_files()}
        self.assertTrue({'packaging/Launcher.cs','packaging/PiankeStudio.iss','packaging/launcher.manifest',
                         'packaging/windows-runtime.json','scripts/launcher_server.py'}<=names)

    def archive(self,name,entries):
        path=self.root/(name+'.zip')
        with zipfile.ZipFile(path,'w') as out:
            for key,value in entries:
                row=zipfile.ZipInfo(key);row.filename=key
                out.writestr(row,value)
        return path

    def test_unpack_checksum_and_no_overwrite(self):
        archive=self.archive('valid',[('python.exe',b'fixture')])
        expected=hashlib.sha256(archive.read_bytes()).hexdigest()
        destination=self.root/'unpack-valid'
        with self.assertRaises(ValueError):build.unpack(archive,destination,'0'*64)
        self.assertFalse(destination.exists())
        build.unpack(archive,destination,expected)
        with self.assertRaises(ValueError):build.unpack(archive,destination,expected)
        self.assertEqual((destination/'python.exe').read_bytes(),b'fixture')

    def test_archive_traversal_duplicate_case_and_symlink_rejected(self):
        for n,entries in enumerate([[('../escape',b'x')],[('C:/escape',b'x')],[('a\\b',b'x')],[('A',b'x'),('a',b'y')]]):
            archive=self.archive('unsafe-'+str(n),entries)
            with self.subTest(entries=entries),self.assertRaises(ValueError):
                build.unpack(archive,self.root/('unsafe-'+str(n)),build.digest(archive))
        archive=self.root/'symlink.zip'
        with zipfile.ZipFile(archive,'w') as out:
            info=zipfile.ZipInfo('link');info.external_attr=(0o120777<<16);out.writestr(info,'elsewhere')
        with self.assertRaises(ValueError):build.unpack(archive,self.root/'unsafe-link',build.digest(archive))

    def test_embedded_python_uses_only_project_relative_paths(self):
        folder=self.root/'embedded';folder.mkdir()
        (folder/'python314.zip').write_bytes(b'fixture')
        (folder/'python314._pth').write_text('old',encoding='utf-8')
        build.configure_python(folder)
        text=(folder/'python314._pth').read_text(encoding='utf-8')
        self.assertIn('../../scripts',text);self.assertIn('site-packages',text)
        self.assertNotIn(str(app.ROOT),text)
        self.assertNotIn('codex-runtimes',text)

    def test_private_bundle_paths_rejected(self):
        for n,name in enumerate(['data/editor/queue.json','sources/original.mp4','configs/storage.json','work/job/log.txt','.env']):
            root=self.root/('boundary-'+str(n));path=root/name;path.parent.mkdir(parents=True);path.write_text('fixture')
            with self.subTest(name=name),self.assertRaises(ValueError):build.assert_clean(root)
        clean=self.root/'boundary-clean';(clean/'configs').mkdir(parents=True)
        (clean/'configs/defaults.json').write_text('{}')
        build.assert_clean(clean)

    def test_launcher_compile_and_no_foreign_process_stop(self):
        if os.name!='nt':self.skipTest('Windows .NET launcher')
        root=self.root/'compiled'
        exe=build.compile_launcher(root)
        result=subprocess.run([str(exe),'--self-test-core'],timeout=30)
        self.assertEqual(result.returncode,0)
        self.assertEqual(subprocess.run([str(exe),'--can-update'],timeout=10).returncode,0)
        self.assertEqual(subprocess.run([str(exe),'--render-preview'],timeout=20).returncode,0)
        self.assertGreater((root/'work/launcher/launcher-preview.png').stat().st_size,1000)
        from PIL import Image
        with Image.open(root/'work/launcher/launcher-preview.png') as image:
            pixels=image.convert('RGB')
            values=pixels.get_flattened_data() if hasattr(pixels,'get_flattened_data') else pixels.getdata()
            self.assertGreater(sum(b-r>80 for r,g,b in values),1000)
        self.assertFalse((root/'data/editor/queue.json').exists())

    def test_launcher_stop_refuses_foreign_busy_and_exports(self):
        e=app.Editor(self.root/'safe-stop/queue.json',start_worker=False)
        before=e.state_file.read_bytes()
        with patch.dict(os.environ,{'PIANKE_LAUNCHER_ID':'owned-instance'}):
            with self.assertRaises(ValueError):e.launcher_stop('foreign')
            e.worker_busy=True
            with self.assertRaises(ValueError):e.launcher_stop('owned-instance')
            e.worker_busy=False;e.state['export_batches']=[{'status':'building'}]
            with self.assertRaises(ValueError):e.launcher_stop('owned-instance')
            e.state['export_batches']=[]
            e.launcher_stop('owned-instance')
        self.assertTrue(e.stopping)
        self.assertEqual(before,e.state_file.read_bytes())
        with patch.object(e,'process') as process:
            e.worker()
        process.assert_not_called()

    def test_launcher_http_identity_origin_and_graceful_shutdown(self):
        e=app.Editor(self.root/'stop-http/queue.json',start_worker=False)
        server=ThreadingHTTPServer(('127.0.0.1',0),app.Handler)
        port=server.server_address[1];base='http://127.0.0.1:'+str(port)
        with patch.object(app,'EDITOR',e),patch.object(app,'PORT',port),patch.dict(os.environ,{'PIANKE_LAUNCHER_ID':'http-owned'}):
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                with urlopen(base+'/api/health') as r:health=json.load(r)
                self.assertEqual(health['launcher_id'],'http-owned');self.assertFalse(health['busy'])
                def stop(value,origin):
                    request=Request(base+'/api/launcher/stop',data=json.dumps({'instance':value}).encode(),headers={'Content-Type':'application/json','Origin':origin})
                    return urlopen(request,timeout=5)
                with self.assertRaises(HTTPError) as cm:stop('http-owned','https://example.org')
                self.assertEqual(cm.exception.code,403)
                with self.assertRaises(HTTPError):stop('other',base)
                e.worker_busy=True
                with self.assertRaises(HTTPError):stop('http-owned',base)
                self.assertFalse(e.stopping)
                e.worker_busy=False
                with stop('http-owned',base) as response:self.assertTrue(json.load(response)['ok'])
                thread.join(5);self.assertFalse(thread.is_alive());self.assertTrue(e.stopping)
            finally:server.shutdown();server.server_close()

    def test_installer_is_user_scoped_and_preserves_generated_data(self):
        text=(app.ROOT/'packaging/PiankeStudio.iss').read_text(encoding='utf-8')
        self.assertIn('PrivilegesRequired=lowest',text)
        self.assertIn('--can-update',text)
        self.assertNotIn('[UninstallDelete]',text)
        self.assertIn('CloseApplications=no',text)
        manifest=(app.ROOT/'packaging/launcher.manifest').read_text(encoding='utf-8')
        self.assertIn('level="asInvoker"',manifest)

    def test_installer_verification_requires_consent_and_no_existing_install(self):
        with patch.object(subprocess,'run') as run:
            with self.assertRaisesRegex(ValueError,'consent'):
                installer_verify.verify(Path('not-a-setup.exe'),self.root)
            run.assert_not_called()
        with patch.object(installer_verify,'installed',return_value=True),patch.object(subprocess,'run') as run:
            with self.assertRaisesRegex(ValueError,'no existing'):
                installer_verify.verify(Path('not-a-setup.exe'),self.root,True)
            run.assert_not_called()


if __name__=='__main__':unittest.main()
