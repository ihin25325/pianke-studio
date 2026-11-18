"""Local visual director: measurable quality + OCR, never watermark erasure.

Runs in a separate process so vision dependencies cannot replace ASR libraries.
"""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'/'vision_packages'))
import argparse
import hashlib
import json
import math
import re
import shutil
import subprocess
import time
from collections import Counter
import cv2
import numpy as np
from PIL import Image,ImageDraw,ImageFont

VERSION=5
FFMPEG=ROOT/'tools'/'ffmpeg'/'bin'/'ffmpeg.exe'
FFPROBE=FFMPEG.with_name('ffprobe.exe')


def media_duration(source):
    r=subprocess.run([str(FFPROBE),'-v','error','-show_entries','format=duration','-of','json',str(source)],capture_output=True,timeout=30)
    if r.returncode:raise ValueError('无法读取原片时长。')
    value=float(json.loads(r.stdout)['format']['duration'])
    if not math.isfinite(value) or not .1<=value<=900:raise ValueError('本地导演支持 0.1 秒至 15 分钟的素材。')
    return value


def font(size):return ImageFont.truetype(str(ROOT/'assets/fonts/SourceHanSans/SourceHanSansCN-Bold.otf'),size)
def clean(text):return re.sub(r'[^a-z0-9\u3400-\u9fff]','',str(text).casefold())


