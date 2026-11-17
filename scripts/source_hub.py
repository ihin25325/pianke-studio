"""Multi-site search settings and bounded, cached search aggregation."""
import copy
import hashlib
import json
import os
from pathlib import Path
import time
import subprocess
import sys
import threading
import video_sources as sources

ROOT=Path(__file__).resolve().parents[1]

class SourceHub:
    def __init__(self,editor):self.e=editor;self.probe_lock=threading.Lock()
    def snapshot(self):
        return {'ok':True,'scope':self.e.state['settings'].get('source_scope','all'),'scopes':sources.SCOPES,
            'products':[{'id':p['id'],'title':p['title']} for p in self.e.state.get('products',[])][:2000],
            'platforms':[{'domain':domain,'name':label} for domain,label in sources.PLATFORMS],
            'limits':{'minutes':15,'megabytes':300,'batch':100},
            'note':'多站点支持不等于每站必成功。自动搜索使用 YouTube、哔哩哔哩及公开网页检索；其他站点可粘贴链接尝试。网页结果待人工确认，不读取 Cookie、不绕过登录 / DRM。'}
    def probe(self,payload):
        url=sources.canonical(payload.get('url'))
        if not url:raise ValueError('请输入公开 HTTPS 视频页面、分享短链或视频直链。')
        if not self.probe_lock.acquire(blocking=False):raise ValueError('已有链接正在检测，请稍后。')
        try:
            result=subprocess.run([sys.executable,'-B',str(ROOT/'scripts/multisite_worker.py'),'info',url],capture_output=True,
                text=True,encoding='utf-8',errors='replace',timeout=65,cwd=ROOT)
            if result.returncode:raise ValueError(sources.failure(result.stderr))
            data=json.loads(result.stdout.strip().splitlines()[-1])
            return {'ok':True,'video':data['video'],'message':'已找到公开视频元信息；尚未下载，不代表后续下载或素材授权已确认。可绑定商品后“仅下载素材”。'}
        except subprocess.TimeoutExpired:raise ValueError('链接检测超时。可稍后重试或换链接，未启动下载。') from None
        finally:self.probe_lock.release()
    def save(self,payload):
        selected=sources.scope(payload.get('scope'))
        with self.e.lock:
            before=copy.deepcopy(self.e.state['settings']);self.e.state['settings']['source_scope']=selected
            try:self.e.save()
            except Exception:self.e.state['settings']=before;raise
        return {'ok':True,'message':'检索范围已保存；之后开始检索时使用，不改变已采用链接和旧成片。'}
    def additional(self,task,target,selected):
        from editor_server import PYTHON
        query=target['queries'][0]
        plans=[]
        if selected in {'all','domestic'}:plans.extend([('bili',query,'domestic'),('web',query,'domestic')])
        if selected in {'all','international'}:plans.append(('web',query,'international'))
        folder=self.e.state_file.resolve().parent/'multisite_search';folder.mkdir(parents=True,exist_ok=True)
        results=[];attempts=[];responses=0
        for kind,terms,region in plans:
            name='哔哩哔哩站内' if kind=='bili' else '公开网页 · '+('国内' if region=='domestic' else '海外')
            self.e.update(task,progress=18,message='多站点检索：'+name+' · '+terms)
            file=folder/(hashlib.sha256(('v1|'+kind+'|'+terms+'|'+region).encode()).hexdigest()+'.json')
            cached=False;entries=None
            if file.is_file():
                try:
                    saved=json.loads(file.read_text(encoding='utf-8'));ttl=86400 if saved['entries'] else 600
                    if time.time()-saved['at']<ttl and isinstance(saved['entries'],list) and len(saved['entries'])<=12 and all(sources.canonical(x.get('url'))==x.get('url') for x in saved['entries']):entries=saved['entries'];cached=True
                except (ValueError,KeyError,TypeError,OSError):pass
            try:
                if entries is None:
                    raw=self.e.command([PYTHON,'-B',str(ROOT/'scripts/multisite_worker.py'),kind,terms,region],task,'search_'+kind+'_'+region,timeout=65)
                    data=json.loads(raw.strip().splitlines()[-1]);entries=data.get('entries',[])[:12]
                    entries=[x for x in entries if isinstance(x,dict) and sources.canonical(x.get('url'))==x.get('url')]
                    temp=file.with_suffix('.tmp');temp.write_text(json.dumps({'at':time.time(),'entries':entries},ensure_ascii=False),encoding='utf-8');os.replace(temp,file)
                responses+=1;results.extend(entries);attempts.append({'query':terms,'engine':name,'count':len(entries),'cached':cached})
            except (ValueError,RuntimeError,OSError) as error:attempts.append({'query':terms,'engine':name,'error':str(error)[-500:]})
        return results,attempts,responses
