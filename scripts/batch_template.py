"""Source-independent batch range. Individual source-bound cuts take priority."""
import clip_edit


def normalize(payload, current=None):
    current=current or {}
    if 'clip_start_seconds' in payload or 'clip_end_seconds' in payload:
        start=clip_edit.seconds(payload.get('clip_start_seconds',current.get('clip_start_seconds',0)))
        end=clip_edit.seconds(payload.get('clip_end_seconds',current.get('clip_end_seconds')),allow_blank=True)
    elif 'max_duration_seconds' in payload:
        start=0
        end=clip_edit.seconds(payload['max_duration_seconds'])
    else:
        start=clip_edit.seconds(current.get('clip_start_seconds',0))
        end=clip_edit.seconds(current.get('clip_end_seconds',current.get('max_duration_seconds',90)),allow_blank=True)
    length=round(end-start,3) if end is not None else 90
    if not .1<=length<=90:
        raise ValueError('批量模板：结束时间须晚于起始时间，片段长度为 0.1–90 秒；结束留空则从起点最多取 90 秒。')
    return {'clip_start_seconds':start,'clip_end_seconds':end,'max_duration_seconds':length}


def snapshot(settings):
    values=normalize({},settings)
    return {'start':values['clip_start_seconds'],'end':values['clip_end_seconds']}
