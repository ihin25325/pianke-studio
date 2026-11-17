"""Local, versioned brand assets. Discovered/cropped logos require confirmation.

Generated fallbacks are explicitly non-official typography, never fake originals.
"""
import copy
import hashlib
from html.parser import HTMLParser
import http.client
import io
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import socket
import ssl
import subprocess
import sys
import threading
from urllib.parse import urljoin,urlsplit,urlunsplit
import uuid
from functools import lru_cache
from urllib.parse import quote
from PIL import Image,ImageDraw,ImageFont,ImageOps,ImageStat

ROOT=Path(__file__).resolve().parents[1]
HOMES={'xvive':'https://xvive.com/audio/','neumann':'https://www.neumann.com/en-us',
       'shure':'https://www.shure.com/','sennheiser':'https://www.sennheiser.com/',
       'yamaha':'https://www.yamaha.com/','roland':'https://www.roland.com/'}


def normalized(text):return re.sub(r'[^a-z0-9\u3400-\u9fff]','',str(text).casefold())


def brand_name(value):
    if not isinstance(value,str) or not 1<=len(value.strip())<=80 or not normalized(value):raise ValueError('请填写品牌名称，最多 80 字。')
    if any(ord(c)<32 for c in value):raise ValueError('品牌名称不能包含控制字符。')
    return value.strip()


def atomic(file,value):
    file.parent.mkdir(parents=True,exist_ok=True);temp=file.with_name(file.name+'.'+uuid.uuid4().hex+'.tmp')
    try:temp.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8');os.replace(temp,file)
    finally:
        if temp.exists():temp.unlink()


@lru_cache(maxsize=128)
def proxy_public_dns(host):
    """Resolve fake-IP proxy domains through a pinned public HTTPS resolver.

    Never connect to the reserved fake IP; private DNS answers still fail closed.
    Only a public hostname is sent, not paths, credentials, files or API keys.
    """
    connection=http.client.HTTPSConnection('cloudflare-dns.com',timeout=10,context=ssl.create_default_context())
    try:
        connection.sock=connection._context.wrap_socket(socket.create_connection(('1.1.1.1',443),timeout=10),server_hostname='cloudflare-dns.com')
        connection.request('GET','/dns-query?name='+quote(host,safe='')+'&type=A',headers={'Accept':'application/dns-json'})
        response=connection.getresponse();raw=response.read(64001)
        if response.status!=200 or len(raw)>64000:raise ValueError()
        result=json.loads(raw)
        return tuple(a['data'] for a in result.get('Answer',[]) if a.get('type')==1)
    except (OSError,ValueError,KeyError,http.client.HTTPException):raise ValueError('代理 DNS 的公网解析失败，请上传 Logo 或稍后重试。') from None
    finally:connection.close()


def public_url(value):
    if not isinstance(value,str) or len(value)>3000:raise ValueError('网址太长。')
    p=urlsplit(value.strip())
    if p.scheme!='https' or not p.hostname or p.username or p.password or p.port not in (None,443):raise ValueError('仅接受无账号密码的公开 HTTPS 网址。')
    host=p.hostname.encode('idna').decode()
    if '.' not in host or host.endswith(('.local','.localhost','.internal')):raise ValueError('不读取本机或内网网址。')
    try:
        addresses=list({x[4][0] for x in socket.getaddrinfo(host,443,type=socket.SOCK_STREAM)})
    except OSError:raise ValueError('该网站域名无法解析。') from None
    if addresses and all(ipaddress.ip_address(ip) in ipaddress.ip_network('198.18.0.0/15') for ip in addresses):
        # This proxy-only compatibility does NOT allow IP literals or private DNS.
        try:ipaddress.ip_address(host)
        except ValueError:addresses=list(proxy_public_dns(host))
    if not addresses or any(not ipaddress.ip_address(ip).is_global for ip in addresses):raise ValueError('不读取本机、内网或保留地址。')
    return urlunsplit(('https',host,p.path or '/',p.query,'')),host,addresses[0]


