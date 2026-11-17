"""Fetch fixed-revision CLIP ONNX data only; no remote Python code is executed."""
from pathlib import Path
import hashlib
import json
import urllib.request
ROOT=Path(__file__).resolve().parents[1]
REV='6b7c523703ae26e39be4b03774966f8571c7f523'
REPO='Xenova/clip-vit-base-patch32'
FILES=['config.json','preprocessor_config.json','tokenizer.json','tokenizer_config.json','README.md',
       'onnx/text_model_quantized.onnx','onnx/vision_model_quantized.onnx']


def main():
    folder=ROOT/'models/clip-vit-base-patch32';folder.mkdir(parents=True,exist_ok=True)
    with urllib.request.urlopen('https://huggingface.co/api/models/'+REPO+'/revision/'+REV+'?blobs=true',timeout=40) as r:metadata=json.load(r)
    known={x['rfilename']:x for x in metadata['siblings']};records=[]
    for name in FILES:
        target=folder/name;target.parent.mkdir(parents=True,exist_ok=True)
        expected=known[name].get('lfs',{}).get('sha256')
        if target.is_file() and (not expected or hashlib.sha256(target.read_bytes()).hexdigest()==expected):continue
        url='https://huggingface.co/'+REPO+'/resolve/'+REV+'/'+name
        request=urllib.request.Request(url,headers={'User-Agent':'Local-Product-Studio/2.0'})
        partial=target.with_suffix(target.suffix+'.part')
        with urllib.request.urlopen(request,timeout=180) as r,partial.open('wb') as out:
            while chunk:=r.read(1024*1024):out.write(chunk)
        digest=hashlib.sha256(partial.read_bytes()).hexdigest()
        if expected and digest!=expected:raise ValueError('Model checksum failed: '+name)
        partial.replace(target);print('Saved '+name,flush=True)
    license_url='https://raw.githubusercontent.com/openai/CLIP/main/LICENSE'
    with urllib.request.urlopen(license_url,timeout=30) as r:license_text=r.read()
    if b'MIT License' not in license_text:raise ValueError('Unexpected model license')
    (folder/'LICENSE.txt').write_bytes(license_text)
    for name in FILES:
        target=folder/name;records.append({'file':name,'bytes':target.stat().st_size,'sha256':hashlib.sha256(target.read_bytes()).hexdigest(),
                                         'url':'https://huggingface.co/'+REPO+'/resolve/'+REV+'/'+name})
    (folder/'manifest.json').write_text(json.dumps({'repository':REPO,'revision':REV,'license':'MIT','license_url':license_url,'files':records},indent=2),encoding='utf-8')


if __name__=='__main__':main()
