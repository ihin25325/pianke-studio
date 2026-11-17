"""Local, unbilled speech recognition; safe SRT parsing and deterministic subtitle layout."""
from __future__ import annotations
import argparse
from datetime import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
MODEL=ROOT/'models'/'faster-whisper-base'
PACKAGES=ROOT/'tools'/'asr_packages'
LANGUAGES={'auto','zh','en','de','fr','es','ja','ko','it','ru','pt'}


def ready():
    return (PACKAGES/'faster_whisper').is_dir() and all((MODEL/name).is_file() for name in ('model.bin','config.json','tokenizer.json','vocabulary.txt'))


def language_code(value):
    if value not in LANGUAGES: raise ValueError('请选择支持的语音语言。')
    return value


def parse_srt(value):
    if not isinstance(value,str) or len(value)>40000:
        raise ValueError('字幕内容过长，最多 40000 个字符。')
    if not value.strip(): return []
    entries=[]
    pattern=r'^(\d{1,2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*(\d{1,2}):(\d{2}):(\d{2})[,.](\d{3})$'
    def seconds(parts):
        h,m,s,ms=map(int,parts)
        if m>=60 or s>=60: raise ValueError('字幕时间格式不正确。')
        return h*3600+m*60+s+ms/1000
    for block in re.split(r'\n\s*\n',value.replace('\r','').lstrip('\ufeff').strip()):
        lines=block.strip().splitlines()
        if lines and re.fullmatch(r'\d+',lines[0].strip()): lines=lines[1:]
        match=re.fullmatch(pattern,lines[0].strip()) if lines else None
        if not match or len(lines)<2: raise ValueError('请使用 SRT 字幕格式，保留每段时间码。')
        start,end=seconds(match.groups()[:4]),seconds(match.groups()[4:])
        text=re.sub(r'\s+',' ',' '.join(lines[1:])).strip()
        if not text or len(text)>300 or not 0<=start<end<=90.25:
            raise ValueError('每段字幕需有文字、时间在 90 秒以内，且结束晚于开始。')
        if entries and start<entries[-1]['end']-.025:
            raise ValueError('字幕时间有重叠，请调整上一段结束或下一段开始时间。')
        entries.append({'start':start,'end':end,'text':text})
        if len(entries)>300: raise ValueError('每条视频最多 300 段字幕。')
    return entries


def srt_stamp(value):
    value=round(value*1000)
    h,value=divmod(value,3600000);m,value=divmod(value,60000);s,ms=divmod(value,1000)
    return f'{h:02}:{m:02}:{s:02},{ms:03}'


def write_srt(entries):
    return '\n\n'.join(f"{i+1}\n{srt_stamp(item['start'])} --> {srt_stamp(item['end'])}\n{item['text']}" for i,item in enumerate(entries))+'\n' if entries else ''


def speech_ass(entries,total,style=None,layout_info=None):
    import product_render as renderer
    from typography import normalize_style
    style=normalize_style(style or {})
    anchor,x={'left':(1,70),'center':(2,540),'right':(3,1010)}[style['subtitle_align']]
    y=style['subtitle_y']
    result=''
    boxes=[]
    clipped=False
    for item in entries:
        start,end=max(0,item['start']),min(total,item['end'])
        if end<=start: continue
        lines,cut=renderer.wrap_overlay(item['text'],66,'Source Han Sans CN',2,max_width=940)
        clipped=clipped or cut
        height=len(lines)*66*renderer.ass_size_scale('Source Han Sans CN')*1.28
        width=max((renderer.text_width(line,66,'Source Han Sans CN') for line in lines),default=0)
        left=x if anchor==1 else x-width if anchor==3 else x-width/2
        boxes.append({'left':left,'right':left+width,'top':y-height,'bottom':y})
        # Bottom anchor matches the position shown by the editor and export.
        text=renderer.ass_text('\n'.join(lines))
        result+=(f"Dialogue: 2,{renderer.ass_timestamp(start)},{renderer.ass_timestamp(end)},Speech,,0,0,0,,"
                 f"{{\\an{anchor}\\pos({x},{y})"+r"\fs66\fnSource Han Sans CN\c&HFFFFFF&\b0\bord2.4\shad0}"+text+'\n')
    if layout_info is not None and boxes:
        layout_info['subtitle']={'left':min(b['left'] for b in boxes),'right':max(b['right'] for b in boxes),
                                 'top':min(b['top'] for b in boxes),'bottom':y}
        layout_info['clipped']=clipped
    return result


def group_words(segments,total):
    result=[]
    for segment in segments:
        if segment.no_speech_prob>.75 or segment.avg_logprob < -1.15: continue
        words=segment.words or []
        if not words:
            words=[type('Word',(),{'start':segment.start,'end':segment.end,'word':segment.text})()]
        group=[]
        def flush():
            if not group: return
            text=''.join(word.word for word in group).strip()
            start=max(0,float(group[0].start),result[-1]['end'] if result else 0)
            end=min(total,float(group[-1].end))
            if end-start>.06 and text: result.append({'start':round(start,3),'end':round(end,3),'text':text[:300]})
        for word in words:
            if not all(isinstance(value,(int,float)) and math.isfinite(value) for value in (word.start,word.end)): continue
            prospective=''.join(w.word for w in group)+word.word
            limit=30 if re.search(r'[\u3400-\u9fff]',prospective) else 72
            if group and (word.end-group[0].start>4.8 or word.start-group[-1].end>.7 or len(prospective)>limit):
                flush();group=[]
            group.append(word)
        flush()
    return result[:300]


