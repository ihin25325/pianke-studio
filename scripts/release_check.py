"""Allowlisted source release audit/ZIP; never package the working directory wholesale."""
import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import zipfile

ROOT = Path(__file__).resolve().parents[1]
VERSION = '0.1.0'
ROOT_FILES = {'.gitignore', '.gitattributes', 'AGENTS.md', 'LICENSE', 'README.md',
              'THIRD_PARTY_NOTICES.md', 'SECURITY.md', 'CONTRIBUTING.md', 'CHANGELOG.md', 'requirements.txt',
              'SETUP.bat', 'DEMO.bat', 'RUN_TEST.bat', 'START_EDITOR.bat', '启动工作台.bat', '开始剪辑.bat',
              '字体编辑.bat', '安装语音字幕.bat', '安装中文翻译.bat', 'SnapAny接入说明.md', '国内外素材使用说明.md',
              'DeepSeek接入说明.md', 'STUDIO_V2使用说明.md', '品牌Logo使用说明.md', '语音字幕使用说明.md'}
PATTERNS = ['scripts/*.py', 'scripts/*.ps1', 'scripts/*.txt', 'web/*.html', 'web/*.css', 'web/*.js',
            'examples/*.csv', 'examples/*.json', 'docs/*.md', 'docs/demo.gif',
            'configs/defaults.json', '.github/workflows/*.yml', 'assets/fonts/**/OFL*.txt',
            'assets/fonts/使用说明.md', 'assets/fonts/高级字体包说明.md']
SECRET = re.compile(r'sk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{20,}|sk_snapany_[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|AKIA[0-9A-Z]{16}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----')
FAKE_KEYS = {'sk-local-test-not-valid-network', 'sk_snapany_local_test_not_a_real_key'}


def release_files(root=ROOT):
    files = {root / name for name in ROOT_FILES if (root / name).is_file()}
    for pattern in PATTERNS:
        files.update(p for p in root.glob(pattern) if p.is_file())
    for file in files:
        if file.is_symlink() or root.resolve() not in file.resolve().parents:
            raise ValueError('Unsafe release file path')
    return sorted(files, key=lambda p: p.relative_to(root).as_posix())


def audit(root=ROOT):
    files = release_files(root)
    names = {p.relative_to(root).as_posix() for p in files}
    issues = []
    required = {'README.md', 'LICENSE', 'requirements.txt', 'SETUP.bat', 'START_EDITOR.bat',
                'scripts/editor_server.py', 'scripts/product_render.py', 'scripts/api_connections.py',
                'scripts/runtime_paths.py', 'scripts/runtime.ps1', 'scripts/setup_core.py',
                'web/board.html', 'web/board.js', 'web/board.css', 'configs/defaults.json', 'examples/products.csv'}
    issues.extend('Missing required source: ' + name for name in sorted(required - names))
    for file in files:
        name = file.relative_to(root).as_posix()
        if file.stat().st_size > 5 * 1024 * 1024:
            issues.append('Unexpected large release file: ' + name)
        if file.suffix == '.gif':
            continue
        text = file.read_text(encoding='utf-8-sig')
        for n, line in enumerate(text.splitlines(), 1):
            for hit in SECRET.findall(line):
                if hit not in FAKE_KEYS:
                    issues.append(f'Possible credential (redacted): {name}:{n}')
            if re.search(r'[A-Za-z]:[\\/]+Users[\\/]+[A-Za-z0-9_-]+[\\/]', line):
                issues.append(f'Personal absolute path: {name}:{n}')
    return files, sorted(set(issues))


def package(root=ROOT, destination=None):
    files, issues = audit(root)
    if issues:
        raise ValueError('\n'.join(issues))
    folder = root / 'dist'
    folder.mkdir(exist_ok=True)
    destination = destination or folder / ('pianke-studio-' + VERSION + '-source_' + datetime.now().strftime('%Y%m%d_%H%M%S_%f') + '.zip')
    manifest = {'version': VERSION, 'files': []}
    with zipfile.ZipFile(destination, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
        for file in files:
            name = file.relative_to(root).as_posix()
            data = file.read_bytes()
            archive.writestr(name, data)
            manifest['files'].append({'path': name, 'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data)})
        archive.writestr('release-manifest.json', json.dumps(manifest, indent=2))
    return destination


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--package', action='store_true')
    args = parser.parse_args()
    files, issues = audit()
    print(f'Public source files: {len(files)}')
    for issue in issues:
        print(issue)
    if issues:
        raise SystemExit(1)
    print('Source release boundary and credential-pattern checks passed. Not a universal security guarantee.')
    if args.package:
        print(package())
