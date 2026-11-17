"""Disk-backed ZIP batches and persistent, version-specific transfer receipts."""
import copy
from datetime import datetime
import os
from pathlib import Path
import re
import shutil
import threading
import time
import uuid
import zipfile

import export_naming


def now():return datetime.now().astimezone().isoformat(timespec='seconds')


class ExportDownloads:
    def __init__(self,editor):
        self.e=editor
        # Tests/custom queues use their own folder, never the live batch folder.
        self.folder=editor.state_file.parent/'export_packages'
        self.tickets={}
        self.e.state.setdefault('export_downloads',[])
        self.e.state.setdefault('export_batches',[])
        for job in self.e.state['export_batches']:
            if job['status']=='building':job.update(status='failed',message='上次打包中断，请重新选择商品打包。')

    @property
    def busy(self):return any(j['status']=='building' for j in self.e.state['export_batches'])

    def task_item(self,tid,used=None):
        task=next((t for t in self.e.state['tasks'] if t['id']==tid),None)
        if not task or task['status']!='completed' or task.get('job_type','render')!='render':
            raise ValueError('该版本尚未生成可下载的成片。')
        file=self.e.resolve_media(task.get('output_url',''))
        if not file or file.suffix!='.mp4' or file.stat().st_size<=1000:
            raise ValueError('成片文件不存在，请重做后下载。')
        return {'task_id':tid,'product_id':task.get('board_product_id',''),
                'title':task.get('original_title') or task.get('title',''),
                'filename':export_naming.unique_filename(export_naming.product_name(task),used if used is not None else set()),
                'size':file.stat().st_size,'output_url':task['output_url']},file

    def prepare(self,payload):
        ids=payload.get('ids')
        if not isinstance(ids,list) or not 1<=len(ids)<=100 or any(not isinstance(p,str) for p in ids):
            raise ValueError('请勾选 1–100 个商品，打包各商品最新的可用成片。')
        with self.e.lock:
            if self.busy:raise ValueError('已有一批成片正在打包，请等完成后再添加。')
            items=[];skipped=[];used=set()
            for pid in dict.fromkeys(ids):
                try:
                    product=self.e.board.product(pid)
                    tid=self.e.board.view(product)['current_task_id']
                    if not tid:raise ValueError('没有可用成片（配件跳过、手动完成不算成片）。')
                    item,_=self.task_item(tid,used);items.append(item)
                except ValueError as exc:skipped.append({'id':pid,'message':str(exc)})
            if not items:raise ValueError('所选商品都没有可下载的成片，请先制作；配件跳过或手动完成不会生成视频。')
            self.folder.mkdir(parents=True,exist_ok=True)
            required=sum(i['size'] for i in items)+10*1024*1024
            if shutil.disk_usage(self.folder).free<required:raise ValueError('D 盘剩余空间不足以保存这批压缩包。')
            jid=uuid.uuid4().hex
            job={'id':jid,'status':'building','progress':0,'created_at':now(),'items':items,'skipped':skipped,
                 'filename':f'商品成片_{datetime.now():%Y%m%d_%H%M%S}_{jid[:6]}.zip','message':'正在 D 盘打包，原成片保持不变。'}
            self.e.state['export_batches'].append(job)
            try:self.e.save()
            except Exception:
                self.e.state['export_batches'].remove(job);raise
            threading.Thread(target=self.build,args=(job,),daemon=True,name='export-zip').start()
            return {'ok':True,'job':self.job_view(job),'message':f'已提交 {len(items)} 个成片打包；跳过 {len(skipped)} 个无成片商品。'}

    def build(self,job):
        partial=self.folder/(job['id']+'.partial.zip');final=self.folder/(job['id']+'.zip')
        total=sum(i['size'] for i in job['items']);written=0;last=0
        try:
            # MP4 is already compressed. ZIP_STORED avoids slow, wasteful recompression.
            with partial.open('xb') as target,zipfile.ZipFile(target,'w',compression=zipfile.ZIP_STORED,allowZip64=True) as archive:
                for item in job['items']:
                    with self.e.lock:
                        actual,file=self.task_item(item['task_id'])
                        if actual['output_url']!=item['output_url'] or actual['size']!=item['size']:
                            raise ValueError('打包期间成片发生变化，请重新打包。')
                    with file.open('rb') as src,archive.open(item['filename'],'w',force_zip64=True) as dst:
                        copied=0
                        while chunk:=src.read(1024*1024):
                            dst.write(chunk);copied+=len(chunk);written+=len(chunk)
                            progress=min(99,int(written/total*100))
                            if progress>=last+5:
                                with self.e.lock:
                                    job.update(progress=progress,message=f'正在打包 {item["filename"]}');self.e.save()
                                last=progress
                        if copied!=item['size']:raise ValueError('成片大小发生变化，未生成正式压缩包。')
                archive.writestr('成片清单.txt','\n'.join(i['filename']+'\t'+i['title'] for i in job['items']))
            # Windows rename is exclusive; no existing package can be overwritten.
            if final.exists():raise ValueError('打包文件名冲突，请重新打包。')
            partial.rename(final)
            with self.e.lock:
                job.update(status='ready',progress=100,ready_at=now(),message='打包完成。点击“下载 ZIP”才会进入下载清单。')
                self.e.save()
        except Exception as exc:
            with self.e.lock:
                job.update(status='failed',message='打包失败：'+str(exc)[-500:]);self.e.save()

    def job_view(self,job):
        file=self.folder/(job['id']+'.zip')
        ready=job['status']=='ready' and file.is_file() and not file.is_symlink()
        return {key:copy.deepcopy(job.get(key)) for key in ('id','filename','status','progress','message','created_at','sent_at','skipped')} | {
            'count':len(job['items']),'download_url':'/export-download/'+job['id'] if ready else '',
            'local_file':str(file) if ready else '',
            'status':'missing' if job['status']=='ready' and not ready else job['status'],
            'message':'打包完成，已有下载记录；可再次下载。' if job.get('sent_at') and ready else job['message']}

    def video_ticket(self,payload):
        with self.e.lock:
            item,_=self.task_item(payload.get('task_id'))
            self.tickets={key:value for key,value in self.tickets.items() if time.time()-value['at']<86400}
            token=uuid.uuid4().hex;self.tickets[token]={'item':item,'at':time.time()}
            return {'ok':True,'download_url':'/export-download/'+token,'filename':item['filename']}

    def delivery(self,token):
        if not re.fullmatch('[a-f0-9]{32}',token):raise ValueError('下载链接无效。')
        with self.e.lock:
            job=next((j for j in self.e.state['export_batches'] if j['id']==token),None)
            if job:
                file=self.folder/(token+'.zip')
                if job['status']!='ready' or not file.is_file() or file.is_symlink():raise ValueError('压缩包未就绪或已移走，请重新打包。')
                return file,job['filename'],copy.deepcopy(job['items']),'batch'
            ticket=self.tickets.get(token)
            if not ticket or time.time()-ticket['at']>=86400:raise ValueError('下载链接已失效，请重新点击下载。')
            _,file=self.task_item(ticket['item']['task_id'])
            return file,ticket['item']['filename'],[copy.deepcopy(ticket['item'])],'video'

    def record_sent(self,items,kind,token):
        with self.e.lock:
            records=self.e.state['export_downloads'];stamp=now()
            for item in items:
                # A clear/reset can race an already-open stream: never resurrect cleared records.
                if not any(t['id']==item['task_id'] for t in self.e.state['tasks']):continue
                record=next((r for r in records if r['task_id']==item['task_id']),None)
                if record:record.update(downloaded_at=stamp,count=record.get('count',1)+1,filename=item['filename'],kind=kind)
                else:records.append({**copy.deepcopy(item),'downloaded_at':stamp,'first_downloaded_at':stamp,'count':1,'kind':kind})
            job=next((j for j in self.e.state['export_batches'] if j['id']==token),None)
            if job:job['sent_at']=stamp
            self.e.save()

    def snapshot(self,query=None):
        query=query or {}
        with self.e.lock:
            search=str(query.get('q',[''])[0]).casefold().strip()[:160]
            records=sorted(self.e.state['export_downloads'],key=lambda r:r['downloaded_at'],reverse=True)
            if search:records=[r for r in records if search in (r['title']+' '+r['filename']).casefold()]
            total=len(records);pages=max(1,(total+24)//25)
            try:page=min(pages,max(1,int(query.get('page',['1'])[0])))
            except (ValueError,TypeError):page=1
            items=[]
            for record in records[(page-1)*25:page*25]:
                task=next((t for t in self.e.state['tasks'] if t['id']==record['task_id']),None)
                file=self.e.resolve_media(task.get('output_url','')) if task else None
                items.append({**copy.deepcopy(record),'available':bool(file),'output_url':task.get('output_url','') if file else ''})
            return {'ok':True,'items':items,'total':total,'page':page,'pages':pages,'count':len(self.e.state['export_downloads']),
                    'batches':[self.job_view(j) for j in reversed(self.e.state['export_batches'][-8:])],
                    'notice':'已下载表示工作台已完整发送文件，不代表浏览器一定保存成功；请核对浏览器下载结果。播放、仅打包和中断传输不计入。'}
