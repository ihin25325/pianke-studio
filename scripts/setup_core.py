"""Install optional local media binaries and default OFL fonts, not models.

FFmpeg is fetched over HTTPS and verified against the publisher's SHA256.
No shell scripts are fetched/executed; extract only explicit binaries/licenses.
"""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import zipfile

ROOT = Path(__file__).resolve().parents[1]
FFMPEG_ZIP = 'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip'
BTBN_BASE = 'https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/'
BTBN_NAME = 'ffmpeg-master-latest-win64-gpl.zip'
CORE_FONTS = ['Oswald/Oswald-Regular.ttf', 'Oswald/Oswald-SemiBold.ttf',
              'SourceHanSans/SourceHanSansCN-Regular.otf', 'SourceHanSans/SourceHanSansCN-Bold.otf']


class SetupNetworkError(ValueError):
    pass


def download(url, target, maximum):
    if not url.startswith('https://'):
        raise ValueError('Only HTTPS setup sources are allowed')
    partial = target.with_name(target.name + '.part')
    request = Request(url, headers={'User-Agent': 'Pianke-Studio-Setup/0.1'})
    if target.exists() or partial.exists():
        raise ValueError('Setup target already exists; refusing to overwrite')
    for attempt in range(3):
        try:
            with urlopen(request, timeout=25) as response, partial.open('xb') as out:
                if not response.geturl().startswith('https://'):
                    raise ValueError('Insecure setup redirect')
                size = 0
                while chunk := response.read(1024 * 1024):
                    size += len(chunk)
                    if size > maximum:
                        raise ValueError('Setup download exceeds size limit')
                    out.write(chunk)
                    if size // (16*1024*1024) != (size-len(chunk)) // (16*1024*1024):
                        print(f'Downloaded {size//(1024*1024)} MB', flush=True)
            partial.rename(target)
            return
        except (HTTPError, URLError, TimeoutError) as error:
            # Only a newly-created setup temporary file can be removed here.
            partial.unlink(missing_ok=True)
            if isinstance(error, HTTPError) and error.code not in {429,500,502,503,504}:
                raise
            if attempt == 2:
                raise SetupNetworkError('Setup source unavailable after 3 attempts. Check network or install trusted FFmpeg manually; SHA256 verification is never skipped.') from None
            time.sleep(attempt + 1)


def unpack_verified(archive, expected, destination):
    if not re.fullmatch(r'[0-9a-fA-F]{64}', expected) or hashlib.sha256(archive.read_bytes()).hexdigest() != expected.lower():
        raise ValueError('FFmpeg checksum mismatch; refusing to install')
    with zipfile.ZipFile(archive) as package:
        chosen = {}
        for item in package.infolist():
            path = PurePosixPath(item.filename)
            if path.is_absolute() or '..' in path.parts or '\\' in item.filename:
                raise ValueError('Unsafe archive path')
            if path.name in {'ffmpeg.exe', 'ffprobe.exe'} and path.parent.name == 'bin':
                if path.name in chosen or item.file_size > 350 * 1024 * 1024:
                    raise ValueError('Invalid binary archive')
                chosen[path.name] = item
        if set(chosen) != {'ffmpeg.exe', 'ffprobe.exe'}:
            raise ValueError('Archive does not contain both FFmpeg and ffprobe')
        destination.mkdir(parents=True, exist_ok=True)
        # Never overwrite a pre-existing local binary, even on partial reruns.
        for name, item in chosen.items():
            target = destination / name
            if not target.exists():
                with package.open(item) as source, target.open('xb') as out:
                    shutil.copyfileobj(source, out)
        for item in package.infolist():
            if PurePosixPath(item.filename).name.lower() in {'license', 'license.txt', 'readme.txt'} and item.file_size < 1024 * 1024:
                target = destination.parent / PurePosixPath(item.filename).name
                if not target.exists():
                    target.write_bytes(package.read(item))


def install_ffmpeg():
    destination = ROOT / 'tools/ffmpeg/bin'
    if all((destination / name).is_file() for name in ('ffmpeg.exe', 'ffprobe.exe')):
        print('FFmpeg already present; preserved existing files.', flush=True)
        return
    cache = ROOT / 'cache/setup'
    cache.mkdir(parents=True, exist_ok=True)
    providers = [('BtbN GPL', BTBN_BASE + BTBN_NAME, BTBN_BASE + 'checksums.sha256', BTBN_NAME),
                 ('Gyan', FFMPEG_ZIP, FFMPEG_ZIP + '.sha256', None)]
    for provider, url, sums, filename in providers:
        attempt = Path(tempfile.mkdtemp(prefix='ffmpeg_', dir=cache))
        archive = attempt / 'ffmpeg.zip'
        checksum = attempt / 'sha256.txt'
        print(f'Downloading FFmpeg from {provider} (100–210 MB); originals are not touched.', flush=True)
        try:
            download(sums, checksum, 64 * 1024)
            text = checksum.read_text(encoding='utf-8-sig')
            if filename:
                text = next((line for line in text.splitlines() if line.split() and line.split()[-1].lstrip('*') == filename), '')
            expected = re.search(r'\b[0-9a-fA-F]{64}\b', text)
            if not expected:
                raise ValueError('Publisher checksum was not available; refusing to install')
            download(url, archive, 250 * 1024 * 1024)
        except (SetupNetworkError, HTTPError, URLError):
            print('Source unavailable; trying next fixed publisher if available.', flush=True)
            continue
        unpack_verified(archive, expected[0], destination)
        print('FFmpeg checksum verified.', flush=True)
        return
    raise SetupNetworkError('Both FFmpeg sources unavailable. Install trusted ffmpeg.exe and ffprobe.exe into tools/ffmpeg/bin, then retry. No checksum was bypassed.')


def install_fonts():
    from download_fonts import PACK, fetch
    records = []
    for family in PACK:
        if family['folder'] not in {'Oswald', 'SourceHanSans'}:
            continue
        folder = ROOT / 'assets/fonts' / family['folder']
        fetch(family['license_url'], folder / 'OFL.txt')
        for name, url in family['fonts']:
            if str(Path(family['folder']) / name).replace('\\', '/') not in CORE_FONTS:
                continue
            file = fetch(url, folder / name, True)
            records.append({'file': str(file.relative_to(ROOT)).replace('\\', '/'),
                            'url': url, 'sha256': hashlib.sha256(file.read_bytes()).hexdigest()})
            print('Font ready: ' + name, flush=True)
    (ROOT / 'assets/fonts/core_manifest.json').write_text(json.dumps(records, indent=2), encoding='utf-8')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--skip-ffmpeg', action='store_true')
    parser.add_argument('--skip-fonts', action='store_true')
    args = parser.parse_args()
    if not args.skip_ffmpeg:
        install_ffmpeg()
    if not args.skip_fonts:
        install_fonts()


if __name__ == '__main__':
    main()