def transcribe(config_file,output_file,language='auto'):
    language_code(language)
    if not ready(): raise RuntimeError('本地语音模型未准备好，请双击 D 盘项目中的“安装语音字幕.bat”。')
    import product_render as renderer
    config=renderer.read_json(Path(config_file))
    video=renderer.resolve_input(config['source'])
    video_info=renderer.probe(video)
    source=renderer.resolve_input(config.get('source_audio')) or video
    if source==video and not renderer.stream(video_info,'audio') and video.with_name('audio.m4a').is_file():
        source=video.with_name('audio.m4a')
    info=renderer.probe(source)
    if not renderer.stream(info,'audio'): raise ValueError('原片没有音轨，无法识别语音。')
    total_available=min(renderer.duration(video_info),renderer.duration(info,'audio'))
    parts=renderer.choose_segments(config,total_available)
    total=sum(end-start for start,end in parts)
    term_prompt='Product names: '+', '.join(str(config.get(key,'')) for key in ('brand_title','product_title'))
    key=hashlib.sha256(json.dumps({'path':str(source),'size':source.stat().st_size,'mtime':source.stat().st_mtime_ns,
        'parts':parts,'language':language,'model':'base','version':2,'term_prompt':term_prompt},sort_keys=True).encode()).hexdigest()
    cache=ROOT/'data'/'speech_cache'/key
    cache.mkdir(parents=True,exist_ok=True)
    cached=cache/'transcript.json'
    output_file=Path(output_file);output_file.parent.mkdir(parents=True,exist_ok=True)
    if cached.is_file():
        result=json.loads(cached.read_text(encoding='utf-8'))
        result['cache_hit']=True
        output_file.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        print('Speech: 100%',flush=True);return result
    wav=cache/'audio.wav'
    graph=[];inputs=[]
    for i,(start,end) in enumerate(parts):
        graph.append(f'[0:a:0]atrim=start={start:.6f}:end={end:.6f},asetpts=PTS-STARTPTS[a{i}]');inputs.append(f'[a{i}]')
    if len(parts)>1:
        graph.append(''.join(inputs)+f'concat=n={len(parts)}:v=0:a=1[out]');out='[out]'
    else: out='[a0]'
    result=subprocess.run([str(renderer.FFMPEG),'-hide_banner','-loglevel','error','-nostdin','-y','-i',str(source),'-filter_complex',';'.join(graph),
                           '-map',out,'-ar','16000','-ac','1','-c:a','pcm_s16le',str(wav)],capture_output=True,timeout=90)
    if result.returncode: raise RuntimeError('提取语音失败：'+result.stderr.decode('utf-8',errors='replace')[-300:])
    for name,value in {'HF_HOME':str(ROOT/'cache/huggingface'),'HF_HUB_OFFLINE':'1','HF_HUB_DISABLE_TELEMETRY':'1','TOKENIZERS_PARALLELISM':'false'}.items(): os.environ[name]=value
    sys.path.insert(0,str(PACKAGES))
    from faster_whisper import WhisperModel
    begin=time.monotonic()
    model=WhisperModel(str(MODEL),device='cpu',compute_type='int8',cpu_threads=4,num_workers=1,local_files_only=True)
    print('Speech: 10%',flush=True)
    segments,metadata=model.transcribe(str(wav),language=None if language=='auto' else language,task='transcribe',
        beam_size=3,word_timestamps=True,vad_filter=True,vad_parameters={'min_silence_duration_ms':500},condition_on_previous_text=False,
        temperature=0,log_prob_threshold=-1.0,no_speech_threshold=.6,initial_prompt=term_prompt)
    collected=[]
    for segment in segments:
        collected.append(segment)
        print(f'Speech: {min(95,10+int(segment.end/max(total,.1)*85))}%',flush=True)
    cues=group_words(collected,total)
    srt=write_srt(cues)
    parse_srt(srt)
    transcript={'engine':'faster-whisper','engine_version':'1.2.1','model':'base','device':'cpu/int8','language':metadata.language,
                'duration':total,'segments':cues,'srt':srt,'elapsed_seconds':round(time.monotonic()-begin,2),'api_calls':0,'cache_hit':False,
                'created_at':datetime.now().astimezone().isoformat()}
    temporary=cache/'transcript.tmp'
    temporary.write_text(json.dumps(transcript,ensure_ascii=False,indent=2),encoding='utf-8');os.replace(temporary,cached)
    output_file.write_text(json.dumps(transcript,ensure_ascii=False,indent=2),encoding='utf-8')
    print('Speech: 100%',flush=True)
    return transcript


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--config',required=True);parser.add_argument('--output',required=True);parser.add_argument('--language',default='auto');parser.add_argument('--target',default='original')
    args=parser.parse_args()
    try:
        from chinese_translation import target_code,translate
        target_code(args.target)
        result=transcribe(args.config,args.output,args.language)
        config=json.loads(Path(args.config).read_text(encoding='utf-8-sig'))
        context={key:config.get(key,'') for key in ('brand_title','product_title','description_lines')}
        result=translate(result,args.target,progress=lambda percent: print(f'Translation: {percent}%',flush=True),context=context)
        Path(args.output).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    except Exception as error: print(str(error),file=sys.stderr,flush=True);return 1
    return 0


if __name__=='__main__': raise SystemExit(main())
