"""Studio V2 orchestration; immutable per-task options and registered assets."""
import copy
import json
from pathlib import Path
import re
import sys

ROOT=Path(__file__).resolve().parents[1]
DEFAULTS={'mode':'smart30','cover_enabled':True,'cover_style':'editorial','cover_hold':.6}


def options(value):
    result={**DEFAULTS,**value}
    if set(value)-set(DEFAULTS):raise ValueError('制作选项包含未知字段。')
    if result['mode'] not in {'smart30','manual'}:raise ValueError('请选择智能 30 秒或手动时段。')
    if result['cover_style'] not in {'editorial','coral','minimal'}:raise ValueError('请选择已有封面风格。')
    if not isinstance(result['cover_enabled'],bool):raise ValueError('封面开关格式错误。')
    hold=result['cover_hold']
    if isinstance(hold,bool) or not isinstance(hold,(int,float)) or not .034<=hold<=2:raise ValueError('封面停留需为 0.034–2 秒。')
    return result


class Studio:
    def __init__(self,editor):
        self.e=editor
        defaults=copy.deepcopy(DEFAULTS)
        if not (ROOT/'tools/vision_packages/rapidocr_onnxruntime').is_dir():
            defaults.update(mode='manual',cover_enabled=False)
        self.e.state.setdefault('studio_settings',defaults)

    def snapshot(self):
        from premium_fonts import FONT_PRESETS
        return {'ok':True,'settings':copy.deepcopy(self.e.state['studio_settings']),'presets':FONT_PRESETS,
                'ocr_ready':(ROOT/'tools/vision_packages/rapidocr_onnxruntime').is_dir(),
                'semantics_ready':(ROOT/'models/clip-vit-base-patch32/manifest.json').is_file(),
                'cloud_enabled':bool(self.e.connections.policy()),'cloud_note':('DeepSeek 自动辅助已开启，仅对开启后新建的智能任务上传抽帧；有每日调用上限。' if self.e.connections.policy() else '默认本地分析。可在 API 接入页面设置 DeepSeek，未开启时不上传画面、不调用付费 API。')}

    def save(self,payload):
        value=options(payload)
        with self.e.lock:
            old=self.e.state['studio_settings'];self.e.state['studio_settings']=value
            try:self.e.save()
            except Exception:self.e.state['studio_settings']=old;raise
        return {'ok':True,'settings':value,'message':'制作策略已保存，新任务使用；历史成片和旧版本参数保留。'}

    def approval(self,pid,payload):
        from editor_server import now
        decision=payload.get('decision');reason=payload.get('reason','')
        if decision not in {'approved','rework'} or not isinstance(reason,str) or len(reason)>280:raise ValueError('请选择验收通过或需返工，原因最多 280 字。')
        with self.e.lock:
            p=self.e.board.product(pid);view=self.e.board.view(p)
            if not view['current_task_id'] or payload.get('task_id')!=view['current_task_id']:raise ValueError('成片版本已变化，请刷新后再验收。')
            p['approval']={'task_id':view['current_task_id'],'decision':decision,'reason':reason.strip(),'at':now()}
            self.e.save()
        return {'ok':True,'message':'已记录此版本验收结果；重做的新版本需重新验收。'}

    def cover_choice(self,pid,payload):
        import clip_edit
        with self.e.lock:
            p=self.e.board.product(pid);view=self.e.board.view(p)
            if view['status']=='working':raise ValueError('请等当前制作结束后再调整封面。')
            at=clip_edit.seconds(payload.get('time'))
            duration=self.e.review.source(view['source_url'],p)['duration']
            if not duration:
                from editor_server import source_id
                import product_render as renderer
                source=self.e.existing_source(source_id(view['source_url'])) if view['source_url'] else None
                if source:duration=renderer.duration(renderer.probe(source))
            if not view['source_url'] or duration and at>=duration:raise ValueError('封面时间超出原片，请先核对素材。')
            p['cover_choice']={'source_url':view['source_url'],'time':at};self.e.save()
        return {'ok':True,'message':'封面时间已保存，下次生成生效；旧成片封面不改变。'}

    def jobs(self,payload):
        job=payload.get('job','render')
        if job not in {'render','analyze'}:raise ValueError('未知制作任务。')
        ids=payload.get('ids')
        if not isinstance(ids,list) or not 1<=len(ids)<=100 or any(not isinstance(p,str) for p in ids):raise ValueError('请选择 1–100 个商品。')
        chosen=options(payload.get('options',self.e.state['studio_settings']))
        added=[];errors=[];skipped=[]
        # Keep the existing worker from taking a task before its director snapshot.
        with self.e.lock:
            for pid in dict.fromkeys(ids):
                try:
                    p=self.e.board.product(pid)
                    if self.e.board.skip_decision(p):skipped.append(pid);continue
                    if job=='render' and self.e.board.view(p)['status']=='done':
                        result=self.e.board.redo(pid,{})
                        task=next(t for t in self.e.state['tasks'] if t['id']==result['task_id'])
                    else:task=self.e.board.new_task(p,job)
                    task['director_options']=copy.deepcopy(chosen)
                    if chosen['mode']=='smart30':
                        # This explicit action is a new auto-cut, not an accidental
                        # reuse of an old manually saved range or stale subtitle.
                        task.pop('clip',None);task.pop('subtitle_srt',None);task.pop('transcription',None)
                    for key in ('director_report','cover_url','title_asset_url'):task.pop(key,None)
                    task.pop('cloud_policy',None);task.pop('cloud_review',None)
                    policy=self.e.connections.policy() if chosen['mode']=='smart30' else None
                    if policy:task['cloud_policy']=policy
                    added.append(task['id'])
                except ValueError as exc:errors.append({'id':pid,'message':str(exc)})
            self.e.save()
        self.e.wake.set()
        return {'ok':True,'added':len(added),'task_ids':added,'errors':errors,'skipped':skipped}

    def direct(self,task,source,config):
        from editor_server import source_id
        import director_assets
        prefs=options(task.get('director_options',{}))
        if prefs['mode']=='manual' and not prefs['cover_enabled'] and task.get('job_type')!='analyze':return config
        self.e.update(task,status='analyzing',progress=28,message='本地导演分析：识别黑屏、片头文字、清晰画面与最佳连续片段…')
        output=self.e.state_file.parent/('director_'+task['id']+'.json')
        command=[sys.executable,'-B',str(ROOT/'scripts/scene_director.py'),'--source',str(source),
                 '--output',str(output),'--seconds','30','--brand',task.get('brand',''),'--model',task.get('model',''),
                 '--category',(task.get('original_title','')+' '+task.get('description',''))[:400]]
        def progress(line):
            match=re.search(r'Director: (\d+)%',line)
            if match:self.e.update(task,progress=28+int(match[1])*.07)
        self.e.command(command,task,'director',timeout=600,progress_handler=progress)
        report=json.loads(output.read_text(encoding='utf-8'))
        if not re.fullmatch('[a-f0-9]{64}',report.get('key','')):raise ValueError('分析缓存标识无效。')
        if task.get('cloud_policy'):
            self.e.update(task,progress=36,message='本地分析完成，正在按已授权设置请求 DeepSeek 视觉复核…')
            report=self.e.connections.enhance(task,report)
        before=task.get('clip')
        if prefs['mode']=='manual' and not before:
            first=config['segments'][0]
            before={'start':first['start'],'end':min(report['available'],first.get('end') or first['start']+config['max_duration_seconds'])}
        chosen=report['selected'];clip={'source_url':task['selected_url'],'start':chosen['start'],'end':chosen['end']}
        # Saving analysis alone never overwrites the user's manual range.
        self.e.update(task,director_report=report)
        if report.get('needs_review') and not before:
            self.e.update(task,status='needs_review',progress=0,message='本地分析发现所选段仍有大量黑屏／片头／疑似水印，请核对分析结果或换素材；未自动擦除水印。')
            return None
        if task.get('job_type')=='analyze':
            with self.e.lock:
                product=self.e.board.by_id.get(task.get('board_product_id'))
                if product:product['source_url']=task['selected_url']
            self.e.update(task,status='analyzed',progress=100,message='本地分析完成，可查看候选段、封面和排除原因，再采用生成。')
            return None
        if not before:
            import clip_edit
            clip_edit.apply(task,clip)
            config.update(segments=[{'start':clip['start'],'end':clip['end']}],max_duration_seconds=round(clip['end']-clip['start'],3))
        else:
            # A user-edited single-video range always wins over auto selection.
            report['manual_override']=True
            frames=[f for f in report['frames'] if before['start']<=f['time']<(before['end'] or before['start']+30)]
            if frames:
                selected_frame=max(frames,key=lambda f:f['score']);report['cover_time']=selected_frame['time'];report['cover_source_frame']=selected_frame['file']
            else:report['cover_time']=before['start']
        with self.e.lock:
            product=self.e.board.by_id.get(task.get('board_product_id'))
            choice=product.get('cover_choice') if product else None
            if choice and choice['source_url']==task['selected_url'] and choice['time']<report['available']:
                report['cover_time']=choice['time'];report['manual_cover']=True
        config['director_report']=report
        if prefs['cover_enabled']:
            text={'brand_title':config.get('brand_title',''),'product_title':config.get('product_title',''),
                  'description':' '.join(config.get('description_lines',[]))}
            assets=director_assets.create(source,report,text,config['typography'],prefs['cover_style'],config.get('brand_logo'))
            config.update(assets,cover_hold=prefs['cover_hold'])
        return config

    def report(self,pid):
        with self.e.lock:
            product=self.e.board.product(pid)
            task=next((t for t in reversed(self.e.board.tasks(product)) if t.get('director_report') and t.get('selected_url')==self.e.board.view(product)['source_url']),None)
            if not task:return None
            r=copy.deepcopy(task['director_report']);prefix='/director/'+r['key']+'/'
            return {'task_id':task['id'],'selected':r['selected'],'cover_time':r['cover_time'],
                    'warnings':r['warnings'],'reason':r['reason'],'elapsed_seconds':r['elapsed_seconds'],
                    'ocr_ready':r['ocr_ready'],'cache_hit':r['cache_hit'],'needs_review':r.get('needs_review',False),
                    'semantics':r.get('semantics'),'cloud_review':task.get('cloud_review',r.get('cloud')),
                    'storyboard_url':prefix+'storyboard.jpg','frame_url':prefix+r['cover_source_frame'],
                    'cover_url':task.get('cover_url',''),'title_asset_url':task.get('title_asset_url',''),
                    'excluded':r['excluded'][:80],'source_url':task['selected_url']}

    def resolve_asset(self,path):
        match=re.fullmatch(r'/director/([a-f0-9]{64})/(storyboard\.jpg|frame_\d{3}\.jpg)',path)
        if not match:return None
        with self.e.lock:
            if not any(t.get('director_report',{}).get('key')==match[1] for t in self.e.state['tasks']):return None
        file=ROOT/'data/director_cache'/match[1]/match[2]
        return file if file.is_file() and not file.is_symlink() else None
