"""Isolated bounded CDN downloader. Input via stdin; no API keys or raw error logs."""
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, ProxyHandler, HTTPRedirectHandler

from multisite_worker import install_network_guard, MAX_BYTES, ROOT
from snapany import video_summary
import video_sources as sources


def media_url(value):
    try:
        p = urlsplit(value)
        if not isinstance(value, str) or len(value) > 16384 or any(ord(c) < 33 for c in value):
            raise ValueError()
        if p.scheme != 'https' or p.username or p.password or p.port not in (None, 443) or not sources.public_host(p.hostname):
            raise ValueError()
        return value  # Signed query strings must not be normalized.
    except (ValueError, TypeError, AttributeError):
        raise ValueError('媒体地址不是安全的公网 HTTPS 地址。') from None


def media_headers(value):
    if not isinstance(value, dict) or len(value) > 32:
        raise ValueError('媒体请求头格式异常。')
    result = {}
    for k, v in value.items():
        if not isinstance(k, str) or not isinstance(v, str) or not re.fullmatch(r'[A-Za-z0-9-]+', k):
            raise ValueError('媒体请求头格式异常。')
        if len(v) > 8192 or any(ord(c) < 32 or ord(c) > 255 for c in v) or 'sk_snapany_' in v:
            raise ValueError('媒体请求头含敏感或无效内容。')
        if k.lower() in {'host', 'content-length', 'transfer-encoding', 'connection', 'proxy-authorization', 'proxy-connection', 'range'}:
            raise ValueError('媒体请求头包含受限字段。')
        result[k] = v  # Keep intentionally empty User-Agent etc.
    return result


class MediaRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        media_url(newurl)
        result = super().redirect_request(req, fp, code, msg, headers, newurl)
        if result and urlsplit(req.full_url).netloc != urlsplit(newurl).netloc:
            for name in list(result.headers):
                if name.lower() in {'authorization', 'cookie'}:
                    del result.headers[name]
        return result


def choose(post, url):
    summary = video_summary(post, url)
    media = next(m for m in post['medias'] if m.get('media_type') == 'video')
    variants = media.get('variants') or []
    if not isinstance(variants, list) or any(not isinstance(v, dict) for v in variants):
        raise ValueError('清晰度变体格式异常。')
    eligible = [v for v in variants if v.get('video_url') and type(v.get('quality')) is int and 0 < v['quality'] <= 1080]
    selected = max(eligible, key=lambda v: v['quality']) if eligible else {}
    urls = [media_url(selected.get('video_url') or media.get('resource_url'))]
    if selected.get('audio_url'):
        urls.append(media_url(selected['audio_url']))
    declared = sum(selected.get(k) or 0 for k in ('video_filesize', 'audio_filesize'))
    if not isinstance(declared, (float, int)) or not math.isfinite(declared) or declared < 0 or declared > MAX_BYTES:
        raise ValueError('声明的素材体积无效或超过 300 MB。')
    return summary, urls, media_headers(media.get('headers') or {})


def stream_file(url, headers, target, total, deadline, opener=None):
    opener = opener or build_opener(ProxyHandler({}), MediaRedirect())
    with opener.open(Request(media_url(url), headers=headers), timeout=25) as response:
        media_url(response.geturl())
        length = response.headers.get('Content-Length')
        if length and int(length) + total[0] > MAX_BYTES:
            raise ValueError('音视频总大小超过 300 MB。')
        # HLS/DASH manifests are not files. Never pass a remote manifest to FFmpeg.
        content_type = response.headers.get('Content-Type', '').lower()
        if any(t in content_type for t in ('mpegurl', 'dash+xml', 'text/html')):
            raise ValueError('当前解析返回播放清单而非完整文件，请换用可下载 MP4 链接。')
        with target.open('xb') as handle:
            first = True
            while True:
                if time.monotonic() > deadline:
                    raise ValueError('素材下载超时。')
                chunk = response.read(256 * 1024)
                if not chunk:
                    break
                if first and (chunk.lstrip().startswith((b'#EXTM3U', b'<?xml', b'<MPD', b'<!DOCTYPE', b'<html'))):
                    raise ValueError('返回的不是完整媒体文件。')
                first = False
                total[0] += len(chunk)
                if total[0] > MAX_BYTES:
                    raise ValueError('音视频总大小超过 300 MB。')
                handle.write(chunk)


FORMATS = 'mov,matroska,webm,mp3,aac,ogg,mpegts,flv,wav'


