"""Bounded multi-site extraction in a child process; never reads browser cookies.

Every Python socket resolution is checked, including redirects, media and subtitles.
HTTP downloads stay native; FFmpeg is only given validated local files for remux.
"""
import argparse
import ipaddress
import json
from pathlib import Path
import re
import socket
import subprocess
import sys
import time
import os
import tempfile
from urllib.parse import urlencode,urlsplit
from urllib.request import Request,urlopen,build_opener,ProxyHandler
from xml.etree import ElementTree as ET
from html.parser import HTMLParser
from urllib.parse import parse_qs,unquote
import video_sources as sources
from runtime_paths import node_executable

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools/python_packages'))
MAX_BYTES=300*1024*1024

def install_network_guard():
    original=socket.getaddrinfo
    from brand_logos import proxy_public_dns
    def guarded(host,port,*args,**kwargs):
        if isinstance(host,bytes):host=host.decode('ascii')
        safe=sources.public_host(host)
        if not safe:raise OSError('Blocked local, private or invalid network destination')
        addresses=original(safe,port,*args,**kwargs)
        ips=[item[4][0] for item in addresses]
        if ips and all(ipaddress.ip_address(ip) in ipaddress.ip_network('198.18.0.0/15') for ip in ips):
            ips=proxy_public_dns(safe)
            addresses=[item for ip in ips for item in original(ip,port,*args,**kwargs)]
        if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):raise OSError('Blocked private or reserved DNS answer')
        return addresses
    socket.getaddrinfo=guarded
    return original

def options(folder=None):
    return {'quiet':True,'no_warnings':False,'noplaylist':True,'playlistend':1,'socket_timeout':15,
        'retries':1,'fragment_retries':1,'extractor_retries':1,'proxy':'','cachedir':str(ROOT/'cache/yt-dlp'),
        'enable_file_urls':False,'nocheckcertificate':False,'concurrent_fragment_downloads':1,
        'hls_prefer_native':True,'fixup':'never',
        'ffmpeg_location':str(ROOT/'tools/ffmpeg/bin'),'merge_output_format':'mp4',
        'js_runtimes':{'node':{'path':node_executable()}} if node_executable() else {},
        'outtmpl':str(folder/'download.%(ext)s') if folder else None}

def info_entry(data):
    url=sources.canonical(data.get('webpage_url') or data.get('url',''))
    if not url:return None
    return {'id':sources.source_key(url),'url':url,'title':str(data.get('title',''))[:400],
        'channel':str(data.get('channel') or data.get('uploader') or '')[:160],
        'duration':data.get('duration') or 0,'is_live':bool(data.get('is_live')),
        'availability':data.get('availability'),'platform':sources.platform(url),'verified_video':True}

def video_result_url(value,region):
    url=sources.canonical(value)
    if not url:return None
    host=urlsplit(url).hostname;path=urlsplit(url).path
    domestic=['bilibili.com','b23.tv','douyin.com','kuaishou.com','xiaohongshu.com','xhslink.com','weibo.com','weibo.cn','youku.com','qq.com']
    overseas=['youtube.com','youtu.be','vimeo.com','tiktok.com','instagram.com','facebook.com','x.com','twitter.com','dailymotion.com']
    domains=domestic if region=='domestic' else overseas
    if any(sources.host_is(host,domain) for domain in domains):return url
    if re.search(r'\.(mp4|webm|mov)$|/(?:videos?|watch|media)(?:/|$)',path,re.I):return url
    return None

class SearchLinks(HTMLParser):
    def __init__(self):super().__init__();self.link=None;self.text=[];self.entries=[]
    def handle_starttag(self,tag,attrs):
        if tag=='a':self.link=dict(attrs).get('href','');self.text=[]
    def handle_data(self,value):
        if self.link:self.text.append(value)
    def handle_endtag(self,tag):
        if tag=='a' and self.link:
            value=self.link
            if value.startswith('/url?'):value=parse_qs(urlsplit(value).query).get('q',[''])[0]
            self.entries.append((value,' '.join(self.text).strip()[:400]));self.link=None

