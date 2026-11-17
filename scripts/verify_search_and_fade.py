"""Opt-in real network/media verification; never loads the production worker."""
import argparse
from array import array
from datetime import datetime
import json
import math
from pathlib import Path
import subprocess
import tempfile
from unittest.mock import patch

import editor_server as app
import product_render as renderer


def verify_search(folder):
    original=json.loads(app.STATE_FILE.read_text(encoding='utf-8'))
    results=[]
    needles=['miniDSP 2X4HD','PHARA-O MINI','星域A7','Alctron/爱克创HA408']
    e=app.Editor(folder/'isolated_queue.json',start_worker=False)
    with patch.object(app,'DATA',folder/'cache'):
        for needle in needles:
            product=next(p for p in original['products'] if needle.lower() in p['title'].lower())
            history=[t for t in original['tasks'] if t.get('board_product_id')==product['id']]
            old=history[-1] if history else {}
            e.board.paste({'text':product['title']})
            task=e.board.new_task(e.state['products'][-1],'collect')
            print('SEARCH_START '+product['title'],flush=True)
            try:
                e.process(task)
                candidates=task.get('candidates',[])
                result={'title':product['title'],'before_status':old.get('status'),
                    'before_top_score':max((c.get('score',0) for c in old.get('candidates',[])),default=0),
                    'after_status':task['status'],'model':task.get('model'),'candidate_count':len(candidates),
                    'selected_url':task.get('selected_url'),'top_candidates':candidates[:3],
                    'report':task.get('search_report')}
            except Exception as error:
                result={'title':product['title'],'error':str(error),'report':task.get('search_report')}
            results.append(result)
            (folder/'search_results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
            print('SEARCH_RESULT '+json.dumps({k:v for k,v in result.items() if k not in {'top_candidates','report'}},ensure_ascii=False),flush=True)
    print('SEARCH_PROOF '+str(folder/'search_results.json'),flush=True)


def run(args):
    result=subprocess.run([str(renderer.FFMPEG),'-nostdin','-v','error']+args,capture_output=True,timeout=45)
    if result.returncode: raise RuntimeError(result.stderr.decode('utf-8',errors='replace'))
    return result.stdout


def verify_fade(folder):
    tone=folder/'verification_tone.wav'
    run(['-n','-f','lavfi','-i','sine=frequency=440:sample_rate=48000:duration=3',str(tone)])
    config=renderer.read_json(app.ROOT/'configs'/'neumann_u87ai.json')
    config.update(source_audio=str(tone),typography=app.load_style(),max_duration_seconds=3,
                  segments=[{'start':0,'end':3}],output_prefix='ending_fade_test',fade_out_seconds=1,
                  subtitles_enabled=True,subtitle_srt='1\n00:00:00,000 --> 00:00:03,000\n一秒结尾淡出测试\n')
    config_file=folder/'fade_config.json'
    config_file.write_text(json.dumps(config,ensure_ascii=False,indent=2),encoding='utf-8')
    renderer.render(config_file)
    metadata=max((app.ROOT/'outputs').glob('ending_fade_test_*.json'),key=lambda p:p.stat().st_mtime_ns)
    report=json.loads(metadata.read_text(encoding='utf-8'))
    video=report['output']
    brightness={}
    for label,at in [('before',1.5),('middle',2.5),('last',2.966667)]:
        # The fixed white brand/title/subtitle overlays must fade along with picture.
        raw=run(['-ss',str(at),'-i',video,'-frames:v','1','-vf','crop=1080:350:0:30','-pix_fmt','gray','-f','rawvideo','pipe:1'])
        brightness[label]=sum(raw)/len(raw)
        run(['-n','-ss',str(at),'-i',video,'-frames:v','1',str(folder/(label+'.png'))])
    def rms(at):
        samples=array('f')
        samples.frombytes(run(['-ss',str(at),'-i',video,'-t','0.04','-vn','-ac','1','-ar','16000','-f','f32le','pipe:1']))
        return math.sqrt(sum(s*s for s in samples)/len(samples))
    audio_before,audio_last=rms(1.5),rms(2.94)
    if not brightness['last']<brightness['middle']<brightness['before'] or brightness['last']>brightness['before']*.1:
        raise RuntimeError('Video fade verification failed: '+str(brightness))
    if audio_last>=audio_before*.15: raise RuntimeError('Audio fade verification failed')
    proof={'video':video,'validation':report['validation'],'ending_effect':report['ending_effect'],
           'brightness':brightness,'audio_rms_before':audio_before,'audio_rms_last':audio_last,
           'audio_note':'Verification uses a generated constant 440Hz tone, not a production soundtrack.',
           'frames':{label:str(folder/(label+'.png')) for label in brightness}}
    (folder/'fade_result.json').write_text(json.dumps(proof,ensure_ascii=False,indent=2),encoding='utf-8')
    print('FADE_PROOF '+json.dumps(proof,ensure_ascii=False),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--search',action='store_true')
    parser.add_argument('--fade',action='store_true')
    args=parser.parse_args()
    base=app.ROOT/'work'/'search_upgrade_verification';base.mkdir(parents=True,exist_ok=True)
    folder=Path(tempfile.mkdtemp(prefix=datetime.now().strftime('%Y%m%d_%H%M%S_'),dir=base))
    print('VERIFICATION_FOLDER '+str(folder),flush=True)
    if args.search: verify_search(folder)
    if args.fade: verify_fade(folder)