def download(value,limit=4*1024*1024):
    # Pin the validated address while verifying TLS against the original host.
    for _ in range(4):
        url,host,address=public_url(value);p=urlsplit(url)
        connection=http.client.HTTPSConnection(host,timeout=10,context=ssl.create_default_context())
        try:
            connection.sock=connection._context.wrap_socket(socket.create_connection((address,443),timeout=10),server_hostname=host)
            connection.request('GET',p.path+('?' + p.query if p.query else ''),headers={'Host':host,'User-Agent':'Mozilla/5.0 LocalBrandStudio/1.0','Accept-Encoding':'identity'})
            response=connection.getresponse()
            if response.status in {301,302,303,307,308}:
                value=urljoin(url,response.getheader('Location',''));continue
            if response.status!=200:raise ValueError('网站返回 HTTP '+str(response.status)+'，可改为上传截图或本地视频截取。')
            if int(response.getheader('Content-Length','0') or '0')>limit:raise ValueError('远程文件过大。')
            raw=response.read(limit+1)
            if len(raw)>limit:raise ValueError('远程文件超过大小限制。')
            return raw,url,response.getheader('Content-Type','')
        except (OSError,ssl.SSLError,http.client.HTTPException):raise ValueError('网站无法读取，请换网址或上传图片；未使用登录凭据。') from None
        finally:connection.close()
    raise ValueError('网站跳转次数过多。')


class LogoHTML(HTMLParser):
    def __init__(self):super().__init__();self.images=[];self.base=''
    def handle_starttag(self,tag,attrs):
        a=dict(attrs)
        if tag=='img':
            hint=' '.join(str(a.get(k,'')) for k in ('src','alt','class','id','data-src'))
            if re.search('logo|brand',hint,re.I) and not re.search('favicon|payment|visa|mastercard',hint,re.I):
                src=a.get('data-src') or a.get('src','')
                if src and not src.startswith('data:'):self.images.append(src)


def candidates(html,page):
    parser=LogoHTML();parser.feed(html);result=[]
    for src in parser.images[:12]:
        url=urljoin(page,src)
        if url not in [v.get('url') for v in result]:result.append({'url':url})
    for svg in re.findall(r'<svg\b[^>]*>.*?</svg>',html,re.I|re.S)[:25]:
        if re.search(r'logo|brand',svg[:1000],re.I):result.insert(0,{'svg':svg})
    return result[:8]


def raster(raw):
    if len(raw)>8*1024*1024:raise ValueError('图片超过 8 MB。')
    is_svg=b'<svg' in raw[:2000].lower()
    if is_svg:
        from xml.etree import ElementTree as ET
        text=raw.decode('utf-8-sig')
        if re.search(r'<!DOCTYPE|<!ENTITY|<script|<foreignObject|<image\b|@import|javascript:',text,re.I):raise ValueError('SVG 包含不支持的脚本、图片或外部资源，请上传透明 PNG。')
        try:root=ET.fromstring(text)
        except ET.ParseError:raise ValueError('SVG 格式无效。') from None
        for element in root.iter():
            for key,value in element.attrib.items():
                if key.lower().startswith('on') or key.rsplit('}',1)[-1]=='href' and not value.startswith('#'):raise ValueError('SVG 不允许外部资源。')
            if re.search(r'url\(\s*["\']?(?!#)[^)]', ' '.join(element.attrib.values())+' '+(element.text or ''),re.I):raise ValueError('SVG 不允许外部资源。')
        folder=ROOT/'cache/temp/logo_svg';folder.mkdir(parents=True,exist_ok=True);token=uuid.uuid4().hex
        source=folder/(token+'.svg');output=folder/(token+'.png');source.write_bytes(raw)
        try:
            r=subprocess.run([sys.executable,'-B',str(ROOT/'scripts/logo_svg.py'),str(source),str(output)],capture_output=True,timeout=20)
            if r.returncode or not output.is_file():raise ValueError('SVG 转换失败，请上传透明 PNG，或安装 Logo SVG 支持。')
            raw=output.read_bytes()
        finally:
            if source.exists():source.unlink()
            if output.exists():output.unlink()
    try:
        with Image.open(io.BytesIO(raw)) as im:
            if im.width*im.height>16_000_000 or max(im.size)>8000:raise ValueError('图片尺寸太大。')
            im=ImageOps.exif_transpose(im).convert('RGBA');im.thumbnail((2000,1600))
            box=im.getchannel('A').getbbox()
            if not box:raise ValueError('图片完全透明。')
            im=im.crop(box);im.load();return im
    except (OSError,Image.DecompressionBombError):raise ValueError('不支持的图片，请使用 PNG、JPG、WebP 或安全 SVG。') from None


