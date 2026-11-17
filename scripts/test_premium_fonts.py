"""Offline font-package checks; opt into a real libass PNG with FONT_SMOKE=1."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import premium_fonts
import typography

ROOT = Path(__file__).resolve().parents[1]


class PremiumFontTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not (ROOT / 'assets/fonts/premium_font_manifest.json').is_file():
            raise unittest.SkipTest('Optional full font pack not installed; run scripts/setup.ps1 -AllFonts.')
        cls.manifest = json.loads((ROOT / 'assets/fonts/premium_font_manifest.json').read_text(encoding='utf-8'))

    def test_all_eight_families_are_verified(self):
        self.assertEqual(self.manifest['failures'], [])
        self.assertEqual(len(self.manifest['families']), 8)
        for family in self.manifest['families']:
            self.assertIn(b'SIL OPEN FONT LICENSE', (ROOT / family['license_file']).read_bytes())
            self.assertEqual(hashlib.sha256((ROOT / family['license_file']).read_bytes()).hexdigest(), family['license_sha256'])
            for file in family['files']:
                self.assertEqual(hashlib.sha256((ROOT / file['file']).read_bytes()).hexdigest(), file['sha256'])
                self.assertFalse(file['metadata']['variable'])
                self.assertEqual(file['metadata']['typographic_family'] or file['metadata']['family'], family['family'])

    def test_paths_and_opt_in_presets_are_valid(self):
        self.assertEqual(len(typography.FONT_CHOICES), 13)
        for paths in premium_fonts.PRIVATE_FONT_FILES.values():
            for file in paths:
                self.assertTrue((ROOT / file).is_file())
        for preset in premium_fonts.FONT_PRESETS:
            typography.normalize_style(preset['style'])
        self.assertEqual(typography.DEFAULT_STYLE['title_font'], 'Oswald')

    @unittest.skipUnless(os.environ.get('FONT_SMOKE') == '1', 'Optional real FFmpeg/libass font render')
    def test_real_libass_render(self):
        target = ROOT / 'work/font_verification'
        target.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='font_smoke_', dir=ROOT / 'work') as temporary:
            folder = Path(temporary)
            (folder / 'fonts').mkdir()
            for pair in premium_fonts.PRIVATE_FONT_FILES.values():
                for source in set(pair):
                    shutil.copy2(ROOT / source, folder / 'fonts' / Path(source).name)
            header = '[Script Info]\nScriptType: v4.00+\nPlayResX: 1280\nPlayResY: 800\n[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n'
            rows = []
            for index, family in enumerate(self.manifest['families']):
                header += f'Style: F{index},{family["family"]},40,&H00251C17,&H00251C17,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,40,40,30,1\n'
                text = family['label'] + '  商品视频 · 清晰有质感' if index < 3 else family['label'] + '  Product Studio — AX 300'
                rows.append(f'Dialogue: 0,0:00:00.00,0:00:01.00,F{index},,0,0,0,,{{\\pos(48,{42 + index * 90})}}{text}')
            (folder / 'fonts.ass').write_text(header + '[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n' + '\n'.join(rows), encoding='utf-8')
            command = [str(ROOT / 'tools/ffmpeg/bin/ffmpeg.exe'), '-hide_banner', '-y', '-f', 'lavfi', '-i', 'color=c=0xf5f5fa:s=1280x800:d=1', '-vf', 'subtitles=fonts.ass:fontsdir=fonts', '-frames:v', '1', str(target / 'premium_fonts_actual.png')]
            result = subprocess.run(command, cwd=folder, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=90)
            (target / 'libass.log').write_text(result.stderr, encoding='utf-8')
            self.assertEqual(result.returncode, 0, result.stderr[-3000:])
            self.assertGreater((target / 'premium_fonts_actual.png').stat().st_size, 10000)
            self.assertNotIn('failed to find any fallback', result.stderr.lower())
            for family in self.manifest['families']:
                self.assertIn(family['family'], result.stderr)


if __name__ == '__main__':
    unittest.main()
