"""Exact-item public photo lookup. No cookies, account tokens or similar-item substitution."""
from html import unescape
from html.parser import HTMLParser
import json
import re
from urllib.parse import urlparse,parse_qs
from urllib.request import Request,build_opener,HTTPRedirectHandler

MAX_PAGE=2*1024*1024


def image_url(value):
    if not isinstance(value,str): return None
    value=unescape(value.strip()).replace('\\/','/')
    if value.startswith('//'): value='https:'+value
    try:
        p=urlparse(value);host=(p.hostname or '').lower()
        if p.scheme!='https' or p.username or p.password or p.port not in (None,443): return None
        if not any(host==d or host.endswith('.'+d) for d in ('alicdn.com','tbcdn.cn')): return None
        if not re.search(r'\.(?:jpg|jpeg|png|webp)(?:_|$)',p.path,re.I): return None
        return value if len(value)<=2048 else None
    except ValueError:return None


def page_url(value,pid):
    try:
        p=urlparse(value)
        return p.scheme=='https' and not p.username and not p.password and p.port in (None,443) and p.hostname in {'item.taobao.com','detail.tmall.com','detail.m.tmall.com'} and parse_qs(p.query).get('id',[''])[0]==pid
    except ValueError:return False


class Metadata(HTMLParser):
    def __init__(self):
        super().__init__();self.photos=[];self.canonicals=[];self.jsons=[];self.script=False;self.parts=[]
    def handle_starttag(self,tag,attrs):
        a=dict(attrs)
        if tag=='meta' and (a.get('property') or a.get('name','')).lower() in {'og:image','twitter:image'}:
            self.photos.append(a.get('content',''))
        if tag=='link' and a.get('rel')=='canonical':self.canonicals.append(a.get('href',''))
        if tag=='script' and a.get('type')=='application/ld+json':self.script=True;self.parts=[]
    def handle_data(self,data):
        if self.script:self.parts.append(data)
    def handle_endtag(self,tag):
        if tag=='script' and self.script:
            try:self.jsons.append(json.loads(''.join(self.parts)))
            except ValueError:pass
            self.script=False


def parse_photo(html,pid):
    metadata=Metadata();metadata.feed(html)
    if re.search(r'<title>\s*(?:登录|验证|login|访问受限)',html,re.I): return None
    exact=any(page_url(url,pid) for url in metadata.canonicals) or bool(re.search(r'(?:itemId|item_id|"id"|[?&]id)["\s:=]+["\s]*'+re.escape(pid)+r'(?!\d)',html,re.I))
    if exact:
        for value in metadata.photos:
            if image_url(value): return image_url(value)
    def walk(value):
        if isinstance(value,list):
            for v in value:
                result=walk(v)
                if result:return result
        if isinstance(value,dict):
            kind=value.get('@type')
            if ('Product'==kind or isinstance(kind,list) and 'Product' in kind) and (str(value.get('sku',''))==pid or str(value.get('productID',''))==pid or page_url(value.get('url',''),pid)):
                pictures=value.get('image',[])
                if not isinstance(pictures,list):pictures=[pictures]
                for p in pictures:
                    if isinstance(p,dict):p=p.get('url','')
                    if image_url(p):return image_url(p)
            for v in value.values():
                if isinstance(v,(dict,list)):
                    result=walk(v)
                    if result:return result
    for value in metadata.jsons:
        result=walk(value)
        if result:return result
    return None


def fetch_photo(url,pid):
    if not re.fullmatch(r'\d{6,20}',str(pid)) or not page_url(url,str(pid)):
        raise ValueError('没有可核对的淘宝／天猫商品页面，请上传主图或粘贴淘宝图片链接。')
    class Redirect(HTTPRedirectHandler):
        def redirect_request(self,request,fp,code,message,headers,newurl):
            if not page_url(newurl,str(pid)):raise ValueError('商品页面要求登录或跳转验证，未自动读取账号。可上传主图或粘贴图片链接。')
            return super().redirect_request(request,fp,code,message,headers,newurl)
    request=Request(url,headers={'User-Agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0 Safari/537.36','Accept':'text/html'})
    with build_opener(Redirect()).open(request,timeout=8) as response:
        raw=response.read(MAX_PAGE+1)
        if len(raw)>MAX_PAGE:raise ValueError('商品页面过大，未自动解析。请上传主图或粘贴图片链接。')
        html=raw.decode(response.headers.get_content_charset() or 'utf-8',errors='replace')
    result=parse_photo(html,str(pid))
    if not result:raise ValueError('公开页面未提供可确认的该商品主图，可能需要登录。请上传主图或粘贴图片链接；不会用其他商品图代替。')
    return result
