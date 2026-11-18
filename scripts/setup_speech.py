"""Download the public Whisper base model into the project directory and verify every model file."""
import hashlib
import json
from pathlib import Path
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
REPO='Systran/faster-whisper-base'
DEST=ROOT/'models'/'faster-whisper-base'
FILES=('config.json','model.bin','tokenizer.json','vocabulary.txt')


def get(url):
    return urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'Local-Product-Video/1.0'}),timeout=90)


def digest(path):
    result=hashlib.sha256()
    with path.open('rb') as handle:
        while block:=handle.read(1024*1024): result.update(block)
    return result.hexdigest()


def main():
    DEST.mkdir(parents=True,exist_ok=True)
    with get(f'https://huggingface.co/api/models/{REPO}?blobs=true') as response:
        metadata=json.load(response)
    revision=metadata['sha']
    entries={entry['rfilename']:entry for entry in metadata['siblings']}
    recorded=[]
    for name in FILES:
        item=entries[name]
        target=DEST/name
        expected=item.get('lfs',{}).get('sha256')
        size=item.get('size') or item.get('lfs',{}).get('size')
        valid=target.is_file() and target.stat().st_size==size and (not expected or digest(target)==expected)
        if not valid:
            partial=target.with_suffix(target.suffix+'.part')
            with get(f'https://huggingface.co/{REPO}/resolve/{revision}/{name}') as response,partial.open('wb') as handle:
                while block:=response.read(1024*1024): handle.write(block)
            if size and partial.stat().st_size!=size:
                raise ValueError(f'Incomplete model file: {name}')
            if expected and digest(partial)!=expected:
                raise ValueError(f'Model checksum mismatch: {name}')
            partial.replace(target)
        recorded.append({'file':name,'bytes':target.stat().st_size,'sha256':digest(target)})
        print('Ready: '+name,flush=True)
    with get('https://raw.githubusercontent.com/openai/whisper/main/LICENSE') as response:
        (DEST/'LICENSE.txt').write_bytes(response.read())
    (DEST/'download_manifest.json').write_text(json.dumps({'repository':REPO,'revision':revision,'files':recorded},indent=2),encoding='utf-8')
    print('Offline speech model ready: '+str(DEST),flush=True)


if __name__=='__main__': main()
