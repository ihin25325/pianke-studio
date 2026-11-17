"""Explicitly verify local link lookup and a redo, retaining all previous outputs."""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import time
from urllib.parse import unquote
from urllib.request import Request,urlopen

ROOT=Path(__file__).resolve().parents[1]
BASE='http://127.0.0.1:8765'

def api(path,payload=None):
    request=Request(BASE+path,data=json.dumps(payload).encode() if payload is not None else None,
                    headers={'Origin':BASE,'Content-Type':'application/json'})
    with urlopen(request,timeout=30) as response:return json.load(response)

def wait(task_id,seconds=300):
    deadline=time.monotonic()+seconds;last=''
    while time.monotonic()<deadline:
        task=next(t for t in api('/api/state')['tasks'] if t['id']==task_id)
        message=f"{task['status']}: {task.get('message','')}"
        if message!=last:print(message,flush=True);last=message
        if task['status'] not in {'queued','searching','downloading','transcribing','rendering'}:return task
        time.sleep(2)
    raise RuntimeError('Sample verification timed out; task left intact for inspection.')

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--original',default='202aa0895ba4');parser.add_argument('--skip-lookup',action='store_true');args=parser.parse_args()
    before=api('/api/state');assert not before['worker_busy']
    original=copy.deepcopy(next(t for t in before['tasks'] if t['id']==args.original))
    original_file=ROOT/'outputs'/unquote(original['output_url'].split('/media/')[1])
    original_hash=hashlib.sha256(original_file.read_bytes()).hexdigest()
    workbook=ROOT/'副本需要添加首图视频的商品 11-6.xlsx'
    workbook_hash=hashlib.sha256(workbook.read_bytes()).hexdigest()
    report={'original_task_id':original['id'],'original_file':str(original_file),'original_sha256':original_hash,
            'workbook_sha256':workbook_hash,'before_counts':api('/api/board?view=all')['counts']}
    # The SAMSON item has no selected source or previous search cache: real public search.
    if not args.skip_lookup:
        product=next(p for p in before['products'] if p.get('product_id')=='2484533044')
        lookup=api('/api/board/jobs',{'ids':[product['id']],'job':'collect'})
        assert lookup['added']==1,lookup
        task=wait(lookup['task_ids'][0]);report['lookup']={k:task.get(k) for k in ('id','status','selected_url','message','candidates')}
        assert task['status'] in {'links_ready','needs_review'},task.get('message')
        assert not task.get('output_url')
        report['lookup']['no_download_log']=not (ROOT/'work'/'editor'/task['id']/'download.log').exists()
    redo=api('/api/products/'+original['board_product_id']+'/redo',{'source_task_id':original['id']})
    task=wait(redo['task_id']);assert task['status']=='completed',task.get('message')
    output=ROOT/'outputs'/unquote(task['output_url'].split('/media/')[1])
    probe=subprocess.run([str(ROOT/'tools'/'ffmpeg'/'bin'/'ffprobe.exe'),'-v','error','-show_entries','stream=codec_name,width,height:format=duration','-of','json',str(output)],capture_output=True,text=True,check=True)
    media=json.loads(probe.stdout);video=next(s for s in media['streams'] if 'width' in s)
    assert (video['width'],video['height'])==(1080,1440)
    assert float(media['format']['duration'])<=original.get('max_duration_seconds',90)+.2
    subprocess.run([str(ROOT/'tools'/'ffmpeg'/'bin'/'ffmpeg.exe'),'-v','error','-i',str(output),'-f','null','-'],check=True,capture_output=True)
    after=api('/api/state');unchanged=next(t for t in after['tasks'] if t['id']==original['id'])==original
    assert unchanged and hashlib.sha256(original_file.read_bytes()).hexdigest()==original_hash
    assert hashlib.sha256(workbook.read_bytes()).hexdigest()==workbook_hash
    report.update(redo_task_id=task['id'],output=str(output),media=media,old_task_unchanged=unchanged,old_media_unchanged=True,workbook_unchanged=True,
                  after_counts=api('/api/board?view=all')['counts'],api_llm_calls=0,subtitle_target=task.get('subtitle_target','original'),subtitle_url=task.get('subtitle_url',''))
    folder=ROOT/'work'/'board_verification';folder.mkdir(parents=True,exist_ok=True)
    file=folder/time.strftime('verified_%Y%m%d_%H%M%S.json');file.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print('VERIFIED '+str(file),flush=True);print('OUTPUT '+str(output),flush=True)

if __name__=='__main__':main()
