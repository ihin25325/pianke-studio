"""Single, source-bound rough-cut ranges shared by UI, ASR and rendering."""
import math
import re

MAX_SOURCE=21600


def seconds(value,allow_blank=False):
    if allow_blank and (value is None or value==''): return None
    if isinstance(value,bool): raise ValueError('时间请填写秒数或 分:秒，例如 30 或 01:30。')
    if isinstance(value,str):
        value=value.strip()
        if not re.fullmatch(r'\d+(?::\d{1,2}){0,2}(?:\.\d{1,3})?',value):
            raise ValueError('时间请填写秒数、分:秒或时:分:秒，例如 30、01:30、00:01:30.5。')
        parts=value.split(':')
        if len(parts)>1 and any(float(p)>=60 for p in parts[1:]): raise ValueError('时间码中的分、秒必须小于 60。')
        value=sum(float(p)*60**i for i,p in enumerate(reversed(parts)))
    if not isinstance(value,(int,float)) or not math.isfinite(value) or not 0<=value<=MAX_SOURCE:
        raise ValueError('起止时间需在 0–21600 秒之间。')
    return round(float(value),3)


def normalize(value,canonical_url,source_duration=None):
    if not isinstance(value,dict): raise ValueError('请设置剪辑起止时间。')
    url=canonical_url(value.get('source_url',''))
    if not url: raise ValueError('请先采用对应的素材链接，再设置剪辑时间。')
    start=seconds(value.get('start',0));end=seconds(value.get('end'),allow_blank=True)
    if end is not None and not .1<=round(end-start,3)<=90:
        raise ValueError('结束需晚于开始，单条剪辑须为 0.1–90 秒；拖动滑块可快速选取。')
    if source_duration and (start>=source_duration or end is not None and end>source_duration+.1):
        raise ValueError(f'起止时间超出原片长度（{source_duration:.1f} 秒）。')
    return {'source_url':url,'start':start,'end':end}


def signature(value):
    return (value.get('source_url',''),value.get('start',0),value.get('end')) if value else ('',0,None)


def apply(task,clip):
    old=task.get('clip')
    if signature(old)!=signature(clip):
        task.pop('subtitle_srt',None);task.pop('transcription',None)
    if clip:
        task['clip']=dict(clip)
        if clip['end'] is not None: task['max_duration_seconds']=round(clip['end']-clip['start'],3)
    else: task.pop('clip',None)
