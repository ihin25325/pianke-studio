"""Fetch a pinned, OFL-licensed font pack and build static export weights on D:.

Only this setup utility needs fontTools. Preview/export uses ready-made TTF files.
Install the build dependency into tools/fonttools-runtime if necessary:
  python -m pip install --target tools/fonttools-runtime fonttools==4.60.1
"""
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sys
import urllib.parse

from download_fonts import fetch
from premium_fonts import PRIVATE_FONT_FILES

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / 'assets' / 'fonts'
sys.path.insert(0, str(ROOT / 'tools' / 'fonttools-runtime'))

COMMIT = 'cc39c4c2c304bc368c9b7c311ea2bcc6013299fe'
BASE = f'https://raw.githubusercontent.com/google/fonts/{COMMIT}/ofl/'
PACK = [
    ('ZCOOL QingKe HuangYou', '站酷庆科黄油体', 'zcoolqingkehuangyou', 'ZCOOLQingKeHuangYou', 'ZCOOLQingKeHuangYou-Regular.ttf', None),
    ('ZCOOL XiaoWei', '站酷小薇体', 'zcoolxiaowei', 'ZCOOLXiaoWei', 'ZCOOLXiaoWei-Regular.ttf', None),
    ('Ma Shan Zheng', '马善政楷书', 'mashanzheng', 'MaShanZheng', 'MaShanZheng-Regular.ttf', None),
    ('Inter', 'Inter', 'inter', 'Inter', 'Inter[opsz,wght].ttf', {'opsz': 14}),
    ('Manrope', 'Manrope', 'manrope', 'Manrope', 'Manrope[wght].ttf', {}),
    ('Space Grotesk', 'Space Grotesk', 'spacegrotesk', 'SpaceGrotesk', 'SpaceGrotesk[wght].ttf', {}),
    ('Bebas Neue', 'Bebas Neue', 'bebasneue', 'BebasNeue', 'BebasNeue-Regular.ttf', None),
    ('Outfit', 'Outfit', 'outfit', 'Outfit', 'Outfit[wght].ttf', {}),
]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def names(font):
    result = {}
    for key, nameid in [('family', 1), ('subfamily', 2), ('typographic_family', 16), ('typographic_subfamily', 17), ('postscript_name', 6)]:
        table = font['name']
        record = table.getName(nameid, 3, 1, 0x409) or table.getName(nameid, 1, 0, 0)
        result[key] = record.toUnicode() if record else None
    return result


def inspect(path):
    from fontTools.ttLib import TTFont
    with TTFont(path) as font:
        return dict(names(font), weight=font['OS/2'].usWeightClass,
                    variable='fvar' in font, glyphs=len(font.getBestCmap() or {}))


def download_family(item):
    from fontTools.ttLib import TTFont
    from fontTools.varLib.instancer import instantiateVariableFont
    family, label, slug, folder, filename, axes = item
    target = DEST / folder
    url = BASE + slug + '/' + urllib.parse.quote(filename)
    license_url = BASE + slug + '/OFL.txt'
    license_path = fetch(license_url, target / 'OFL.txt')
    source = fetch(url, target / filename, True)
    outputs = []
    expected = sorted(set(PRIVATE_FONT_FILES[family.lower()]))
    if axes is None:
        outputs = [source]
    else:
        for weight, weight_name in [(400, 'Regular'), (700, 'Bold')]:
            path = target / f'{folder}-{weight_name}.ttf'
            if not path.is_file():
                with TTFont(source) as variable:
                    instance = instantiateVariableFont(variable, dict(axes, wght=weight), inplace=False, updateFontNames=True)
                    instance.save(path)
            outputs.append(path)
    if sorted(str(path.relative_to(ROOT)).replace('\\', '/') for path in outputs) != expected:
        raise ValueError(f'Font metadata paths do not match: {family}')
    files = []
    for path in outputs:
        info = inspect(path)
        actual_family = info.get('typographic_family') or info['family']
        if actual_family != family or info['variable']:
            raise ValueError(f'Unexpected font family/variable font: {path.name}: {info}')
        files.append(dict(file=str(path.relative_to(ROOT)).replace('\\', '/'),
                          bytes=path.stat().st_size, sha256=digest(path), metadata=info,
                          derived_from=url if axes is not None else None,
                          source_url=url, generated_axes=dict(axes, wght=info['weight']) if axes is not None else None))
    return dict(family=family, label=label, source_commit=COMMIT,
                homepage=f'https://github.com/google/fonts/tree/{COMMIT}/ofl/{slug}',
                license='SIL Open Font License 1.1', license_url=license_url,
                license_file=str(license_path.relative_to(ROOT)).replace('\\', '/'),
                license_sha256=digest(license_path), source_file=str(source.relative_to(ROOT)).replace('\\', '/'),
                source_sha256=digest(source), files=files)


def main():
    from fontTools import __version__
    successful, failures = [], []
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {pool.submit(download_family, item): item[0] for item in PACK}
        for future in as_completed(futures):
            family = futures[future]
            try:
                successful.append(future.result())
                print(f'Verified: {family}', flush=True)
            except Exception as error:
                failures.append({'family': family, 'error': str(error)})
                print(f'Failed: {family}: {error}', flush=True)
    successful.sort(key=lambda item: next(i for i, row in enumerate(PACK) if row[0] == item['family']))
    manifest = dict(generated_at=datetime.now().astimezone().isoformat(), source_commit=COMMIT,
                    fonttools_version=__version__, families=successful, failures=failures)
    (DEST / 'premium_font_manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    return bool(failures)


if __name__ == '__main__':
    raise SystemExit(main())
