"""Extract and test a desktop ZIP in isolation, never use an existing app directory."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from build_windows import unpack,digest

ROOT=Path(__file__).resolve().parents[1]


def verify(archive,prepare_media=False):
    # A new destination is mandatory: this command must never test the user's board.
    parent=ROOT/'work/windows_verification';parent.mkdir(parents=True,exist_ok=True)
    target=Path(tempfile.mkdtemp(prefix='clean_',dir=parent))/'extracted'
    expected=archive.with_suffix('.zip.sha256').read_text().split()[0]
    unpack(archive,target,expected)
    root=target/'PiankeStudio';exe=root/'PiankeStudio.exe';python=root/'tools/python/python.exe'
    manifest=json.loads((root/'bundle-manifest.json').read_text(encoding='utf-8'))
    for row in manifest['files']:
        if digest(root/row['path'])!=row['sha256']:raise ValueError('Bundle file hash mismatch: '+row['path'])
    env=dict(os.environ,PYTHONUTF8='1',PYTHONIOENCODING='utf-8',PYTHONDONTWRITEBYTECODE='1')
    if prepare_media:
        subprocess.run([str(exe),'--prepare'],env=env,cwd=root,timeout=1000,check=True)
    else:
        raise ValueError('Fresh desktop tests require explicit --prepare-media consent for the media downloads')
    # Only Windows system tools remain on PATH; core Python and Node must be bundled.
    windir=Path(os.environ['WINDIR'])
    env.update(PATH=str(windir/'System32')+os.pathsep+str(windir),
               HTTP_PROXY='http://127.0.0.1:9',HTTPS_PROXY='http://127.0.0.1:9',ALL_PROXY='http://127.0.0.1:9',
               NO_PROXY='127.0.0.1,localhost',PIANKE_NODE=str(root/'tools/node/node.exe'))
    probe='import sys,json,shutil,PIL,yt_dlp; from runtime_paths import node_executable; print(json.dumps({"python":sys.executable,"pillow":PIL.__file__,"yt_dlp":yt_dlp.__file__,"node":node_executable(),"path_python":shutil.which("python"),"path_node":shutil.which("node")}))'
    runtime=json.loads(subprocess.check_output([str(python),'-B','-c',probe],env=env,cwd=root,text=True,encoding='utf-8'))
    for key in ['python','pillow','yt_dlp','node']:
        if root not in Path(runtime[key]).resolve().parents:raise ValueError('External runtime used: '+key)
    if runtime['path_python'] or runtime['path_node']:raise ValueError('System runtime still on PATH')
    subprocess.run([str(exe),'--smoke-test'],env=env,cwd=root,timeout=90,check=True)
    subprocess.run([str(exe),'--render-preview'],env=env,cwd=root,timeout=30,check=True)
    subprocess.run([str(python),'-B','scripts/verify_clean_release.py'],env=env,cwd=root,timeout=240,check=True)
    smoke=json.loads((root/'work/launcher/smoke-proof.json').read_text())
    render=json.loads((root/'work/release_smoke/proof.json').read_text(encoding='utf-8'))
    proof={'zip':str(archive),'zip_sha256':digest(archive),'isolated_root':str(root),
           'private_runtimes':runtime,'launcher':smoke,'render':render,
           'note':'Real isolated extract, system runtimes removed from PATH, external network blocked after media setup. Not a separate clean Windows VM or browser interaction recording.'}
    path=target.parent/'proof.json';path.write_text(json.dumps(proof,ensure_ascii=False,indent=2),encoding='utf-8')
    print('Desktop verification proof: '+str(path),flush=True)
    return proof


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('archive',type=Path);parser.add_argument('--prepare-media',action='store_true')
    args=parser.parse_args();verify(args.archive.resolve(),args.prepare_media)