def web_search(query,region):
    sites='(site:bilibili.com/video OR site:douyin.com/video OR site:kuaishou.com/short-video OR site:weibo.com OR site:xiaohongshu.com)' if region=='domestic' else '(site:vimeo.com OR site:youtube.com/watch OR site:dailymotion.com/video OR site:tiktok.com)'
    # RSS contains only result metadata; do not run HTML or page instructions.
    url='https://www.bing.com/search?'+urlencode({'q':query+' video '+sites,'format':'rss','count':10})
    opener=build_opener(ProxyHandler({}))
    with opener.open(Request(url,headers={'User-Agent':'Mozilla/5.0'}),timeout=18) as response:raw=response.read(2*1024*1024+1)
    if len(raw)>2*1024*1024:raise ValueError('Search response is too large')
    root=ET.fromstring(raw);entries=[]
    for item in root.findall('./channel/item')[:12]:
        link=video_result_url(item.findtext('link',''),region)
        if link:entries.append({'id':sources.source_key(link),'url':link,'title':item.findtext('title','')[:400],
            'channel':'网页检索候选（尚未验证视频）','duration':0,'platform':sources.platform(link),'verified_video':False})
    if not entries:
        # A separate public HTML fallback; challenges are not solved or bypassed.
        alternative='https://www.google.com/search?'+urlencode({'q':query+' video '+sites,'num':10})
        with opener.open(Request(alternative,headers={'User-Agent':'Mozilla/5.0'}),timeout=15) as response:html=response.read(2*1024*1024).decode('utf-8',errors='replace')
        if 'unusual traffic' in html.lower() or '/sorry/' in html:raise ValueError('Search engine requires login / CAPTCHA verification')
        parser=SearchLinks();parser.feed(html)
        seen=set()
        for value,title in parser.entries:
            link=video_result_url(value,region)
            if link and link not in seen and title:
                seen.add(link);entries.append({'id':sources.source_key(link),'url':link,'title':title,'channel':'网页检索候选（尚未验证视频）','duration':0,'platform':sources.platform(link),'verified_video':False})
                if len(entries)>=12:break
    return {'entries':entries}

