"""Build a filtered Windows portable bundle; no system Python or PATH changes.

Private CPython and Node archives come only from pinned official HTTPS origins.
FFmpeg/fonts are downloaded by the user's first-run consent, not redistributed.
"""
import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tempfile
from urllib.request import Request, urlopen
import zipfile

import release_check

ROOT=Path(__file__).resolve().parents[1]
VERSION='0.2.0-preview.1'
LOCK=ROOT/'packaging/windows-runtime.json'


def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def download(url, path, limit=100*1024*1024):
    from urllib.parse import urlsplit
    if urlsplit(url).scheme!='https' or urlsplit(url).hostname not in {'www.python.org','nodejs.org'}:
        raise ValueError('Only pinned official Python/Node publishers are allowed')
    if path.exists():return path
    partial=path.with_suffix(path.suffix+'.part')
    if partial.exists():raise ValueError('Incomplete cached download exists; inspect before retrying')
    path.parent.mkdir(parents=True,exist_ok=True)
    with urlopen(Request(url,headers={'User-Agent':'Pianke-Studio-Windows-Build/0.2'}),timeout=40) as response,partial.open('xb') as out:
        if not response.geturl().startswith('https://'):raise ValueError('Insecure download redirect')
        size=0
        while chunk:=response.read(1024*1024):
            size+=len(chunk)
            if size>limit:raise ValueError('Download size limit exceeded')
            out.write(chunk)
    partial.rename(path)
    return path


def unpack(archive,destination,expected):
    if not re.fullmatch('[a-f0-9]{64}',expected) or digest(archive)!=expected:
        raise ValueError('Official archive SHA256 mismatch')
    if destination.exists():raise ValueError('Refusing to overwrite an existing destination')
    with zipfile.ZipFile(archive) as source:
        seen=set()
        for row in source.infolist():
            name=PurePosixPath(row.filename)
            normalized=row.filename.casefold()
            if name.is_absolute() or '..' in name.parts or chr(92) in row.orig_filename or ':' in row.filename or normalized in seen or row.file_size>250*1024*1024 or (row.external_attr>>16)&0o170000==0o120000:
                raise ValueError('Unsafe or duplicate archive entry')
            seen.add(normalized)
        if sum(r.file_size for r in source.infolist())>600*1024*1024:raise ValueError('Archive expands beyond limit')
        source.extractall(destination)


def compile_launcher(destination):
    compiler=Path(os.environ.get('WINDIR','C:/Windows'))/'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
    if not compiler.is_file():raise ValueError('Windows .NET Framework C# compiler not found')
    destination.mkdir(parents=True,exist_ok=True)
    exe=destination/'PiankeStudio.exe'
    if exe.exists():raise ValueError('Existing launcher will not be overwritten')
    subprocess.run([str(compiler),'/nologo','/target:winexe','/platform:x64','/optimize+','/debug-',
        '/reference:System.dll','/reference:System.Core.dll','/reference:System.Drawing.dll',
        '/reference:System.Windows.Forms.dll','/reference:System.Web.Extensions.dll',
        '/win32manifest:'+str(ROOT/'packaging/launcher.manifest'),'/out:'+str(exe),str(ROOT/'packaging/Launcher.cs')],check=True)
    return exe


def configure_python(folder):
    paths=list(folder.glob('python*._pth'))
    if len(paths)!=1:raise ValueError('Unexpected embeddable Python layout')
    standard=next(folder.glob('python*.zip')).name
    paths[0].write_text(standard+'\n.\nsite-packages\n../../scripts\nimport site\n',encoding='utf-8')


def inventory(root):
    return [{'path':p.relative_to(root).as_posix(),'bytes':p.stat().st_size,'sha256':digest(p)}
            for p in sorted(root.rglob('*')) if p.is_file() and p.name!='bundle-manifest.json']


def assert_clean(root):
    banned={'data','work','cache','outputs','sources','models','.git','.venv','dist'}
    for p in root.rglob('*'):
        rel=p.relative_to(root)
        if rel.parts[0] in banned or p.is_symlink():raise ValueError('Private/generated path in distributable: '+str(rel))
        if p.is_file() and (p.suffix.lower() in {'.xlsx','.xls','.mp4','.log','.pyc'} or p.name.startswith('.env')):
            raise ValueError('Business/test artifact in bundle: '+str(rel))
        if p.is_file() and rel.parts[0]=='configs' and rel.as_posix()!='configs/defaults.json':
            raise ValueError('Nondefault configuration in bundle')