def metrics(image,previous=None):
    gray=cv2.cvtColor(image,cv2.COLOR_BGR2GRAY)
    hist=np.bincount((gray//8).ravel(),minlength=32).astype(float);hist/=hist.sum()
    entropy=float(-np.sum(hist[hist>0]*np.log2(hist[hist>0])))
    sharp=float(cv2.Laplacian(gray,cv2.CV_64F).var())
    dark=float(np.mean(gray<22));light=float(np.mean(gray>245))
    motion=float(np.mean(cv2.absdiff(gray,previous)))/255 if previous is not None and previous.shape==gray.shape else .05
    score=40+min(25,math.log1p(sharp)*4)+min(20,entropy*4)-dark*28-light*14
    if entropy<1.2:score-=30
    return {'score':round(max(0,min(100,score)),1),'entropy':round(entropy,3),'sharpness':round(sharp,2),
            'dark':round(dark,3),'light':round(light,3),'motion':round(motion,3)},gray


def black_intervals(log,available):
    result=[]
    for start,end in re.findall(r'black_start:([\d.]+)\s+black_end:([\d.]+)',log):
        a,b=float(start),min(available,float(end))
        if b>a:result.append({'start':a,'end':b,'kind':'black','reason':'连续黑屏'})
    return result


def annotate_ocr(frames,folder,brand,model):
    try:
        from rapidocr_onnxruntime import RapidOCR
        engine=RapidOCR(intra_op_num_threads=2,inter_op_num_threads=1,det_limit_side_len=480)
    except Exception as exc:return ['本地 OCR 不可用，仅使用画面质量分析：'+str(exc)[:80]],False
    desired=[clean(brand),clean(model)];corner_hits=[]
    intro_words=re.compile(r'subscrib|youtube|follow\s+us|welcome|present[s]?|like\s+and|review\s+channel|频道|订阅|关注我们|片头',re.I)
    for index,f in enumerate(frames):
        if f['dark']>.95:continue
        image=cv2.imdecode(np.fromfile(folder/f['file'],dtype=np.uint8),cv2.IMREAD_COLOR)
        h,w=image.shape[:2]
        try:rows,_=engine(image,use_cls=False)
        except Exception:rows=[]
        texts=[];large_center=False;corners=[]
        for box,text,confidence in rows or []:
            if confidence<.60:continue
            texts.append(text);xs=[p[0] for p in box];ys=[p[1] for p in box]
            cx,cy=(min(xs)+max(xs))/2/w,(min(ys)+max(ys))/2/h
            area=(max(xs)-min(xs))*(max(ys)-min(ys))/(w*h)
            if .2<cx<.8 and .18<cy<.82 and area>.025:large_center=True
            token=clean(text)
            if token and len(token)>2 and (cx<.25 or cx>.75) and (cy<.22 or cy>.86) and area<.09:
                if not any(term and (term in token or token in term) for term in desired):corners.append(token)
        f['text']=' · '.join(texts)[:500]
        f['product_text']=any(term and len(term)>1 and term in clean(f['text']) for term in desired)
        f['intro']=bool(f['time']<min(25,frames[-1]['time']*.4) and (
            intro_words.search(f['text']) or large_center and f['entropy']<2.0))
        f['outro']=bool(f['time']>frames[-1]['time']-15 and intro_words.search(f['text']))
        f['corner_tokens']=corners;corner_hits.extend(set(corners))
        if f['product_text']:f['score']=min(100,f['score']+12)
        if f['intro'] or f['outro']:f['score']=max(0,f['score']-70)
        if index%10==0:print(f'Director: {30+int(index/max(1,len(frames))*40)}%',flush=True)
    repeated={t for t,n in Counter(corner_hits).items() if n>=max(3,len(frames)*.12)}
    for f in frames:
        f['watermark']=bool(repeated.intersection(f.get('corner_tokens',[])))
        if f['watermark']:f['score']=max(0,f['score']-35)
    warnings=[]
    if repeated:warnings.append('检测到重复角落文字，可能是频道标记／水印；只避开，不擦除。请核对：'+', '.join(sorted(repeated))[:120])
    return warnings,True


def overlap(a,b,intervals):
    spans=sorted((max(a,x['start']),min(b,x['end'])) for x in intervals if x['end']>a and x['start']<b)
    total=0;last=a
    for x,y in spans:
        if y>max(x,last):total+=y-max(x,last);last=max(last,y)
    return total


def choose_window(frames,available,seconds,bad,allowed=None):
    lo,hi=allowed or (0,available);hi=min(available,hi)
    if lo>=hi:raise ValueError('可选时间范围超出原片。')
    length=min(seconds,hi-lo)
    starts={round(lo,3),round(max(lo,hi-length),3)}
    starts.update(round(f['time'],3) for f in frames if lo<=f['time']<=hi-length)
    starts.update(round(x['end']+.12,3) for x in bad if lo<=x['end']+.12<=hi-length)
    candidates=[]
    for start in starts:
        end=start+length;sample=[f for f in frames if start<=f['time']<end]
        if not sample:continue
        blocked=overlap(start,end,bad)/length
        score=sum(f['score'] for f in sample)/len(sample)-blocked*150
        if sample[0].get('intro') or sample[0]['dark']>.9:score-=40
        # A small preference for sustained, legible shots, not flashy transitions.
        score-=sum(f['motion']>.35 for f in sample)/len(sample)*8
        candidates.append({'start':round(start,3),'end':round(end,3),'score':round(score,1),
                           'blocked_ratio':round(blocked,3)})
    if not candidates:raise ValueError('未发现可用的连续片段，请手动选择时段。')
    candidates.sort(key=lambda c:(c['blocked_ratio']<.03,c['score'],-c['start']),reverse=True)
    best=candidates[0]
    # Short sources with a long intro cannot yield 30 clean seconds. Prefer a
    # shorter clean take instead of padding with known intro/black frames.
    if best['blocked_ratio']>.08:
        cuts=sorted({lo,hi,*[max(lo,min(hi,x[k])) for x in bad for k in ('start','end')]})
        clean_runs=[]
        for a,b in zip(cuts,cuts[1:]):
            if b-a>=.5 and overlap(a,b,bad)<.05:clean_runs.append((a,b))
        for a,b in clean_runs:
            try:
                candidate,_=choose_window(frames,available,min(seconds,b-a),[],(a,b))
                candidate['shortened']=True
                candidate['rank_score']=candidate['score']+min(30,b-a)
                candidates.append(candidate)
            except ValueError:pass
        safe=[c for c in candidates if c.get('shortened')]
        if safe:best=max(safe,key=lambda c:c['rank_score'])
    return best,candidates[:5]


def storyboard(frames,folder):
    selected=frames[::max(1,math.ceil(len(frames)/48))][:48]
    width=960;tile_w=160;tile_h=116
    sheet=Image.new('RGB',(width,max(1,math.ceil(len(selected)/6))*tile_h),'#101827');draw=ImageDraw.Draw(sheet)
    for i,f in enumerate(selected):
        with Image.open(folder/f['file']) as img:
            img.thumbnail((160,90));x=i%6*tile_w;y=i//6*tile_h
            sheet.paste(img,(x+(160-img.width)//2,y))
        draw.text((x+6,y+91),f"{f['time']:.1f}s  {f['score']:.0f}",font=font(12),fill='white')
    sheet.save(folder/'storyboard.jpg',quality=86)


def analyze(source,seconds=30,brand='',model='',allowed=None,progress=None,category=''):
    source=Path(source).resolve();available=media_duration(source)
    if not .1<=seconds<=30:raise ValueError('智能片段长度需为 0.1–30 秒。')
    import local_semantics
    semantics_ready=local_semantics.ready()
    key=hashlib.sha256(json.dumps([VERSION,str(source),source.stat().st_size,source.stat().st_mtime_ns,seconds,brand,model,allowed,category,semantics_ready]).encode()).hexdigest()
    folder=ROOT/'data'/'director_cache'/key;folder.mkdir(parents=True,exist_ok=True)
    report_file=folder/'report.json'
    if report_file.is_file():
        report=json.loads(report_file.read_text(encoding='utf-8'));report['cache_hit']=True;return report
    if shutil.disk_usage(folder).free<200*1024*1024:raise ValueError('工作目录所在磁盘空间不足 200 MB，请清理工作缓存后再进行画面分析。')
    began=time.monotonic();step=max(1.5,available/120)
    # Decode once, detecting black intervals at video-frame granularity.
    command=[str(FFMPEG),'-hide_banner','-nostdin','-loglevel','info','-i',str(source),'-an',
             '-vf',f"scale=480:-2,blackdetect=d=0.25:pix_th=0.10:pic_th=0.96,select='isnan(prev_selected_t)+gte(t-prev_selected_t,{step})',showinfo",
             '-fps_mode','vfr','-frames:v','122','-q:v','3','-y',str(folder/'frame_%03d.jpg')]
    r=subprocess.run(command,capture_output=True,timeout=300)
    if r.returncode:raise RuntimeError('本地画面采样失败：'+r.stderr.decode('utf-8',errors='replace')[-500:])
    print('Director: 25%',flush=True)
    log=r.stderr.decode('utf-8',errors='replace')
    times=[float(t) for t in re.findall(r'Parsed_showinfo[^\n]*?\bn:\s*\d+[^\n]*?pts_time:([\d.]+)',log)]
    frames=[];previous=None
    for i,file in enumerate(sorted(folder.glob('frame_*.jpg'))):
        image=cv2.imdecode(np.fromfile(file,dtype=np.uint8),cv2.IMREAD_COLOR)
        m,previous=metrics(image,previous)
        frames.append({'file':file.name,'time':round(min(available-.01,times[i] if i<len(times) else i*step),3),**m})
    if not frames:raise ValueError('没有可分析的视频帧。')
    bad=black_intervals(r.stderr.decode('utf-8',errors='replace'),available)
    if frames[-1]['dark']>.96 and not any(x['start']<=frames[-1]['time']<=x['end'] for x in bad):
        a=next((f['time'] for f in reversed(frames) if f['dark']<=.96),0)+step
        if a<available:bad.append({'start':a,'end':available,'kind':'black','reason':'片尾黑屏'})
    warnings,ocr=annotate_ocr(frames,folder,brand,model)
    try:semantics=local_semantics.score(frames,folder,category)
    except Exception as exc:
        semantics={'ready':False,'reason':'本地类别识别不可用，已退回质量和 OCR 评分。'}
        warnings.append(semantics['reason']+' '+str(exc)[:100])
    for f in frames:
        for flag,label in [('intro','疑似片头文字卡'),('outro','订阅／片尾引导'),('watermark','疑似频道水印')]:
            if f.get(flag):bad.append({'start':max(0,f['time']-step/2),'end':min(available,f['time']+step/2),'kind':flag,'reason':label})
    best,candidates=choose_window(frames,available,seconds,bad,allowed)
    pool=[f for f in frames if best['start']<=f['time']<best['end'] and not any(f.get(k) for k in ('intro','outro','watermark')) and f['dark']<.9]
    if not pool:pool=[f for f in frames if best['start']<=f['time']<best['end']]
    cover=max(pool,key=lambda f:f['score']+f.get('category_similarity',0)*25-f['motion']*10)
    if best.get('shortened'):warnings.append('原片无法提供完整 30 秒干净画面，已缩短为可用片段，不填充黑屏。')
    if best['blocked_ratio']>.08:warnings.append('候选段仍包含疑似片头／水印，请人工复核或更换素材。')
    storyboard(frames,folder)
    report={'version':VERSION,'key':key,'engine':'local-visual-ocr','available':available,'seconds_requested':seconds,
            'selected':best,'alternatives':candidates,'cover_time':cover['time'],'cover_source_frame':cover['file'],
            'frames':frames,'excluded':bad,'warnings':warnings,'ocr_ready':ocr,'cache_hit':False,
            'needs_review':best['blocked_ratio']>.25,'api_calls':0,'elapsed_seconds':round(time.monotonic()-began,2),
            'semantics':semantics,
            'reason':'综合清晰度、曝光、画面信息量、OCR 文字'+('、CLIP 商品类别相似度' if semantics['ready'] else '')+'与片头／黑屏排除，选择评分最高的连续片段；本地评分不等同完整语义理解、型号或授权验证。'}
    temp=report_file.with_suffix('.tmp');temp.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8');temp.replace(report_file)
    return report


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--source',required=True);parser.add_argument('--output',required=True)
    parser.add_argument('--seconds',type=float,default=30);parser.add_argument('--brand',default='');parser.add_argument('--model',default='')
    parser.add_argument('--category',default='')
    args=parser.parse_args()
    report=analyze(args.source,args.seconds,args.brand,args.model,category=args.category)
    Path(args.output).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print('Director: 100%',flush=True)


if __name__=='__main__':main()
