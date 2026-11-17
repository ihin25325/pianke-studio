"""URL identity and site labels. Network permission is checked again in workers."""
import hashlib
import ipaddress
import re
from urllib.parse import urlsplit,urlunsplit,parse_qs,urlencode

SCOPES={'all':'综合：国内＋海外＋公开网页','domestic':'国内：哔哩哔哩＋网页','international':'海外：YouTube＋网页','youtube':'仅 YouTube（原方式）'}
PLATFORMS=[('bilibili.com','哔哩哔哩'),('b23.tv','哔哩哔哩短链'),('douyin.com','抖音'),('kuaishou.com','快手'),
 ('xiaohongshu.com','小红书'),('xhslink.com','小红书短链'),('weibo.com','微博'),('weibo.cn','微博'),
 ('youku.com','优酷'),('qq.com','腾讯视频'),('taobao.com','淘宝视频'),('youtube.com','YouTube'),('youtu.be','YouTube'),
 ('vimeo.com','Vimeo'),('tiktok.com','TikTok'),('instagram.com','Instagram'),('facebook.com','Facebook'),
 ('fb.watch','Facebook'),('twitter.com','X / Twitter'),('x.com','X / Twitter'),('dailymotion.com','Dailymotion')]

def host_is(host,domain):return host==domain or host.endswith('.'+domain)

def public_host(host):
    try:
        host=host.encode('idna').decode().lower()
        try:return host if ipaddress.ip_address(host).is_global else None
        except ValueError:pass
        if len(host)>253 or '.' not in host or host.endswith(('.local','.localhost','.internal','.test','.invalid','.example')):return None
        if not all(re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?',p) for p in host.split('.')):return None
        if not re.search('[a-z]',host.split('.')[-1]):return None
        return host
    except (UnicodeError,AttributeError):return None

def youtube(value):
    try:
        p=urlsplit(value)
        if p.scheme!='https' or p.username or p.password or p.port not in (None,443):return None
        if p.hostname in {'youtu.be','www.youtu.be'}:vid=p.path.strip('/').split('/')[0]
        elif p.hostname in {'youtube.com','www.youtube.com','m.youtube.com','www.youtube-nocookie.com'}:
            vid=parse_qs(p.query).get('v',[''])[0]
            if p.path.startswith(('/shorts/','/embed/','/live/')):vid=p.path.split('/')[2]
        else:return None
        if re.fullmatch(r'[A-Za-z0-9_-]{11}',vid):return 'https://www.youtube.com/watch?v='+vid
    except (ValueError,TypeError,AttributeError):pass
    return None

def canonical(value):
    if not isinstance(value,str):return None
    value=value.strip()
    if len(value)>4096 or any(ord(c)<33 for c in value):return None
    try:
        p=urlsplit(value.strip())
        if p.scheme!='https' or p.username or p.password or p.port not in (None,443):return None
        host=public_host(p.hostname)
        if not host:return None
        if host_is(host,'youtube.com') or host_is(host,'youtu.be') or host_is(host,'youtube-nocookie.com'):return youtube(value)
        path=p.path or '/'
        if path=='/':return None
        if host_is(host,'bilibili.com'):
            match=re.fullmatch(r'/video/(BV[A-Za-z0-9]{10}|av\d+)/?',path)
            if not match:return None
            page=parse_qs(p.query).get('p',[''])[0]
            return 'https://www.bilibili.com/video/'+match[1]+('/?p='+page if page.isdigit() and int(page)>1 else '/')
        if host_is(host,'vimeo.com') and not re.search(r'/\d+',path):return None
        # Keep query strings: many public CDNs and short links need them.
        query='&'.join(part for part in p.query.split('&') if part and not re.match(r'(?:utm_\w+|spm|from_source|share_source)=',part,re.I))
        return urlunsplit(('https',host,path,query,''))
    except (ValueError,TypeError,AttributeError):return None

def source_key(value):
    url=canonical(value)
    if not url:raise ValueError('请提供公开 HTTPS 视频页面、分享短链或视频直链；不接受本机、内网和播放列表。')
    yt=youtube(url)
    return parse_qs(urlsplit(yt).query)['v'][0] if yt else 'v_'+hashlib.sha256(url.encode()).hexdigest()[:24]

def platform(value):
    try:
        host=urlsplit(value).hostname or ''
        return next((label for domain,label in PLATFORMS if host_is(host,domain)),'公开网页 / 视频直链')
    except ValueError:return '未知网站'

def scope(value):
    if value not in SCOPES:raise ValueError('请选择有效的检索范围。')
    return value

def failure(error):
    text=str(error).lower()
    if any(w in text for w in ['drm','protected content','encrypted']):return '该素材受 DRM / 加密保护，不下载；请改用有权使用的公开原片。'
    if any(w in text for w in ['login','cookies','sign in','captcha','403','412','429','验证','登录']):return '平台要求登录、验证或限制请求，已停止；不会自动读取浏览器 Cookie 或绕过限制。可换公开链接或使用平台授权下载。'
    if any(w in text for w in ['unsupported url','no video','not a video']):return '该页面未发现下载器可读取的视频。请提供具体视频页、分享短链或公开 MP4 链接。'
    return '公开素材读取失败，可能是网络、站点变更或时长 / 体积限制。详情见本地任务日志；可换链接重试。'
