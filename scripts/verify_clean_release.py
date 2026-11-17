"""Real synthetic render + fresh-server smoke. No paid APIs or business queue edits."""
import hashlib
import json
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.request import urlopen

import demo
import editor_server as app
import product_render as renderer

ROOT=Path(__file__).resolve().parents[1]


def digest(file):
    return hashlib.sha256(file.read_bytes()).hexdigest() if file.is_file() else None


def main():
    production=ROOT/'data/editor/queue.json'
    before=digest(production)
    source=demo.generate()
    original=digest(source)
    folder=ROOT/'work/release_smoke'
    folder.mkdir(parents=True,exist_ok=True)
    # A new isolated queue for each test run, never the actual board.
    state=folder/('queue_'+str(time.time_ns())+'.json')
    e=app.Editor(state,start_worker=False)
    uploaded=e.board.upload('products.csv',(ROOT/'examples/products.csv').read_bytes())
    e.board.commit({'token':uploaded['token']})
    task=e.board.new_task(e.state['products'][0],'render')
    task.update(subtitles_enabled=False,max_duration_seconds=6,batch_clip={'start':0,'end':6})
    e.process(task)
    if task['status']!='completed':raise ValueError(task.get('message','Synthetic render failed'))
    output=Path(task['output_file'])
    metadata=json.loads(output.with_suffix('.json').read_text(encoding='utf-8'))
    validation=metadata['validation']
    if (validation['width'],validation['height'])!=(1080,1440):raise ValueError('Unexpected output dimensions')
    result=subprocess.run([str(renderer.FFMPEG),'-v','error','-nostdin','-i',str(output),'-f','null','-'],capture_output=True,timeout=45)
    if result.returncode:raise ValueError('Full output decode failed')
    if digest(source)!=original:raise ValueError('Synthetic original changed')
    with socket.socket() as available:
        available.bind(('127.0.0.1',0));port=available.getsockname()[1]
    server=subprocess.Popen([sys.executable,'-B',str(ROOT/'scripts/editor_server.py'),'--port',str(port)],cwd=ROOT,
                            stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    paths=['/','/sources','/typography','/api-settings','/brand-assets','/api/health','/api/board','/api/snapany']
    try:
        base='http://127.0.0.1:'+str(port)
        for _ in range(50):
            try:
                with urlopen(base+'/api/health',timeout=1) as response:
                    if json.load(response)['ok']:break
            except OSError:time.sleep(.2)
        else:raise ValueError('Fresh local service failed to start')
        for path in paths:
            with urlopen(base+path,timeout=5) as response:
                if response.status!=200:raise ValueError('HTTP smoke failed: '+path)
        with urlopen(base+'/api/snapany') as response:
            if json.load(response)['mode']!='off':raise ValueError('Fresh paid API must be off')
    finally:
        subprocess.run(['taskkill','/PID',str(server.pid),'/T','/F'],capture_output=True)
        server.wait(timeout=10)
    if before is None:
        fresh=json.loads(production.read_text(encoding='utf-8')) if production.is_file() else {}
        if fresh.get('tasks') or fresh.get('products'):raise ValueError('Smoke inserted data into the default board')
    elif digest(production)!=before:raise ValueError('Production queue changed')
    proof={'output':str(output),'validation':validation,'http_pages':paths,'full_decode':True,
           'original_unchanged':True,'production_queue_unchanged':True,'paid_api_calls':0,
           'note':'Real synthetic rendering and HTTP checks, not browser visual QA or a live 100-video batch.'}
    (folder/'proof.json').write_text(json.dumps(proof,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(proof,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