def build(destination=None):
    if os.name!='nt':raise ValueError('Build on Windows x64')
    files,issues=release_check.audit()
    if issues:raise ValueError('\n'.join(issues))
    stamp=datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    parent=Path(destination) if destination else ROOT/'dist'/('windows_'+stamp)
    if parent.exists():raise ValueError('Use a new output directory; existing files are preserved')
    bundle=parent/'PiankeStudio'
    bundle.mkdir(parents=True)
    for file in files:
        rel=file.relative_to(ROOT)
        target=bundle/rel;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(file,target)
    compile_launcher(bundle)
    settings=json.loads(LOCK.read_text(encoding='utf-8'))
    cache=ROOT/'cache/windows-build';cache.mkdir(parents=True,exist_ok=True)
    py=settings['python'];pyzip=download(py['url'],cache/Path(py['url']).name)
    python=bundle/'tools/python';unpack(pyzip,python,py['sha256']);configure_python(python)
    node=settings['node'];checks=download(node['checksums'],cache/('node-'+node['version']+'-SHASUMS256.txt'),1024*1024)
    filename=Path(node['url']).name
    matches=[line.split()[0] for line in checks.read_text().splitlines() if line.split()[-1].lstrip('*')==filename]
    if len(matches)!=1:raise ValueError('Node publisher checksum missing or ambiguous')
    nodezip=download(node['url'],cache/filename)
    stage=Path(tempfile.mkdtemp(prefix='node_',dir=cache))/'expanded'
    unpack(nodezip,stage,matches[0]);node_root=next(stage.iterdir())
    target=bundle/'tools/node';target.mkdir(parents=True)
    for filename in ['node.exe','LICENSE']:
        shutil.copyfile(node_root/filename,target/filename)
    wheels=cache/'wheels';wheels.mkdir(exist_ok=True)
    subprocess.run([sys.executable,'-m','pip','download','--disable-pip-version-check','--only-binary=:all:',
        '--platform','win_amd64','--implementation','cp','--python-version','3.14','--abi','cp314',
        '--dest',str(wheels),*settings['wheels']],check=True)
    # Only exact wheels resolved for the three pinned distributions are unpacked.
    selected=[]
    for requirement in settings['wheels']:
        name,version=requirement.split('==');prefix=name.lower().replace('-','_')+'-'+version+'-'
        candidates=[p for p in wheels.glob('*.whl') if p.name.lower().startswith(prefix)]
        if len(candidates)!=1:raise ValueError('Wheel cache ambiguous: '+name)
        selected.append(candidates[0])
    packages=python/'site-packages';packages.mkdir()
    for wheel in selected:
        stage=Path(tempfile.mkdtemp(prefix='wheel_',dir=cache))/'expanded'
        unpack(wheel,stage,digest(wheel))
        for child in stage.iterdir():
            dest=packages/child.name
            if dest.exists():raise ValueError('Overlapping wheel contents')
            shutil.move(str(child),str(dest))
    subprocess.run([str(python/'python.exe'),'-B','-c','import sys,PIL,yt_dlp,pip; assert sys.version_info[:2]==(3,14); print(sys.executable); print(PIL.__version__)'],check=True,cwd=bundle)
    assert_clean(bundle)
    manifest={'version':VERSION,'python':py,'node':dict(node,sha256=matches[0]),
              'wheels':[{'name':p.name,'sha256':digest(p)} for p in selected],
              'note':'FFmpeg and fonts are first-run official downloads, not bundled binaries. No API keys/models/business data.',
              'files':inventory(bundle)}
    (bundle/'bundle-manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    archive=parent/('PiankeStudio-'+VERSION+'-windows-x64-portable.zip')
    with zipfile.ZipFile(archive,'x',compression=zipfile.ZIP_DEFLATED) as out:
        for file in bundle.rglob('*'):
            if file.is_file():out.write(file,'PiankeStudio/'+file.relative_to(bundle).as_posix())
    archive.with_suffix('.zip.sha256').write_text(digest(archive)+'  '+archive.name+'\n',encoding='utf-8')
    print(json.dumps({'bundle':str(bundle),'zip':str(archive),'sha256':digest(archive)},ensure_ascii=False),flush=True)
    return bundle,archive


def installer(bundle,compiler):
    assert_clean(bundle)
    subprocess.run([str(compiler),'--quiet','/DBundleDir='+str(bundle),'/DOutputDir='+str(bundle.parent),str(ROOT/'packaging/PiankeStudio.iss')],check=True)
    exe=bundle.parent/('PiankeStudio-'+VERSION+'-windows-x64-Setup.exe')
    if not exe.is_file():raise ValueError('Installer compiler did not produce the expected EXE')
    exe.with_suffix('.exe.sha256').write_text(digest(exe)+'  '+exe.name+'\n',encoding='utf-8')
    print('Installer ready: '+str(exe),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--destination',type=Path)
    parser.add_argument('--iscc',type=Path)
    parser.add_argument('--installer-only',type=Path)
    args=parser.parse_args()
    if args.installer_only:
        if not args.iscc:parser.error('--installer-only requires --iscc')
        installer(args.installer_only,args.iscc)
    else:
        bundle,_=build(args.destination)
        if args.iscc:installer(bundle,args.iscc)