def probe_local(path):
    import product_render as renderer
    command = [str(renderer.FFPROBE), '-v', 'error', '-protocol_whitelist', 'file,pipe',
               '-format_whitelist', FORMATS, '-show_format', '-show_streams', '-of', 'json', str(path)]
    result = subprocess.run(command, capture_output=True, timeout=30)
    if result.returncode:
        raise ValueError('下载文件未通过安全媒体格式检查。')
    return json.loads(result.stdout)


def fetch(payload):
    url = sources.canonical(payload.get('url'))
    if not url:
        raise ValueError('原始视频地址无效。')
    folder = Path(payload['folder']).resolve()
    if ROOT not in folder.parents or not any(parent.resolve() in folder.parents for parent in (ROOT / 'sources', ROOT / 'work')):
        raise ValueError('下载目标必须位于 D 盘项目 sources 或 work 子目录。')
    summary, urls, headers = choose(payload['post'], url)
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / 'source.mp4'
    info = folder / 'source.info.json'
    if target.exists() or info.exists():
        raise ValueError('原片或来源记录已存在，不覆盖；请检查已有文件。')
    attempt = Path(tempfile.mkdtemp(prefix='snapany_', dir=folder))
    total = [0]
    for i, link in enumerate(urls):
        stream_file(link, headers, attempt / f'stream{i}.bin', total, time.monotonic() + 240)
    import product_render as renderer
    for i in range(len(urls)):
        probe = probe_local(attempt / f'stream{i}.bin')
        if not 0 < renderer.duration(probe) <= 900.1:
            raise ValueError('下载的媒体无效或超过 15 分钟。')
        if i == 0 and not renderer.stream(probe, 'video'):
            raise ValueError('下载文件没有视频轨。')
    output = attempt / 'complete.mp4'
    command = [str(renderer.FFMPEG), '-v', 'error', '-nostdin', '-n']
    for i in range(len(urls)):
        command += ['-protocol_whitelist', 'file,pipe', '-format_whitelist', FORMATS, '-i', str(attempt / f'stream{i}.bin')]
    command += ['-map', '0:v:0', '-map', '1:a:0' if len(urls) == 2 else '0:a:0?',
                '-c:v', 'libx264', '-preset', 'fast', '-crf', '21', '-threads', '4',
                '-c:a', 'aac', '-movflags', '+faststart', '-fs', str(MAX_BYTES + 1), str(output)]
    result = subprocess.run(command, capture_output=True, timeout=480)
    if result.returncode or not output.is_file():
        raise ValueError('下载后本地 MP4 转换失败，未登记为可用原片。')
    probe = probe_local(output)
    if output.stat().st_size > MAX_BYTES or not renderer.stream(probe, 'video') or not 0 < renderer.duration(probe) <= 900.1:
        raise ValueError('输出原片未通过体积、时长或视频轨验证。')
    # Persist only sanitized attribution, never CDN URLs / cookies / API response.
    metadata = {'id': summary['id'], '_workbench_source_id': summary['id'], '_workbench_source_url': url,
                'webpage_url': url, 'title': summary['title'], 'duration': renderer.duration(probe),
                'platform': summary['platform'], 'extractor': 'SnapAny', 'downloaded_at': time.time()}
    record = attempt / 'source.info.json'
    record.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding='utf-8')
    output.rename(target)
    record.rename(info)
    # Only our own two temporary stream files, no originals or sibling files.
    for i in range(len(urls)):
        (attempt / f'stream{i}.bin').unlink()
    attempt.rmdir()
    return {'ok': True}


def main():
    temp = ROOT / 'cache/temp'
    temp.mkdir(parents=True, exist_ok=True)
    os.environ['TEMP'] = os.environ['TMP'] = str(temp)
    tempfile.tempdir = str(temp)
    install_network_guard()
    payload = json.loads(sys.stdin.buffer.read(2 * 1024 * 1024 + 1))
    return fetch(payload)


if __name__ == '__main__':
    try:
        result = main()
    except HTTPError as error:
        result = {'ok': False, 'error': f'素材服务器 HTTP {error.code}，未自动重新解析或启用额外付费代理。解析可能已扣积分。'}
    except ValueError as error:
        result = {'ok': False, 'error': str(error) if str(error).endswith('。') else '素材格式或安全校验未通过。解析可能已扣积分。'}
    except Exception:
        result = {'ok': False, 'error': '素材下载或转码失败，请检查网络、D 盘空间或换链接；未自动重新解析。解析可能已扣积分。'}
    print(json.dumps(result, ensure_ascii=False))
    raise SystemExit(0 if result.get('ok') else 1)