def transform(image,rect=None,background='keep'):
    im=image.convert('RGBA').copy()
    if rect is not None:
        if not isinstance(rect,list) or len(rect)!=4 or any(type(v) not in (int,float) or not math.isfinite(v) for v in rect):raise ValueError('裁剪框格式错误。')
        x,y,w,h=rect
        if x<0 or y<0 or w<=0 or h<=0 or x+w>1.001 or y+h>1.001:raise ValueError('请在图片内选择有效的裁剪区域。')
        im=im.crop((round(x*im.width),round(y*im.height),round((x+w)*im.width),round((y+h)*im.height)))
    if min(im.size)<2:raise ValueError('裁剪结果为空或尺寸太小。')
    if background not in {'keep','white','black'}:raise ValueError('背景处理选项无效。')
    if background!='keep':
        target=255 if background=='white' else 0
        # Remove only border-connected near-white/black, preserving enclosed details.
        for point in [(0,0),(im.width-1,0),(0,im.height-1),(im.width-1,im.height-1)]:
            pixel=im.getpixel(point)
            if pixel[3]>200 and max(abs(c-target) for c in pixel[:3])<32:ImageDraw.floodfill(im,point,(0,0,0,0),thresh=45)
    box=im.getchannel('A').getbbox()
    if not box or min(im.size)<2:raise ValueError('裁剪结果为空或尺寸太小。')
    return im.crop(box)


def asset_image(config):
    value=config.get('brand_logo')
    if not value:return None
    path=Path(value.get('file',''))
    base=ROOT/'data'
    # State can be isolated under work/ for tests, but asset paths stay registered.
    if not path.is_absolute() or path.suffix!='.png' or not re.fullmatch(r'[a-f0-9]{32}\.png',path.name) or not path.is_file() or path.is_symlink():raise ValueError('品牌 Logo 文件无效。')
    if ROOT.resolve() not in path.resolve().parents or path.parent.name!='images' or path.parent.parent.name!='brand_library':raise ValueError('品牌 Logo 不在项目素材库中。')
    with Image.open(path) as image:return image.convert('RGBA').copy()


def place(canvas,image,box):
    image=image.copy();image.thumbnail((int(box[2]),int(box[3])),Image.Resampling.LANCZOS)
    x=round(box[0]+(box[2]-image.width)/2);y=round(box[1]+(box[3]-image.height)/2)
    backdrop=canvas.convert('RGB').getpixel((max(0,x),max(0,y)))
    # Preserve official colours: use a neutral plate for poor contrast, never recolour.
    foreground=ImageStat.Stat(image.convert('RGB'),mask=image.getchannel('A')).mean;dark_bg=sum(backdrop)<300;dark_logo=sum(foreground)<430
    if dark_bg and dark_logo:
        draw=ImageDraw.Draw(canvas);draw.rounded_rectangle((x-12,y-8,x+image.width+12,y+image.height+8),radius=12,fill='#f7f8fa')
    elif not dark_bg and sum(ImageStat.Stat(image.convert('RGB'),mask=image.getchannel('A')).mean)>650:
        ImageDraw.Draw(canvas).rounded_rectangle((x-12,y-8,x+image.width+12,y+image.height+8),radius=12,fill='#1d2738')
    canvas.paste(image,(x,y),image)