def fetch_video(url,folder):
    import yt_dlp
    url=sources.canonical(url)
    if not url:raise ValueError('Invalid public video URL')
    folder=Path(folder).resolve()
    if ROOT.resolve() not in folder.parents or not ((ROOT/'sources').resolve() in folder.parents or (ROOT/'work').resolve() in folder.parents):raise ValueError('Download folder must be under project sources or isolated work')
    folder.mkdir(parents=True,exist_ok=True)
    if (folder/'source.mp4').exists():raise ValueError('Source already exists; refusing to overwrite')
    opts=options(folder)
    opts.update(format='bv[height<=1080]+ba/b[height<=1080]/b',max_filesize=MAX_BYTES,overwrites=False,continuedl=True)
    last=[0]
    def progress(status):
        size=sum(p.stat().st_size for p in folder.glob('download.*') if p.is_file())
        if size>MAX_BYTES:raise ValueError('Combined media exceeds 300 MB')
        if time.monotonic()-last[0]>2:
            print('Downloaded bytes: '+str(size),flush=True);last[0]=time.monotonic()
    opts['progress_hooks']=[progress]
    with yt_dlp.YoutubeDL(opts) as downloader:
        data=downloader.extract_info(url,download=False)
        if data.get('_type') in {'playlist','multi_video'} or data.get('entries') is not None:raise ValueError('Only a single video is accepted')
        if data.get('is_live') or data.get('live_status') in {'is_live','is_upcoming'}:raise ValueError('Live streams are not accepted')
        if data.get('duration') and float(data['duration'])>900:raise ValueError('Source exceeds 15 minutes')
        if data.get('has_drm'):raise ValueError('DRM media is not accepted')
        formats=data.get('requested_formats') or [data]
        for fmt in formats:
            if fmt.get('has_drm'):raise ValueError('DRM media is not accepted')
            protocol=fmt.get('protocol','https')
            if protocol=='m3u8':fmt['protocol']='m3u8_native'
            elif protocol not in {'https','http','m3u8_native','http_dash_segments'}:raise ValueError('Unsupported media transport; no external network downloader is used')
        data=downloader.process_ie_result(data,download=True)
    files=[p for p in folder.glob('download.*') if p.suffix.lower() in {'.mp4','.webm','.mkv','.mov','.flv'} and p.is_file() and not p.is_symlink()]
    if not files:raise ValueError('No completed media file')
    media=max(files,key=lambda p:p.stat().st_size)
    if media.stat().st_size>MAX_BYTES:raise ValueError('Media exceeds 300 MB')
    import product_render as renderer
    probe=renderer.probe(media)
    if renderer.duration(probe)>900.1 or not renderer.stream(probe,'video'):raise ValueError('Media has no video or exceeds 15 minutes')
    target=folder/'source.mp4'
    if media.suffix.lower()=='.mp4':media.rename(target)
    else:
        partial=folder/'normalized.partial.mp4'
        # Never pass a remote URL to an external downloader / FFmpeg process.
        command=[str(renderer.FFMPEG),'-v','error','-nostdin','-n','-protocol_whitelist','file,pipe,crypto','-i',str(media),
            '-map','0:v:0','-map','0:a:0?','-c:v','libx264','-preset','fast','-crf','21','-threads','4','-c:a','aac','-movflags','+faststart',str(partial)]
        result=subprocess.run(command,capture_output=True,timeout=600)
        if result.returncode:raise ValueError('Local MP4 conversion failed')
        if partial.stat().st_size>MAX_BYTES:raise ValueError('Converted media exceeds 300 MB')
        partial.rename(target)
    metadata={'id':sources.source_key(url),'_workbench_source_id':sources.source_key(url),'_workbench_source_url':url,
        'webpage_url':url,'title':data.get('title',''),'duration':renderer.duration(renderer.probe(target)),
        'extractor':data.get('extractor_key',''),'platform':sources.platform(url),'downloaded_at':time.time()}
    (folder/'source.info.json').write_text(json.dumps(metadata,ensure_ascii=False,indent=2),encoding='utf-8')
    return {'ok':True,'source':str(target),'metadata':metadata}

def main():
    parser=argparse.ArgumentParser();parser.add_argument('mode',choices=['bili','web','info','download']);parser.add_argument('value');parser.add_argument('extra',nargs='?',default='domestic');args=parser.parse_args()
    temp=ROOT/'cache/temp';temp.mkdir(parents=True,exist_ok=True);os.environ['TEMP']=os.environ['TMP']=str(temp);tempfile.tempdir=str(temp)
    install_network_guard()
    from yt_dlp.downloader.external import FFmpegFD
    def no_remote_ffmpeg(*args,**kwargs):raise ValueError('External network downloader is disabled; use a public MP4 or native HLS source')
    FFmpegFD.real_download=no_remote_ffmpeg
    if args.mode=='web':result=web_search(args.value,args.extra)
    elif args.mode=='download':result=fetch_video(args.value,args.extra)
    else:
        import yt_dlp
        opts=options();opts.pop('outtmpl',None)
        if args.mode=='bili':opts.update(extract_flat=True)
        with yt_dlp.YoutubeDL(opts) as downloader:
            data=downloader.extract_info('bilisearch6:'+args.value if args.mode=='bili' else sources.canonical(args.value),download=False)
        if args.mode=='bili':result={'entries':[entry for raw in data.get('entries',[]) if (entry:=info_entry(raw))]}
        else:
            if data.get('entries') is not None:raise ValueError('Only single videos are accepted')
            if data.get('has_drm'):raise ValueError('DRM media is not accepted')
            if data.get('is_live'):raise ValueError('Live streams are not accepted')
            result={'ok':True,'video':info_entry(data)}
    print(json.dumps(result,ensure_ascii=False),flush=True)

if __name__=='__main__':
    try:main()
    except Exception as error:
        print(sources.failure(error)+'\n'+str(error)[-1000:],file=sys.stderr);raise SystemExit(1)
