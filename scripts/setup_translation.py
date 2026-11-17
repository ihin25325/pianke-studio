"""Fetch only the official Qwen GGUF and official llama.cpp Windows CPU build."""
import hashlib
import json
from pathlib import Path,PurePosixPath
import shutil
import urllib.request
import zipfile

ROOT=Path(__file__).resolve().parents[1]
MODEL=ROOT/'models'/'qwen3-4b'
REPO='Qwen/Qwen3-4B-GGUF'
REV='bc640142c66e1fdd12af0bd68f40445458f3869b'
FILE='Qwen3-4B-Q4_K_M.gguf'
SHA='7485fe6f11af29433bc51cab58009521f205840f5b4ae3a32fa7f92e8534fdf5'
TAG='b12211'


def request(url):
    return urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'Local-Product-Video/1.0'}),timeout=90)


def digest(file):
    value=hashlib.sha256()
    with file.open('rb') as handle:
        while block:=handle.read(4*1024*1024): value.update(block)
    return value.hexdigest()


def download(url,target,sha=None):
    if target.is_file() and (not sha or digest(target)==sha): return
    partial=target.with_suffix(target.suffix+'.part')
    with request(url) as response,partial.open('wb') as handle:
        expected=int(response.headers.get('Content-Length') or 0);size=0;last=-1
        while block:=response.read(4*1024*1024):
            handle.write(block);size+=len(block)
            progress=int(size/max(1,expected)*100) if expected else size//(100*1024*1024)
            if progress//5!=last:
                last=progress//5;print(f'Download {target.name}: {size//(1024*1024)} MB',flush=True)
    if expected and partial.stat().st_size!=expected: raise ValueError('Incomplete download: '+target.name)
    if sha and digest(partial)!=sha: raise ValueError('SHA256 mismatch: '+target.name)
    partial.replace(target)


def main():
    cache=ROOT/'cache'/'translation';cache.mkdir(parents=True,exist_ok=True)
    tools=ROOT/'tools'/'llama-cpp';tools.mkdir(parents=True,exist_ok=True)
    url=f'https://github.com/ggml-org/llama.cpp/releases/download/{TAG}/llama-{TAG}-bin-win-cpu-x64.zip'
    archive=cache/f'llama-{TAG}-bin-win-cpu-x64.zip';download(url,archive)
    with zipfile.ZipFile(archive) as zipped:
        if zipped.testzip(): raise ValueError('Invalid official tool archive.')
        for entry in zipped.infolist():
            relative=PurePosixPath(entry.filename)
            if relative.is_absolute() or '..' in relative.parts or '\\' in str(relative): raise ValueError('Unsafe archive path.')
            if entry.is_dir(): continue
            target=tools.joinpath(*relative.parts);target.parent.mkdir(parents=True,exist_ok=True)
            with zipped.open(entry) as source,target.open('wb') as handle: shutil.copyfileobj(source,handle)
    for name in ('LICENSE','README.md'):
        with request(f'https://raw.githubusercontent.com/ggml-org/llama.cpp/{TAG}/{name}') as response: (tools/name).write_bytes(response.read())
    MODEL.mkdir(parents=True,exist_ok=True)
    model_url=f'https://huggingface.co/{REPO}/resolve/{REV}/{FILE}'
    download(model_url,MODEL/FILE,SHA)
    for name in ('LICENSE','README.md'):
        with request(f'https://huggingface.co/{REPO}/resolve/{REV}/{name}') as response: (MODEL/name).write_bytes(response.read())
    (MODEL/'download_manifest.json').write_text(json.dumps({'repository':REPO,'revision':REV,'url':model_url,'file':FILE,'bytes':(MODEL/FILE).stat().st_size,'sha256':SHA,
        'runtime_release':TAG,'runtime_url':url,'runtime_archive_sha256':digest(archive)},indent=2),encoding='utf-8')
    print('Offline Chinese translation ready: '+str(MODEL),flush=True)


if __name__=='__main__': main()