def overlay(config,destination):
    image=asset_image(config)
    if image is None:return None
    prefs=config.get('typography',{});scale=float(prefs.get('brand_scale',1));offset=float(prefs.get('brand_offset',0))
    width=min(820,max(150,520*scale));height=min(240,max(60,150*scale));center=max(55+height/2,min(385-height/2,228+offset))
    canvas=Image.new('RGBA',(1080,1440));place(canvas,image,(540-width/2,center-height/2,width,height))
    canvas.save(destination);return Path(destination)


class BrandLogos:
    def __init__(self,editor):
        self.e=editor;self.folder=editor.state_file.resolve().parent/'brand_library';self.file=self.folder/'library.json'
        self.lock=threading.RLock();self.fetch_lock=threading.Lock()
    def load(self):
        if not self.file.is_file():return {'enabled':True,'fallback':True,'auto_find':True,'brands':{}}
        try:
            result=json.loads(self.file.read_text(encoding='utf-8'))
            if not isinstance(result.get('brands'),dict):raise ValueError()
            result.setdefault('auto_find',True)
            return result
        except (ValueError,OSError):raise ValueError('品牌素材库无法读取，请保留原文件并检查权限。') from None
    def entry(self,state,name):
        name=brand_name(name);key=normalized(name)
        matches=[b for b in state['brands'].values() if key in [normalized(b['name']),*[normalized(a) for a in b['aliases']]]]
        if len(matches)>1:raise ValueError('品牌别名有冲突，请先在素材库修正。')
        if matches:return matches[0]
        record={'name':name,'aliases':[],'assets':[],'selected':''};state['brands'][key]=record;return record
    def add(self,name,image,kind,source=''):
        with self.lock:
            state=self.load();entry=self.entry(state,name)
            fingerprint=hashlib.sha256(str(image.size).encode()+image.tobytes()).hexdigest()
            old=next((a for a in entry['assets'] if a.get('fingerprint')==fingerprint and a['kind']==kind),None)
            if old:return copy.deepcopy(old)
            token=uuid.uuid4().hex;folder=self.folder/'images';folder.mkdir(parents=True,exist_ok=True)
            image.save(folder/(token+'.png'))
            asset={'id':token,'kind':kind,'source':source[:3000],'width':image.width,'height':image.height,'approved':kind=='generated',
                   'url':'/brand-logo/'+token+'.png','fingerprint':fingerprint}
            entry['assets'].append(asset)
            if kind=='generated' and not entry['selected']:entry['selected']=token
            atomic(self.file,state);return copy.deepcopy(asset)
    def snapshot(self):
        with self.lock:
            state=self.load();names={t.get('brand','') for t in self.e.state.get('tasks',[]) if t.get('brand')}
            for name in names:self.entry(state,name)
            result=copy.deepcopy(state);result['brands']=sorted(result['brands'].values(),key=lambda b:b['name'].casefold())
            result['ok']=True;result['homes']=HOMES
            result['sources']=[{'task_id':t['id'],'label':t.get('title',''),'url':t.get('selected_url','')} for t in reversed(self.e.state.get('tasks',[])) if t.get('selected_url') and t.get('status') in {'completed','analyzed','downloaded'}][:100]
            return result
    def generate(self,name):
        name=brand_name(name)
        with self.lock:
            state=self.load();entry=self.entry(state,name)
            old=next((a for a in entry['assets'] if a['kind']=='generated'),None)
            if old:return copy.deepcopy(old)
        path=ROOT/'assets/fonts/Manrope/Manrope-Bold.ttf'
        if not path.is_file():path=ROOT/'assets/fonts/Oswald/Oswald-SemiBold.ttf'
        if re.search(r'[\u3400-\u9fff]',name):path=ROOT/'assets/fonts/SourceHanSans/SourceHanSansCN-Bold.otf'
        size=180
        while size>24:
            font=ImageFont.truetype(str(path),size)
            if font.getlength(name)<1400:break
            size-=2
        box=font.getbbox(name);image=Image.new('RGBA',(math.ceil(font.getlength(name))+36,box[3]-box[1]+36))
        ImageDraw.Draw(image).text((18,18-box[1]),name,font=font,fill='white')
        return self.add(name,image,'generated','本地字体排版生成；非官方原版 Logo，不调用 API。')
    def save(self,payload):
        with self.lock:
            state=self.load()
            for key in ('enabled','fallback','auto_find'):
                if key in payload:
                    if not isinstance(payload[key],bool):raise ValueError('开关格式错误。')
                    state[key]=payload[key]
            if payload.get('brand'):
                entry=self.entry(state,payload['brand'])
                if 'aliases' in payload:
                    aliases=payload['aliases']
                    if not isinstance(aliases,list) or len(aliases)>20:raise ValueError('别名最多 20 个。')
                    aliases=list(dict.fromkeys(brand_name(x) for x in aliases))
                    occupied={normalized(n) for b in state['brands'].values() if b is not entry for n in [b['name'],*b['aliases']]}
                    if any(normalized(a) in occupied for a in aliases):raise ValueError('别名已被另一个品牌使用。')
                    entry['aliases']=aliases
                if 'selected' in payload:
                    chosen=next((a for a in entry['assets'] if a['id']==payload['selected']),None)
                    if payload['selected'] and not chosen:raise ValueError('素材不存在。')
                    if chosen and chosen['kind']!='generated':
                        if payload.get('confirmed') is not True:raise ValueError('采用前请确认品牌匹配及当前用途的使用权限。')
                        chosen['approved']=True
                    entry['selected']=payload['selected']
            atomic(self.file,state)
        return {'ok':True,'message':'品牌设置已保存，新制作和预览使用；旧视频不改动。'}
    def discover(self,payload):
        name=brand_name(payload.get('brand'));page=payload.get('url','').strip() or HOMES.get(normalized(name),'')
        if not page:return {'ok':True,'assets':[self.generate(name)],'message':'该品牌尚无预置官网，已生成非官方文字字标。可填写官网、上传图片或截取已下载原片。'}
        if not self.fetch_lock.acquire(blocking=False):raise ValueError('已有找图任务正在进行，请稍候。')
        try:
            # Reject unsafe destinations before a recoverable fetch failure.
            public_url(page)
            try:raw,final,kind=download(page)
            except ValueError as error:
                return {'ok':True,'assets':[self.generate(name)],'message':str(error)+' 已保留非官方文字字标回退。'}
            if 'image/' in kind or b'<svg' in raw[:1000].lower():items=[{'raw':raw,'url':final}]
            else:items=candidates(raw.decode('utf-8',errors='replace'),final)
            assets=[];errors=[]
            for item in items[:5]:
                try:
                    if 'svg' in item:data=item['svg'].encode();source=final+'#inline-logo'
                    elif 'raw' in item:data=item['raw'];source=item['url']
                    else:data,source,_=download(item['url'])
                    image=raster(data)
                    if min(image.size)<12:continue
                    asset=self.add(name,image,'web',source)
                    if asset['id'] not in [a['id'] for a in assets]:assets.append(asset)
                    if len(assets)>=3:break
                except (ValueError,OSError,TimeoutError) as error:errors.append(str(error)[:100])
            if not assets:
                generated=self.generate(name)
                return {'ok':True,'assets':[generated],'message':'未找到可用原版候选，已生成非官方文字字标。可上传截图或从已下载视频中框选。'+(' '+errors[0] if errors else '')}
            return {'ok':True,'assets':assets,'message':f'找到 {len(assets)} 个候选，尚未自动采用；请核对品牌和用途后选择。'}
        finally:self.fetch_lock.release()
    def upload(self,name,raw):return {'ok':True,'asset':self.add(name,raster(raw),'uploaded','用户上传，待核对品牌和用途。')}
    def frame(self,payload):
        from editor_server import source_id
        import product_render as renderer
        import clip_edit
        name=brand_name(payload.get('brand'));task_id=payload.get('task_id');at=clip_edit.seconds(payload.get('time','0'))
        with self.e.lock:task=next((t for t in self.e.state['tasks'] if t['id']==task_id),None)
        if not task:raise ValueError('请选择已有素材的任务。')
        source=self.e.existing_source(source_id(task.get('selected_url','')))
        if not source:raise ValueError('原片未下载。请先在商品详情采用链接并“仅下载素材”，再来截取；也可上传各平台截图。')
        duration=renderer.duration(renderer.probe(source))
        if at>=duration:raise ValueError('截帧时间超出原片。')
        r=subprocess.run([str(renderer.FFMPEG),'-v','error','-nostdin','-ss',str(at),'-i',str(source),'-frames:v','1','-vf','scale=1600:-2','-f','image2pipe','-vcodec','png','-'],capture_output=True,timeout=35)
        if r.returncode:raise ValueError('视频截帧失败。')
        return {'ok':True,'asset':self.add(name,raster(r.stdout),'video_frame',task.get('selected_url','')+' · '+str(at)+'s'),
                'message':'原片帧已提取，请在预览图框选品牌 Logo，再生成裁剪候选。不要将频道标识误作商品品牌。'}
    def crop(self,payload):
        name=brand_name(payload.get('brand'));asset_id=payload.get('asset_id');path=self.resolve('/brand-logo/'+str(asset_id)+'.png')
        if not path:raise ValueError('请先选择素材。')
        with self.lock:
            state=self.load();entry=self.entry(state,name)
            parent=next((a for a in entry['assets'] if a['id']==asset_id),None)
            if not parent:raise ValueError('此素材不属于该品牌。')
        with Image.open(path) as im:image=transform(im,payload.get('rect'),payload.get('background','keep'))
        return {'ok':True,'asset':self.add(name,image,'crop',parent['source']),'message':'裁剪候选已生成，原图保留。核对后才能采用。'}
    def resolve(self,path):
        match=re.fullmatch(r'/brand-logo/([a-f0-9]{32})\.png',path)
        if not match:return None
        with self.lock:
            if not any(a['id']==match[1] for b in self.load()['brands'].values() for a in b['assets']):return None
        file=self.folder/'images'/(match[1]+'.png')
        return file if file.is_file() and not file.is_symlink() else None
    def prepare(self,config):
        """Look up a known public homepage once on first export, never on previews.

        Unconfirmed originals stay pending. Explicit discovery can be repeated.
        """
        name=config.get('brand_title') or 'PRODUCT'
        with self.lock:
            state=self.load();entry=self.entry(state,name)
            should_find=state['enabled'] and state['auto_find'] and normalized(entry['name']) in HOMES and not entry.get('discovery_attempted') and not any(a['approved'] and a['kind']!='generated' for a in entry['assets'])
        if should_find:
            try:note=self.discover({'brand':entry['name']})['message']
            except (ValueError,OSError,TimeoutError):note='官网候选暂不可用；可在品牌素材库手动重试或上传。'
            with self.lock:
                state=self.load();entry=self.entry(state,name);entry['discovery_attempted']=True;entry['discovery_note']=note;atomic(self.file,state)
        return self.apply(config)
    def apply(self,config):
        with self.lock:
            config.pop('brand_logo',None)
            state=self.load()
            if not state['enabled']:config.pop('brand_logo',None);return config
            entry=self.entry(state,config.get('brand_title') or 'PRODUCT')
            asset=next((a for a in entry['assets'] if a['id']==entry['selected'] and a['approved']),None)
            if not asset and state['fallback']:asset=self.generate(entry['name'])
            if asset:
                file=self.resolve(asset['url'])
                if file:config['brand_logo']={'file':str(file),'id':asset['id'],'kind':asset['kind'],'source':asset['source'],
                                            'brand':entry['name'],'official_claim':False if asset['kind']=='generated' else None}
        return config
