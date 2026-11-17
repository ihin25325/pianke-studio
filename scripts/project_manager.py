"""Persistent product-level todo board over the existing, append-only video jobs."""
from __future__ import annotations
import copy
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
from urllib.parse import parse_qs,quote,unquote,urlparse
import uuid

import excel_import
import skip_rules
import clip_edit
import batch_template

ROOT=Path(__file__).resolve().parents[1]
ACTIVE={'queued','searching','downloading','analyzing','transcribing','rendering'}
LABELS={'todo':'待制作','working':'处理中','done':'已完成','failed':'需要处理','needs_review':'待确认素材','linked':'已收集链接','ready':'素材已下载'}


def now(): return datetime.now().astimezone().isoformat(timespec='seconds')
def clean(value): return re.sub(r'\s+',' ',str(value or '')).strip()
def title_key(value): return re.sub(r'[^a-z0-9\u3400-\u9fff]','',clean(value).lower())
def key_for(pid,title): return 'item:'+pid if pid else 'title:'+title_key(title)


class ProjectManager:
    def __init__(self,editor):
        self.e=editor
        migration=editor.state.get('board_version')!=2
        rules_missing='skip_rules' not in editor.state
        if migration and editor.state_file.is_file():
            backup=editor.state_file.parent/'backups'
            backup.mkdir(parents=True,exist_ok=True)
            shutil.copy2(editor.state_file,backup/f'queue_before_board_{datetime.now():%Y%m%d_%H%M%S_%f}.json')
        if rules_missing and editor.state_file.is_file():
            backup=editor.state_file.parent/'backups';backup.mkdir(parents=True,exist_ok=True)
            shutil.copy2(editor.state_file,backup/f'queue_before_skip_rules_{datetime.now():%Y%m%d_%H%M%S_%f}.json')
        editor.state.setdefault('products',[])
        editor.state.setdefault('imports',[])
        self.by_id={p['id']:p for p in editor.state['products']}
        self.by_key={p['key']:p for p in editor.state['products']}
        for task in editor.state['tasks']: self.attach(task)
        editor.state['skip_rules']=skip_rules.normalize(editor.state.get('skip_rules',skip_rules.defaults()))
        changed=self.cancel_skipped_pending()
        editor.state['board_version']=2
        if migration or rules_missing or changed: editor.save()

    def skip_decision(self,product,rules=None):
        if product.get('skip_override')=='search': return None
        if product.get('skip_override')=='skip': return {'label':'手动免检索','keyword':'单品定义','source':'manual'}
        return skip_rules.match(product.get('title',''),rules if rules is not None else self.e.state['skip_rules'])

    def cancel_skipped_pending(self):
        changed=0
        for task in self.e.state['tasks']:
            product=self.by_id.get(task.get('board_product_id'))
            if not product or not self.skip_decision(product): continue
            if task['status']=='queued':
                task.update(status='skipped',progress=100,message='配件免检索，已归入已完成；未生成视频。',updated_at=now());changed+=1
            if task.get('transcription',{}).get('status')=='queued':
                task['transcription'].update(status='skipped',message='配件免制作，已跳过排队的字幕任务。');changed+=1
        return changed

    def skip_task_if_needed(self,task):
        with self.e.lock:
            if task['status']=='skipped': return True
            product=self.by_id.get(task.get('board_product_id')) or {'title':task.get('original_title') or task.get('title','')}
            if not self.skip_decision(product): return False
            self.e.update(task,status='skipped',progress=100,message='配件免检索，已归入已完成；未生成视频。')
            return True

    def rules_preview(self,payload=None):
        rules=skip_rules.normalize(payload['rules']) if payload and 'rules' in payload else copy.deepcopy(self.e.state['skip_rules'])
        with self.e.lock:
            hits=[];newly=0;released=0
            for product in self.e.state['products']:
                decision=self.skip_decision(product,rules);old=self.skip_decision(product)
                if decision: hits.append({'id':product['id'],'title':product['title'],**decision})
                newly+=bool(decision and not old);released+=bool(old and not decision)
            busy=self.e.worker_busy or any(t['status'] in ACTIVE-{'queued'} or t.get('transcription',{}).get('status')=='transcribing' for t in self.e.state['tasks'])
            return {'ok':True,'rules':rules,'defaults':skip_rules.defaults(),'hits':hits[:200],'hit_count':len(hits),
                    'newly_skipped':newly,'released':released,'total':len(self.e.state['products']),'can_apply':not busy,
                    'message':'当前任务处理完后才能保存规则，可先暂停后续队列。' if busy else '命中商品归入已完成并标注配件跳过，不生成视频。'}

    def save_rules(self,payload):
        rules=skip_rules.normalize(payload.get('rules'))
        with self.e.lock:
            preview=self.rules_preview({'rules':rules})
            if not preview['can_apply']: raise ValueError(preview['message'])
            folder=self.e.state_file.parent/'backups';folder.mkdir(parents=True,exist_ok=True)
            backup=folder/f'queue_before_rules_change_{datetime.now():%Y%m%d_%H%M%S_%f}.json'
            with backup.open('x',encoding='utf-8') as handle:
                json.dump(self.e.state,handle,ensure_ascii=False,indent=2);handle.flush();os.fsync(handle.fileno())
            previous=copy.deepcopy(self.e.state)
            try:
                self.e.state['skip_rules']=rules;cancelled=self.cancel_skipped_pending();self.e.save()
            except Exception:
                self.e.state=previous;self.by_id={p['id']:p for p in previous['products']};self.by_key={p['key']:p for p in previous['products']}
                raise
        return {'ok':True,'hit_count':preview['hit_count'],'cancelled':cancelled,'backup_file':str(backup),
                'message':f'规则已保存，{preview["hit_count"]} 个商品免检索归入已完成。旧视频和历史记录保留。'}

    def set_skip(self,pid,payload):
        mode=payload.get('mode')
        if mode not in {'auto','skip','search'}: raise ValueError('免检索方式无效。')
        with self.e.lock:
            if self.e.worker_busy: raise ValueError('请等当前任务完成后修改单品定义。')
            product=self.product(pid)
            if any(t['status'] in ACTIVE-{'queued'} or t.get('transcription',{}).get('status')=='transcribing' for t in self.tasks(product)):
                raise ValueError('该商品正在处理，请完成后再修改。')
            previous=copy.deepcopy(self.e.state)
            try:
                product['skip_override']=mode
                if mode=='search': product['manual_state']='todo'
                else: product.pop('manual_state',None)
                product['updated_at']=now();self.cancel_skipped_pending();self.e.save()
            except Exception:
                self.e.state=previous;self.by_id={p['id']:p for p in previous['products']};self.by_key={p['key']:p for p in previous['products']}
                raise
        return {'ok':True,'message':{'auto':'已恢复按全局规则判断。','skip':'已设为免检索并归入已完成；不会生成视频。','search':'已移回待办，此商品不再受免检索关键词影响；尚未开始制作。'}[mode]}

    def attach(self,task,product=None):
        if product is None: product=self.by_id.get(task.get('board_product_id'))
        if product is None and task.get('edited_from'):
            parent=next((t for t in self.e.state['tasks'] if t['id']==task['edited_from']),None)
            if parent: product=self.by_id.get(parent.get('board_product_id'))
        pid=str(task.get('product_id') or '')
        title=task.get('original_title') or task['title']
        key=key_for(pid,title)
        product=product or self.by_key.get(key)
        if product is None:
            product=self.create(pid,title,task.get('taobao_url',''))
        task['board_product_id']=product['id']
        return product

    def create(self,pid,title,url='',**fields):
        key=key_for(pid,title)
        product={'id':hashlib.sha256(key.encode()).hexdigest()[:12],'key':key,'product_id':pid,
                 'title':title,'product_url':url,'created_at':now(),'updated_at':now(),'batch_ids':[],**fields}
        self.e.state['products'].append(product);self.by_id[product['id']]=product;self.by_key[key]=product
        return product

    def product(self,pid):
        product=self.by_id.get(pid)
        if product is None: raise ValueError('商品不存在，请刷新列表。')
        return product

    def tasks(self,product):
        return [t for t in self.e.state['tasks'] if t.get('board_product_id')==product['id']]

    def output_exists(self,task):
        file=self.e.resolve_media(task.get('output_url',''))
        return file is not None and file.suffix=='.mp4' and file.stat().st_size>1000

    def summary(self,task):
        has_file=self.output_exists(task)
        return {key:task.get(key,'') for key in ('id','title','status','progress','message','created_at','updated_at','selected_url','metadata_url','subtitle_url','job_type','redone_from')} | {
            'output_url':task.get('output_url','') if has_file else '', 'has_file':has_file,
            'job_type':task.get('job_type','render')}

    def view(self,product):
        tasks=self.tasks(product)
        active=next((t for t in reversed(tasks) if t['status'] in ACTIVE),None)
        renders=[t for t in tasks if t.get('job_type','render')=='render']
        good=[t for t in renders if t['status']=='completed' and self.output_exists(t)]
        latest=tasks[-1] if tasks else None
        status='todo';reason='尚未开始制作'
        skipped=self.skip_decision(product)
        if active: status='working';reason=active.get('message','正在处理')
        elif skipped: status='done';reason=f'免检索：{skipped["label"]} · 命中“{skipped["keyword"]}”；未生成视频。'
        elif product.get('manual_state')=='todo': reason='已移回待办'
        elif product.get('manual_state')=='done': status='done';reason='手动标记完成'
        elif latest and latest['status'] in {'failed','needs_review'}:
            status=latest['status'];reason=latest.get('message','需要确认')
        elif good: status='done';reason='已有可用成片'
        elif latest and latest['status']=='completed' and latest.get('job_type','render')=='render':
            status='failed';reason='记录中的成片文件不存在，请重新制作。'
        elif latest and latest['status']=='downloaded': status='ready';reason='原始素材已下载，等待制作'
        elif latest and latest['status']=='links_ready': status='linked';reason='已收集匹配素材，可确认后制作'
        elif latest and latest['status']=='analyzed': status='ready';reason='智能选片已完成，可预览并采用生成'
        source=product.get('source_url') or next((t['selected_url'] for t in reversed(tasks) if t.get('selected_url')),'')
        current=good[-1] if good else None
        approval=product.get('approval') if current and product.get('approval',{}).get('task_id')==current['id'] else None
        return {key:product.get(key,'') for key in ('id','product_id','title','product_url','existing_video','created_at','batch_ids','search_name','clip','product_image')} | {
            'status':status,'status_label':('手动跳过' if skipped['source']=='manual' else '配件跳过') if skipped and status=='done' else LABELS[status],'message':reason,'source_url':source,'version_count':len(good),
            'task_count':len(tasks),'current_task_id':current['id'] if current else '',
            'latest_task_id':latest['id'] if latest else '', 'output_url':current.get('output_url','') if current else '',
            'cover_url':current.get('cover_url','') if current else '',
            'approval':copy.deepcopy(approval),'needs_approval':bool(current and (not approval or approval['decision']!='approved')),
            'progress':active.get('progress',0) if active else 100 if status=='done' else 0,
            'updated_at':(latest.get('updated_at') or latest.get('created_at')) if latest else product['updated_at'],
            'manual_done':product.get('manual_state')=='done','skipped':bool(skipped),'skip_reason':skipped,
            'skip_mode':product.get('skip_override','auto')}

    def snapshot(self,query):
        with self.e.lock:
            for task in self.e.state['tasks']: self.attach(task)
            batch=query.get('batch',[''])[0]
            rows=[self.view(p) for p in self.e.state['products'] if not batch or batch in p.get('batch_ids',[])]
            counts={'all':len(rows),'todo':sum(p['status'] in {'todo','linked','ready'} for p in rows),
                    'working':sum(p['status']=='working' for p in rows),'done':sum(p['status']=='done' for p in rows),
                    'attention':sum(p['status'] in {'failed','needs_review'} for p in rows)}
            counts['skipped']=sum(p['status']=='done' and p['skipped'] for p in rows)
            counts['generated']=sum(bool(p['output_url']) for p in rows)
            counts['review']=sum(p['needs_approval'] for p in rows)
            view=query.get('view',['todo'])[0];search=title_key(query.get('q',[''])[0])
            def included(row):
                group={'todo':{'todo','linked','ready'},'attention':{'failed','needs_review'},'links':{'linked','ready','needs_review'}}.get(view,{view})
                return (view=='all' or view=='review' and row['needs_approval'] or row['status'] in group) and (not search or search in title_key(row['title']+' '+row['product_id']))
            rows=[p for p in rows if included(p)]
            if view in {'working','done','attention'}: rows.sort(key=lambda p:p['updated_at'],reverse=True)
            page=max(1,int(query.get('page',['1'])[0]));pages=max(1,(len(rows)+24)//25);page=min(page,pages)
            return {'ok':True,'items':rows[(page-1)*25:page*25],'total':len(rows),'page':page,'pages':pages,'counts':counts,
                'download_count':len(self.e.state.get('export_downloads',[])),
                'batches':copy.deepcopy(list(reversed(self.e.state['imports']))),'settings':copy.deepcopy(self.e.state['settings']),
                'paused':self.e.state['paused'],'worker_busy':self.e.worker_busy,'storage_root':str(ROOT),
                'task_map':{t['id']:t.get('board_product_id') for t in self.e.state['tasks']},
                'speech_ready':__import__('speech_subtitles').ready(),'translation_ready':__import__('chinese_translation').ready()}

    def detail(self,pid):
        with self.e.lock:
            product=self.product(pid);tasks=self.tasks(product)
            candidates=[];seen=set()
            for task in reversed(tasks):
                for candidate in task.get('candidates',[]):
                    if candidate['url'] in seen: continue
                    seen.add(candidate['url']);candidates.append(candidate)
            report=next((copy.deepcopy(t['search_report']) for t in reversed(tasks) if t.get('search_report')),None)
            return {'ok':True,'product':self.view(product),'history':[self.summary(t) for t in reversed(tasks)],'candidates':candidates[:30],'search_report':report,'review':self.e.review.snapshot(product),
                    'director':self.e.studio.report(pid) if hasattr(self.e,'studio') else None}

    def reset_preview(self):
        with self.e.lock:
            busy=self.e.worker_busy or getattr(getattr(self.e,'downloads',None),'busy',False) or any(
                t['status'] in ACTIVE-{'queued'} or t.get('transcription',{}).get('status')=='transcribing'
                for t in self.e.state['tasks'])
            counts={key:len(self.e.state[key]) for key in ('products','tasks','imports')}
            revision=hashlib.sha256(json.dumps(self.e.state,ensure_ascii=False,sort_keys=True).encode('utf-8')).hexdigest()
            return {'ok':True,'counts':counts,'revision':revision,'can_clear':not busy and any(counts.values()),
                    'reason':'当前有任务正在处理，请等本条完成后再清空；可先暂停后续队列。' if busy else
                             '清单已经为空，可以直接上传新表格。' if not any(counts.values()) else ''}

    def reset(self,payload):
        if payload.get('confirmation')!='清空所有清单': raise ValueError('请输入“清空所有清单”后确认。')
        with self.e.lock:
            preview=self.reset_preview()
            if payload.get('revision')!=preview['revision']: raise ValueError('清单已发生变化，请重新打开“清空清单”窗口核对。')
            if not preview['can_clear']:
                if not any(preview['counts'].values()): return {'ok':True,'message':preview['reason'],'removed':preview['counts']}
                raise ValueError(preview['reason'])
            # The complete state is flushed to a new, exclusive backup before any reset.
            folder=self.e.state_file.parent/'backups';folder.mkdir(parents=True,exist_ok=True)
            backup=folder/f'queue_before_clear_{datetime.now():%Y%m%d_%H%M%S_%f}_{uuid.uuid4().hex[:6]}.json'
            previous=self.e.state
            with backup.open('x',encoding='utf-8') as handle:
                json.dump(previous,handle,ensure_ascii=False,indent=2)
                handle.flush();os.fsync(handle.fileno())
            previous_id,previous_key=self.by_id,self.by_key
            self.e.state={**previous,'tasks':[],'products':[],'imports':[],'paused':False,'export_downloads':[],'export_batches':[],
                          'last_reset':{'at':now(),'backup_file':str(backup),'removed':preview['counts']}}
            self.by_id={};self.by_key={}
            try: self.e.save()
            except Exception:
                self.e.state=previous;self.by_id,self.by_key=previous_id,previous_key
                raise
        self.e.wake.set()
        return {'ok':True,'removed':preview['counts'],'backup_file':str(backup),
                'message':'已清空所有待办、已办和导入批次。原片、成片及字体 / 字幕设置保留，可以重新上传 Excel。'}

    def new_task(self,product,job='render'):
        if job not in {'render','collect','download','analyze'}: raise ValueError('未知任务类型。')
        if self.skip_decision(product): raise ValueError('此商品已定义为免检索。请先改为“仍需制作”。')
        if any(t['status'] in ACTIVE for t in self.tasks(product)): raise ValueError('这个商品已经在处理中。')
        if sum(t['status'] in ACTIVE for t in self.e.state['tasks'])>=100: raise ValueError('处理队列最多 100 条，请稍后再添加。')
        source=self.view(product)['source_url']
        text=product['title']+(' | '+product['description'] if product.get('description') else '')+(' | '+source if source else '')
        task=self.e.parse_line(text)
        task.update(product_id=product.get('product_id',''),original_title=product['title'],taobao_url=product.get('product_url',''),job_type=job)
        task.update(status='queued',message='等待检索与制作。',search_name=product.get('search_name',''))
        if job=='collect': task.update(selected_url='',message='等待重新检索素材链接；不会只复用旧链接。')
        settings=self.e.state['settings']
        task.update(max_duration_seconds=settings.get('max_duration_seconds',90),text_scale=settings.get('text_scale',1.1),
                    typography=copy.deepcopy(self.e.typography),subtitles_enabled=settings.get('subtitles_enabled',True),
                    subtitle_language=settings.get('subtitle_language','auto'),subtitle_target=settings.get('subtitle_target','zh'))
        task['batch_clip']=batch_template.snapshot(settings)
        if source and job!='collect': task['selected_url']=source
        if job=='render':self.apply_product_clip(task,product)
        self.e.state['tasks'].append(task);self.attach(task,product)
        product.pop('manual_state',None);product['updated_at']=now()
        return task

    def bulk(self,payload):
        ids=payload.get('ids');job=payload.get('job','render')
        if not isinstance(ids,list) or not 1<=len(ids)<=100 or any(not isinstance(item,str) for item in ids): raise ValueError('请勾选 1–100 个商品。')
        if job not in {'render','collect','download'}: raise ValueError('未知任务类型。')
        added=[];errors=[];skipped=[]
        with self.e.lock:
            for pid in dict.fromkeys(ids):
                try:
                    product=self.product(pid)
                    if self.skip_decision(product): skipped.append(pid);continue
                    if job=='render' and self.view(product)['status']=='done': raise ValueError('已完成的商品请使用“重做”，避免重复制作。')
                    task=self.new_task(product,job);added.append(task['id'])
                except ValueError as error: errors.append({'id':pid,'message':str(error)})
            self.e.save()
        self.e.wake.set()
        return {'ok':True,'added':len(added),'task_ids':added,'errors':errors,'skipped':skipped}

    def redo(self,pid,payload):
        with self.e.lock:
            product=self.product(pid);tasks=self.tasks(product)
            if self.skip_decision(product): raise ValueError('此商品免检索、不自动制作。请先在详情改为“仍需制作”。')
            if any(t['status'] in ACTIVE or t.get('transcription',{}).get('status') in {'queued','transcribing'} for t in tasks): raise ValueError('该商品已有任务，请等处理完成。')
            if sum(t['status'] in ACTIVE for t in self.e.state['tasks'])>=100: raise ValueError('处理队列已满。')
            source_id=payload.get('source_task_id')
            original=next((t for t in reversed(tasks) if (t['id']==source_id if source_id else t.get('job_type','render')=='render')),None)
            if source_id and original is None: raise ValueError('指定的版本不属于该商品。')
            if original is None: return self.bulk({'ids':[pid],'job':'render'})
            mode=payload.get('mode','same')
            if mode not in {'same','new_source'}: raise ValueError('重做方式无效。')
            task=copy.deepcopy(original)
            task.update(id=uuid.uuid4().hex[:12],status='queued',progress=0,message='快捷重做已排队，旧版视频保留。',
                        output_url='',metadata_url='',subtitle_url='',created_at=now(),updated_at=now(),job_type='render',redone_from=original['id'])
            if task.get('transcription',{}).get('status')!='ready': task.pop('transcription',None)
            if mode=='new_source':
                task.update(selected_url='',candidates=[])
                task['search_name']=product.get('search_name','')
                task.pop('transcription',None);task.pop('subtitle_srt',None)
                product.pop('source_url',None)
            elif product.get('source_url') and product['source_url']!=task.get('selected_url'):
                task.update(selected_url=product['source_url'],candidates=[])
                task.pop('transcription',None);task.pop('subtitle_srt',None)
            self.apply_product_clip(task,product)
            self.e.state['tasks'].append(task);self.attach(task,product)
            product.pop('manual_state',None);product['updated_at']=now()
            self.e.save()
        self.e.wake.set()
        return {'ok':True,'added':1,'task_id':task['id'],'message':'已开始重做，不需要重新复制商品。旧成片保留。'}

    def set_source(self,pid,payload):
        from editor_server import video_url as youtube_url
        with self.e.lock:
            product=self.product(pid)
            if any(t['status'] in ACTIVE for t in self.tasks(product)): raise ValueError('请等待当前任务完成后再更换素材。')
            url=youtube_url(payload.get('url',''))
            if not url: raise ValueError('请输入公开 HTTPS 视频页面、分享短链或视频直链。')
            if self.view(product)['source_url']!=url:product.pop('clip',None)
            product.update(source_url=url,updated_at=now());self.e.save()
        return {'ok':True,'message':'素材链接已绑定。点击“开始制作”或“重做”后使用，不会立即下载。'}

    def apply_product_clip(self,task,product):
        clip=product.get('clip')
        if clip and clip.get('source_url')!=task.get('selected_url'):clip=None
        clip_edit.apply(task,clip)

    def validated_clip(self,product,payload):
        from editor_server import video_url as youtube_url
        source=self.view(product)['source_url']
        clip=clip_edit.normalize(payload,youtube_url,self.e.review.source(source,product)['duration'])
        if not source or clip['source_url']!=source:raise ValueError('素材链接已变化，请刷新并为当前视频重新设置起止时间。')
        return clip

    def save_clip(self,pid,payload):
        with self.e.lock:
            product=self.product(pid)
            if any(t['status'] in ACTIVE for t in self.tasks(product)):raise ValueError('该商品正在处理，请完成后再修改起止时间。')
            clip=self.validated_clip(product,payload)
            before=copy.deepcopy(product);product.update(clip=clip,updated_at=now())
            try:self.e.save()
            except Exception:
                product.clear();product.update(before);raise
        return {'ok':True,'clip':clip,'message':'起止时间已保存；生成新版时生效，旧成片不变。'}

    def generate(self,pid,payload):
        with self.e.lock:
            product=self.product(pid)
            if self.skip_decision(product):raise ValueError('该商品已免检索，请先改为仍需制作。')
            if any(t['status'] in ACTIVE or t.get('transcription',{}).get('status') in {'queued','transcribing'} for t in self.tasks(product)):
                raise ValueError('该商品已有任务，请等本条完成。')
            if sum(t['status'] in ACTIVE for t in self.e.state['tasks'])>=100:raise ValueError('处理队列已满。')
            clip=self.validated_clip(product,payload)
            before_product=copy.deepcopy(product);before_tasks=list(self.e.state['tasks'])
            product.update(clip=clip,updated_at=now())
            try:
                original=next((t for t in reversed(self.tasks(product)) if t.get('job_type','render')=='render'),None)
                if original:
                    task=copy.deepcopy(original)
                    task.update(id=uuid.uuid4().hex[:12],selected_url=clip['source_url'],status='queued',progress=0,
                        output_url='',metadata_url='',subtitle_url='',created_at=now(),updated_at=now(),
                        redone_from=original['id'],job_type='render',message='采用当前素材与起止时间，等待生成新版。')
                    if original.get('selected_url')!=clip['source_url']:
                        task.pop('subtitle_srt',None);task.pop('transcription',None)
                    self.apply_product_clip(task,product)
                    self.e.state['tasks'].append(task);self.attach(task,product)
                else:task=self.new_task(product,'render')
                # Explicit generation after review also creates a source-derived
                # cover, while preserving the user's manually chosen cut.
                if hasattr(self.e,'studio'):
                    task['director_options']=copy.deepcopy(self.e.state['studio_settings'])
                    task['director_options']['mode']='manual'
                product.pop('manual_state',None);self.e.save()
            except Exception:
                self.e.state['tasks']=before_tasks;product.clear();product.update(before_product);raise
        self.e.wake.set()
        return {'ok':True,'task_id':task['id'],'message':'已按当前素材和起止时间加入生成队列，旧成片保留。'}

    def set_search_name(self,pid,payload):
        value=payload.get('name','')
        if not isinstance(value,str) or len(value)>160 or any(ord(c)<32 for c in value):
            raise ValueError('检索名称最多 160 字，请填写品牌和具体型号，不要粘贴多行或链接。')
        value=clean(value)
        if value and (len(value)<2 or re.search(r'https?://',value,re.I)):
            raise ValueError('请填写品牌和具体型号；视频链接请使用下方素材链接入口。')
        with self.e.lock:
            product=self.product(pid)
            if any(t['status'] in ACTIVE for t in self.tasks(product)):
                raise ValueError('请等待当前任务完成后再修改检索名称。')
            before=copy.deepcopy(product)
            product.update(search_name=value,updated_at=now())
            try: self.e.save()
            except Exception:
                product.clear();product.update(before);raise
        return {'ok':True,'message':'检索名称已保存。点击“重新检索素材链接”后使用，不改变商品标题、旧链接或历史视频。'}

    def mark(self,pid,payload):
        state=payload.get('state')
        if state not in {'todo','done'}: raise ValueError('无效的待办状态。')
        with self.e.lock:
            product=self.product(pid)
            if any(t['status'] in ACTIVE for t in self.tasks(product)): raise ValueError('处理中不能修改完成状态。')
            if state=='todo' and self.skip_decision(product): product['skip_override']='search'
            product.update(manual_state=state,updated_at=now());self.e.save()
        return {'ok':True,'message':'已更新待办状态。手动标记不会生成视频文件。'}

    def paste(self,payload):
        text=payload.get('text','')
        if not isinstance(text,str) or not text.strip() or len(text)>100000: raise ValueError('请粘贴商品，每次最多 100000 字符。')
        lines=[line.strip() for line in text.splitlines() if line.strip()]
        if len(lines)>1000: raise ValueError('一次最多整理 1000 行商品；更多请导入 Excel。')
        added=0;duplicates=0;skipped=0
        with self.e.lock:
            for line in lines:
                task=self.e.parse_line(line);key=key_for(task['product_id'],task['original_title'] or task['title'])
                if key in self.by_key: duplicates+=1;continue
                product=self.create(task['product_id'],task['original_title'] or task['title'],task.get('taobao_url',''),
                                    description=task['description'],source_url=task.get('selected_url',''))
                added+=1
                skipped+=bool(self.skip_decision(product))
            self.e.save()
        return {'ok':True,'added':added,'duplicates':duplicates,'skipped':skipped,'message':f'已整理 {added} 个新商品，其中 {skipped} 个免检索归入已完成；跳过 {duplicates} 个重复商品。尚未启动制作。'}

    def import_folder(self):
        folder=self.e.state_file.parent/'imports';folder.mkdir(parents=True,exist_ok=True);return folder

    def upload(self,filename,raw):
        name=Path(filename).name
        if name!=filename or len(name)>180: raise ValueError('文件名无效。')
        table=excel_import.read_table(raw,name)
        token=uuid.uuid4().hex;folder=self.import_folder()
        suffix=Path(name).suffix.lower()
        (folder/(token+suffix)).write_bytes(raw)
        meta={'filename':name,'suffix':suffix,'sha256':hashlib.sha256(raw).hexdigest(),'created_at':now()}
        (folder/(token+'.json')).write_text(json.dumps(meta,ensure_ascii=False),encoding='utf-8')
        return self.analyze({'token':token,'rule':'all'},table)

    def load_import(self,token):
        if not isinstance(token,str) or not re.fullmatch('[a-f0-9]{32}',token): raise ValueError('导入记录无效，请重新选择文件。')
        file=self.import_folder()/(token+'.json')
        if not file.is_file(): raise ValueError('导入记录不存在，请重新选择文件。')
        meta=json.loads(file.read_text(encoding='utf-8'))
        return meta,(self.import_folder()/(token+meta['suffix'])).read_bytes()

    def normalized_records(self,prepared,rule):
        from editor_server import video_url as youtube_url
        if rule not in {'all','missing_video','marked'}: raise ValueError('未知的商品筛选规则。')
        if rule=='missing_video' and prepared['mapping']['existing_video']<0: raise ValueError('请先匹配“已有视频链接”列。')
        if rule=='marked' and prepared['mapping']['need']<0: raise ValueError('请先匹配“是否需要制作”列。')
        selected=[];seen=set();duplicate=0;excluded=0;invalid=[]
        for row in prepared['records']:
            if rule=='missing_video' and row['existing_video']: excluded+=1;continue
            if rule=='marked' and excel_import.norm(row['need']) not in {'是','1','yes','true','需要','待制作','待做','需制作','重做','未完成'}: excluded+=1;continue
            pid=row['id'].lstrip("'")
            if pid and not re.fullmatch(r'\d{6,20}',pid): invalid.append({'row':row['row'],'reason':'商品 ID 格式无效，请按文本保存，避免科学计数法或精度丢失。'});continue
            if not pid and row['product_url']:
                try:
                    host=urlparse(row['product_url']).hostname
                    if host in {'item.taobao.com','detail.tmall.com'}: pid=parse_qs(urlparse(row['product_url']).query).get('id',[''])[0]
                except ValueError: pass
                if pid and not re.fullmatch(r'\d{6,20}',pid): pid=''
            title=clean(row['title'])
            if not title and pid in self.e.by_id: title=self.e.by_id[pid]['title']
            if not title or len(title)>1000: invalid.append({'row':row['row'],'reason':'缺少商品名称或标题超过 1000 字符。'});continue
            key=key_for(pid,title)
            if key in seen: duplicate+=1;continue
            seen.add(key)
            selected.append({'key':key,'product_id':pid,'title':title,'product_url':row['product_url'],
                             'existing_video':row['existing_video'],'source_url':youtube_url(row['source_url']) or '', 'source_row':row['row']})
        return selected,{'total_rows':len(prepared['records']),'selected':len(selected),'duplicates':duplicate,'excluded':excluded,'invalid_count':len(invalid),'invalid':invalid[:30],
                         'source_rows':prepared['source_rows'],'hidden_rows':prepared['hidden_rows'],'hidden_excluded':prepared['hidden_excluded']}

    def analyze(self,payload,table=None):
        meta,raw=self.load_import(payload.get('token'))
        prepared=excel_import.prepare(table or excel_import.read_table(raw,meta['filename'],payload.get('sheet','')),payload.get('mapping'),payload.get('include_hidden',False))
        selected,summary=self.normalized_records(prepared,payload.get('rule','all'))
        with self.e.lock:
            existing=[self.by_key[r['key']] for r in selected if r['key'] in self.by_key]
            summary.update(new=len(selected)-len(existing),existing=len(existing),completed=sum(self.view(p)['status']=='done' for p in existing))
            summary['skip_count']=sum(bool(self.skip_decision(self.by_key.get(r['key'],r))) for r in selected)
            preview=[{**row,'in_board':row['key'] in self.by_key,'status':self.view(self.by_key[row['key']])['status_label'] if row['key'] in self.by_key else '配件跳过' if self.skip_decision(row) else '待制作'} for row in selected[:8]]
        return {'ok':True,'token':payload['token'],'filename':meta['filename'],'sheet':prepared['sheet'],'sheets':prepared['sheets'],
                'headers':prepared['headers'],'mapping':prepared['mapping'],'rule':payload.get('rule','all'),'summary':summary,'preview':preview,'include_hidden':prepared['include_hidden']}

    def commit(self,payload):
        meta,raw=self.load_import(payload.get('token'))
        prepared=excel_import.prepare(excel_import.read_table(raw,meta['filename'],payload.get('sheet','')),payload.get('mapping'),payload.get('include_hidden',False))
        selected,summary=self.normalized_records(prepared,payload.get('rule','all'))
        if not selected: raise ValueError('这个规则没有筛出有效商品，请先调整规则或列匹配。')
        signature=hashlib.sha256(json.dumps([meta['sha256'],prepared['sheet'],payload.get('rule','all'),prepared['mapping'],'visible-rows-v1',prepared['include_hidden']],sort_keys=True).encode()).hexdigest()
        with self.e.lock:
            previous=next((batch for batch in self.e.state['imports'] if batch.get('signature')==signature),None)
            if previous: return {'ok':True,'added':0,'existing':len(selected),'batch_id':previous['id'],'message':'这份清单已导入，已打开原批次，没有创建重复待办。'}
            batch_id=uuid.uuid4().hex[:12];added=0
            for row in selected:
                product=self.by_key.get(row['key'])
                if product is None:
                    product=self.create(row['product_id'],row['title'],row['product_url'],existing_video=row['existing_video'],source_url=row['source_url'],source_row=row['source_row'])
                    added+=1
                else:
                    if row['existing_video']: product['existing_video']=row['existing_video']
                if batch_id not in product['batch_ids']: product['batch_ids'].append(batch_id)
            batch={'id':batch_id,'filename':meta['filename'],'sheet':prepared['sheet'],'created_at':now(),'rule':payload.get('rule','all'),
                   'count':len(selected),'added':added,'signature':signature,'token':payload['token'],
                   'include_hidden':prepared['include_hidden'],'hidden_excluded':prepared['hidden_excluded']}
            self.e.state['imports'].append(batch);self.e.save()
            skipped=sum(bool(self.skip_decision(self.by_key[row['key']])) for row in selected)
        return {'ok':True,'added':added,'existing':len(selected)-added,'skipped':skipped,'batch_id':batch_id,
                'message':f'已加入 {added} 个新商品，关联 {len(selected)-added} 个已有商品。本次有 {skipped} 个免检索归已完成；尚未启动制作。'}


def extract_links(text):
    from editor_server import video_url as youtube_url
    if not isinstance(text,str) or len(text)>100000: raise ValueError('链接文本过长。')
    links=[];seen=set()
    for raw in re.findall(r'https?://[^\s<>"\u3000]+',text):
        url=youtube_url(raw.rstrip('。，,.;；)）]》'))
        if url and url not in seen: seen.add(url);links.append(url)
    if len(links)>100: raise ValueError('一次最多整理 100 个素材链接。')
    return {'ok':True,'links':links,'count':len(links)}
