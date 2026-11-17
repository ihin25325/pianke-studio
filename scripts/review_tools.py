"""Product-local review metadata and safe, optional reference photos."""
import copy
import io
import json
import math
from pathlib import Path
import re
import threading
import time
import uuid

import product_images
import batch_template

ROOT=Path(__file__).resolve().parents[1]


class ReviewTools:
    def __init__(self,editor):
        self.e=editor;self.image_inflight=set()

    def source(self,url,product=None):
        from editor_server import youtube_url,source_id
        url=youtube_url(url)
        if not url:return {'url':'','video_id':'','local_url':'','duration':0,'title':''}
        vid=source_id(url);duration=0;title=''
        if product:
            for task in reversed(self.e.board.tasks(product)):
                candidate=next((c for c in task.get('candidates',[]) if youtube_url(c.get('url'))==url),None)
                if candidate:
                    duration=candidate.get('duration') or 0;title=candidate.get('title','');break
        local=self.e.existing_source(vid)
        if local:
            try:
                metadata=json.loads(local.with_name('source.info.json').read_text(encoding='utf-8'))
                duration=metadata.get('duration') or duration;title=metadata.get('title') or title
            except (OSError,ValueError):pass
        if not isinstance(duration,(int,float)) or isinstance(duration,bool) or not math.isfinite(duration) or duration<0:duration=0
        return {'url':url,'video_id':vid,'local_url':'/source-preview/'+vid+'.mp4' if local else '',
                'duration':duration,'title':title,'has_local':bool(local)}

    def snapshot(self,product):
        url=self.e.board.view(product)['source_url']
        source=self.source(url,product)
        clip=product.get('clip')
        if not clip or clip.get('source_url')!=url:clip=None
        return {'source':source,'clip':copy.deepcopy(clip),'image':copy.deepcopy(product.get('product_image',{})),
                'batch_clip':batch_template.snapshot(self.e.state['settings']),
                'max_duration':self.e.state['settings'].get('max_duration_seconds',90)}

    def save_image(self,pid,image):
        from editor_server import now
        with self.e.lock:
            product=self.e.board.product(pid);before=copy.deepcopy(product)
            product.update(product_image=image,updated_at=now())
            try:self.e.save()
            except Exception:
                product.clear();product.update(before);raise

    def set_image_url(self,pid,payload):
        url=product_images.image_url(payload.get('url'))
        if not url:raise ValueError('请粘贴 HTTPS 淘宝／天猫图片链接（alicdn.com 或 tbcdn.cn 的 JPG、PNG、WebP），其他图片可直接上传。')
        image={'url':url,'status':'ready','source':'manual_url','message':'用户提供的淘宝 CDN 图片，请人工核对商品。','checked_at':time.time(),'revision':uuid.uuid4().hex}
        self.save_image(pid,image)
        return {'ok':True,'image':image,'message':'商品参考图已保存。'}

    def upload_image(self,pid,raw):
        with self.e.lock:self.e.board.product(pid)
        if not raw or len(raw)>8*1024*1024:raise ValueError('商品图为空或超过 8 MB。')
        try:
            from PIL import Image
            with Image.open(io.BytesIO(raw)) as image:
                kind=image.format
                if kind not in {'JPEG','PNG','WEBP'} or image.width*image.height>24000000:
                    raise ValueError('请选择不超过 2400 万像素的 JPG、PNG 或 WebP 图片。')
                image.verify()
        except ImportError as exc:raise ValueError('本机图片校验组件不可用，请先粘贴淘宝图片链接。') from exc
        except ValueError:raise
        except Exception as exc:raise ValueError('图片损坏、像素过大或格式不支持，请使用 JPG、PNG、WebP。') from exc
        suffix={'JPEG':'.jpg','PNG':'.png','WEBP':'.webp'}[kind]
        folder=ROOT/'data'/'editor'/'product_images'/pid;folder.mkdir(parents=True,exist_ok=True)
        name=uuid.uuid4().hex+suffix
        with (folder/name).open('xb') as handle:handle.write(raw)
        image={'url':'/product-image/'+pid+'/'+name,'status':'ready','source':'manual_upload',
               'message':'用户上传的商品参考图，请人工核对。','checked_at':time.time(),'revision':uuid.uuid4().hex}
        self.save_image(pid,image)
        return {'ok':True,'image':image,'message':'商品参考图已上传到 D 盘，不会写进视频。'}

    def fetch_image(self,pid,payload):
        force=payload.get('force',False)
        if not isinstance(force,bool):raise ValueError('刷新开关格式不正确。')
        with self.e.lock:
            product=self.e.board.product(pid)
            image=copy.deepcopy(product.get('product_image',{}))
            if not force and (image.get('url') or time.time()-image.get('checked_at',0)<600):
                return {'ok':True,'image':image,'message':image.get('message','')}
            if pid in self.image_inflight:return {'ok':True,'image':image,'message':'正在另一个请求中读取主图，稍后可重新读取。'}
            self.image_inflight.add(pid)
            url=product.get('product_url','');item_id=product.get('product_id','')
            before_image=copy.deepcopy(image)
        try:
            try:
                photo=product_images.fetch_photo(url,item_id)
                image={'url':photo,'status':'ready','source':'taobao_public','source_url':url,
                       'message':'来自对应淘宝／天猫商品公开页面，请核对款式。','checked_at':time.time()}
            except Exception as exc:
                message=str(exc) if isinstance(exc,ValueError) else '商品页面暂不可访问或要求登录。可上传主图或粘贴图片链接，不会使用其他商品图代替。'
                if image.get('url'):return {'ok':True,'image':image,'message':message}
                image={'url':'','status':'unavailable','message':message,'checked_at':time.time()}
            with self.e.lock:
                latest=self.e.board.product(pid).get('product_image',{})
                if latest!=before_image and latest.get('source','').startswith('manual_'):
                    return {'ok':True,'image':copy.deepcopy(latest),'message':'保留刚刚手动补充的商品参考图。'}
                self.save_image(pid,image)
            return {'ok':True,'image':image,'message':image['message']}
        finally:
            with self.e.lock:self.image_inflight.discard(pid)
