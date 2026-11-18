"""Release safety and clean-bootstrap tests; never require private data or API keys."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile
import editor_server as app
import release_check as release
import runtime_paths
import setup_core


class ReleaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        folder = app.ROOT / 'work/release_tests'
        folder.mkdir(parents=True, exist_ok=True)
        cls.root = Path(tempfile.mkdtemp(prefix='run_', dir=folder))

    def test_public_source_boundary_complete_and_secret_free(self):
        files, issues = release.audit()
        self.assertEqual(issues, [])
        names = {p.relative_to(app.ROOT).as_posix() for p in files}
        self.assertIn('scripts/product_render.py', names)
        self.assertIn('scripts/test_editor.py', names)
        self.assertIn('web/brand-assets.js', names)
        self.assertIn('assets/fonts/使用说明.md', names)
        self.assertIn('assets/fonts/高级字体包说明.md', names)
        for name in names:
            self.assertNotIn(name.split('/')[0], {'data','work','sources','outputs','cache','tools','models','.git','.venv','dist'})
            self.assertFalse(name.startswith('configs/task_'))
            self.assertNotIn(Path(name).suffix, {'.xlsx','.exe','.ttf','.otf'})

    def test_published_copy_has_no_fixed_project_drive(self):
        for file in release.release_files():
            if file.name.startswith('test_') or file.suffix not in {'.md','.html','.js','.py','.ps1','.bat'}:
                continue
            text=file.read_text(encoding='utf-8-sig')
            with self.subTest(file=file.relative_to(app.ROOT).as_posix()):
                # System paths and intentionally invalid security-test paths are not private project locations.
                self.assertNotRegex(text, r'\bD:(?:[\\/]|\s|[.])|D\s*盘|C\s*盘')
                self.assertNotRegex(text, r'[A-Za-z]:[\\/]+Users[\\/]+[^\\/\s]+[\\/]')

    def test_font_specimen_writes_local_notes_not_published_guide(self):
        import font_specimen as specimen
        folder=self.root/'specimen'
        fonts=folder/'assets/fonts'
        fonts.mkdir(parents=True)
        guide=fonts/'使用说明.md'
        guide.write_text('Portable published guide\n',encoding='utf-8')
        manifest={'families':[{'name':'Demo','family':'Demo Sans','use':'Fixture',
                               'fonts':[{'bytes':1048576}]}]}
        (fonts/'font_manifest.json').write_text(json.dumps(manifest),encoding='utf-8')
        output=folder/'outputs'
        with patch.object(specimen,'FONTS',fonts),patch.object(specimen,'OUT',output), \
             patch.object(specimen.Image,'new'),patch.object(specimen.ImageDraw,'Draw'), \
             patch.object(specimen,'font'),patch.object(specimen,'center'):
            specimen.main()
        self.assertEqual(guide.read_text(encoding='utf-8'),'Portable published guide\n')
        note=(output/'字体对比说明.md').read_text(encoding='utf-8')
        self.assertIn('[字体对比.png](字体对比.png)',note)
        self.assertIn('1.0 MiB',note)
        self.assertNotIn(str(folder),note)
        self.assertNotRegex(note,r'[A-Za-z]:[\\/]')

    def test_fresh_default_template_without_original_product_config(self):
        root = self.root / 'template'
        (root/'configs').mkdir(parents=True)
        (root/'configs/defaults.json').write_text('{}', encoding='utf-8')
        self.assertEqual(runtime_paths.sample_config(root).name, 'defaults.json')
        (root/'configs/neumann_u87ai.json').write_text('{}', encoding='utf-8')
        self.assertEqual(runtime_paths.sample_config(root).name, 'neumann_u87ai.json')
        with patch.object(app, 'sample_config', return_value=app.ROOT/'configs/defaults.json'):
            e = app.Editor(self.root/'fresh/queue.json', start_worker=False)
        self.assertEqual(e.sample['product_id'], 'demo')
        self.assertFalse(e.connections.policy())
        self.assertEqual(e.snapany.mode(), 'off')

    def test_clean_studio_mode_is_manual_and_does_not_replace_saved_preferences(self):
        import studio
        with patch.object(studio, 'ROOT', self.root/'empty'):
            e = app.Editor(self.root/'manual/queue.json', start_worker=False)
            self.assertEqual(e.state['studio_settings']['mode'], 'manual')
            self.assertFalse(e.state['studio_settings']['cover_enabled'])
            e.studio.save({'mode':'smart30','cover_enabled':True})
            again = app.Editor(e.state_file, start_worker=False)
            self.assertEqual(again.state['studio_settings']['mode'], 'smart30')

    def test_checksum_and_zip_traversal_rejected_no_overwrite(self):
        folder = self.root/'archive'
        folder.mkdir()
        archive = folder/'test.zip'
        with zipfile.ZipFile(archive, 'w') as out:
            out.writestr('package/bin/ffmpeg.exe', b'test fixture')
            out.writestr('package/bin/ffprobe.exe', b'probe fixture')
        checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
        with self.assertRaises(ValueError):
            setup_core.unpack_verified(archive, '0'*64, folder/'invalid')
        destination = folder/'tools/bin'
        setup_core.unpack_verified(archive, checksum, destination)
        (destination/'ffmpeg.exe').write_bytes(b'local original')
        setup_core.unpack_verified(archive, checksum, destination)
        self.assertEqual((destination/'ffmpeg.exe').read_bytes(), b'local original')
        bad = folder/'bad.zip'
        with zipfile.ZipFile(bad, 'w') as out:
            out.writestr('../escape', b'bad')
        with self.assertRaises(ValueError):
            setup_core.unpack_verified(bad, hashlib.sha256(bad.read_bytes()).hexdigest(), folder/'bad')

    def test_source_zip_excludes_private_data_and_has_hash_manifest(self):
        destination = self.root/'source.zip'
        release.package(destination=destination)
        with zipfile.ZipFile(destination) as archive:
            names = archive.namelist()
            self.assertIn('LICENSE', names)
            self.assertIn('examples/products.csv', names)
            self.assertIn('assets/fonts/使用说明.md', names)
            self.assertIn('assets/fonts/高级字体包说明.md', names)
            self.assertFalse(any(n.startswith(('data/','sources/','outputs/','work/','tools/','models/','.git/')) for n in names))
            manifest = json.loads(archive.read('release-manifest.json'))
            for row in manifest['files']:
                self.assertEqual(hashlib.sha256(archive.read(row['path'])).hexdigest(), row['sha256'])

    def test_node_environment_override(self):
        path = self.root/'node.exe'
        path.write_bytes(b'test only, never executed')
        with patch.dict(os.environ, {'PIANKE_NODE':str(path)}):
            self.assertEqual(runtime_paths.node_executable(), str(path))

    def test_synthetic_xlsx_import_without_private_business_workbook(self):
        import io
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer,'w') as z:
            z.writestr('xl/workbook.xml','<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="示例" sheetId="1" r:id="rId1"/></sheets></workbook>')
            z.writestr('xl/_rels/workbook.xml.rels','<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>')
            rows = '<row r="1"><c r="A1" t="inlineStr"><is><t>商品ID</t></is></c><c r="B1" t="inlineStr"><is><t>商品名称</t></is></c></row>'
            for n, title, hidden in [(2,'Demo AX100',''),(3,'Demo AX200',' hidden="1"')]:
                rows += f'<row r="{n}"{hidden}><c r="A{n}"><v>{10000000+n}</v></c><c r="B{n}" t="inlineStr"><is><t>{title}</t></is></c></row>'
            z.writestr('xl/worksheets/sheet1.xml','<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'+rows+'</sheetData></worksheet>')
        e = app.Editor(self.root/'xlsx/queue.json', start_worker=False)
        preview = e.board.upload('synthetic.xlsx', buffer.getvalue())
        self.assertEqual(preview['summary']['selected'],1)
        self.assertEqual(preview['summary']['hidden_excluded'],1)
        result = e.board.commit({'token':preview['token']})
        self.assertEqual(result['added'],1)
        self.assertFalse(e.state['tasks'])

    def test_shipped_csv_example_can_be_imported_without_a_catalog(self):
        e=app.Editor(self.root/'csv/queue.json',start_worker=False)
        e.catalog=[];e.by_id={};e.by_title={}
        preview=e.board.upload('products.csv',(app.ROOT/'examples/products.csv').read_bytes())
        self.assertEqual(preview['summary']['selected'],1)
        self.assertEqual(e.board.commit({'token':preview['token']})['added'],1)
        self.assertEqual(e.state['products'][0]['source_url'],'https://example.com/pianke-demo.mp4')

    def test_setup_download_retries_transient_failure_without_overwrite(self):
        import io
        from urllib.error import HTTPError
        class Response(io.BytesIO):
            def geturl(self):return 'https://example.com/archive'
        target = self.root/'retry.bin'
        with patch.object(setup_core, 'urlopen', side_effect=[HTTPError('https://example.com',503,'unavailable',{},None), Response(b'checked')]) as request, patch.object(setup_core.time,'sleep'):
            setup_core.download('https://example.com/archive', target, 1024)
        self.assertEqual(request.call_count,2)
        self.assertEqual(target.read_bytes(),b'checked')
        with patch.object(setup_core, 'urlopen', side_effect=AssertionError('Existing file must not request network')), self.assertRaises(ValueError):
            setup_core.download('https://example.com/archive', target, 1024)

    def test_fixed_ffmpeg_fallback_still_requires_matching_sha256(self):
        root=self.root/'mirror'
        root.mkdir()
        def fetch(url,target,limit):
            if 'github.com' in url:raise setup_core.SetupNetworkError('unavailable')
            if url.endswith('.sha256'):
                target.write_text('a'*64+'  '+setup_core.BTBN_NAME,encoding='utf-8')
            else:target.write_bytes(b'not trusted until checked')
        with patch.object(setup_core,'ROOT',root),patch.object(setup_core,'download',side_effect=fetch),patch.object(setup_core,'unpack_verified') as unpack:
            setup_core.install_ffmpeg()
        self.assertEqual(unpack.call_count,1)
        self.assertEqual(unpack.call_args.args[1],'a'*64)


if __name__ == '__main__':
    unittest.main()
