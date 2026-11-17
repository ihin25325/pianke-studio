"""Opt-in SnapAny adapter. Keys encrypted with DPAPI; signed URLs stay in memory.

Only single-post extraction and free balance checks are implemented. No proxy,
ASR, playlist calls, automatic retries, recharge or reuse of the DeepSeek key.
"""
import base64
import copy
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener, ProxyHandler
import uuid

from api_connections import crypt, write_json, NoRedirect
import video_sources as sources

ROOT = Path(__file__).resolve().parents[1]
ENDPOINTS = {'cn': 'https://api-cn.snapany.com', 'global': 'https://api.snapany.com'}
MODES = {'off': '仅本地下载（不自动调用 API）', 'fallback': '本地失败后使用 SnapAny', 'prefer': '优先使用 SnapAny'}
DEFAULTS = {'endpoint': 'cn', 'mode': 'off', 'daily_limit': 10, 'secret': '', 'verified': False}


def request_json(endpoint, path, key, body=None):
    if endpoint not in ENDPOINTS or path not in {'/credits/balance', '/extract/post'}:
        raise ValueError('不支持的 SnapAny 接口。')
    headers = {'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json', 'Accept-Language': 'zh'}
    request = Request(ENDPOINTS[endpoint] + '/openapi/v1' + path,
                      data=json.dumps(body).encode() if body is not None else None, headers=headers)
    try:
        with build_opener(ProxyHandler({}), NoRedirect()).open(request, timeout=60) as response:
            raw = response.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024:
            raise ValueError('SnapAny 响应超过安全限制，已停止。')
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError('SnapAny 响应格式异常。')
        return value
    except HTTPError as error:
        status = error.code
        error.close()
        message = {400: '该内容可能已删除、私密、不受支持或暂时无法解析。',
                   401: '密钥无效或已撤销，请重新保存并检查。', 402: '积分不足，请自行在官方控制台检查。',
                   403: '平台拒绝访问，请核对账号权限。', 429: '请求受限，请稍后手动重试。'}.get(status, '官方接口暂时不可用。')
        raise ValueError(f'SnapAny HTTP {status}：{message} 未自动重试；费用以官方账单为准。') from None
    except (URLError, TimeoutError, OSError):
        raise ValueError('连接 SnapAny 超时或失败，没有自动重试。若请求已被接收，可能已扣积分，请先查账单。') from None
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise ValueError('SnapAny 返回的内容不是有效 JSON，没有自动重试。') from None


def video_summary(post, url):
    medias = post.get('medias', [])
    if not isinstance(medias, list) or any(not isinstance(m, dict) for m in medias):
        raise ValueError('SnapAny 媒体格式异常。')
    if any(m.get('media_type') == 'live' or m.get('has_drm') for m in medias) or post.get('has_drm'):
        raise ValueError('不接受直播或 DRM 素材。本次解析可能已扣积分。')
    videos = [m for m in medias if m.get('media_type') == 'video']
    if len(videos) != 1:
        raise ValueError('请提供包含一个视频的具体帖子；不自动选取多视频或图片帖。本次解析可能已扣积分。')
    duration = videos[0].get('duration') or 0
    if type(duration) not in (int, float) or not math.isfinite(duration) or not 0 <= duration <= 900:
        raise ValueError('原片时长无效或超过 15 分钟。本次解析可能已扣积分。')
    clean = lambda v: ''.join(c for c in str(v) if c.isprintable())[:400]
    return {'id': sources.source_key(url), 'url': url, 'title': clean(post.get('title') or post.get('text') or '未提供标题'),
            'duration': duration, 'platform': sources.platform(url), 'provider': 'snapany', 'verified_video': True}


class SnapAny:
    def __init__(self, editor):
        self.e = editor
        self.folder = editor.state_file.parent / 'private_api'
        self.config_file = self.folder / 'snapany.json'
        self.ledger_file = self.folder / 'snapany_usage.json'
        self.lock = threading.RLock()
        self.call_lock = threading.Lock()
        self.cache = {}

    def config(self):
        try:
            value = json.loads(self.config_file.read_text(encoding='utf-8')) if self.config_file.is_file() else {}
            c = {**DEFAULTS, **value}
            if c['endpoint'] not in ENDPOINTS or c['mode'] not in MODES or type(c['daily_limit']) is not int or not 1 <= c['daily_limit'] <= 100:
                raise ValueError()
            return c
        except (ValueError, TypeError, OSError):
            raise ValueError('SnapAny 本机设置无法读取，已停止 API 调用，请检查磁盘文件权限。') from None

    def key(self, c):
        if not c['secret']:
            raise ValueError('请先在国内外素材页面填写 SnapAny API 密钥（不是 DeepSeek 密钥）。')
        try:
            return crypt(base64.b64decode(c['secret'], validate=True), True).decode()
        except (ValueError, UnicodeDecodeError):
            raise ValueError('SnapAny 密钥无法解密，请在当前 Windows 账号下重新填写。') from None

    def ledger(self):
        try:
            rows = json.loads(self.ledger_file.read_text(encoding='utf-8')) if self.ledger_file.is_file() else []
            if not isinstance(rows, list) or any(not isinstance(r, dict) or not isinstance(r.get('date'), str) for r in rows):
                raise ValueError()
            return rows
        except (ValueError, OSError):
            raise ValueError('SnapAny 调用台账无法读取，已停止 API 调用。') from None

    def snapshot(self):
        with self.lock:
            c = self.config()
            rows = self.ledger()
            today = datetime.now().astimezone().date().isoformat()
            current = [r for r in rows if r.get('date') == today]
            return {'ok': True, 'endpoint': c['endpoint'], 'mode': c['mode'], 'modes': MODES,
                    'key_saved': bool(c['secret']), 'verified': bool(c['verified']), 'daily_limit': c['daily_limit'],
                    'balance': c.get('balance'), 'today_calls': len(current),
                    'recent_calls': [{k: r[k] for k in ('at', 'status') if k in r} for r in rows[-6:]][::-1]}

    def save(self, payload):
        if set(payload) - {'api_key', 'endpoint', 'mode', 'daily_limit', 'consent', 'clear_key'}:
            raise ValueError('SnapAny 设置包含未知字段。')
        with self.lock:
            previous = self.config()
            c = copy.deepcopy(previous)
            endpoint = payload.get('endpoint', c['endpoint'])
            mode = payload.get('mode', c['mode'])
            limit = payload.get('daily_limit', c['daily_limit'])
            if endpoint not in ENDPOINTS or mode not in MODES:
                raise ValueError('请选择有效的 SnapAny 接口区域与下载模式。')
            if type(limit) is not int or not 1 <= limit <= 100:
                raise ValueError('每日最多请求次数需为 1–100。')
            if endpoint != c['endpoint']:
                c.update(endpoint=endpoint, verified=False, mode='off', balance=None)
                mode = 'off'
            key = payload.get('api_key', '')
            if not isinstance(key, str):
                raise ValueError('密钥格式不正确。')
            key = key.strip()
            if key:
                if not key.startswith('sk_snapany_') or not 16 <= len(key) <= 512 or not key.isascii() or any(ch.isspace() for ch in key):
                    raise ValueError('请复制完整的 sk_snapany_ 开头密钥，不要填写聊天密码或 DeepSeek 密钥。')
                c.update(secret=base64.b64encode(crypt(key.encode())).decode(), verified=False, balance=None)
                mode = 'off'
            if payload.get('clear_key') is True:
                c.update(secret='', verified=False, balance=None)
                mode = 'off'
            if mode != 'off':
                if payload.get('consent') is not True:
                    raise ValueError('请先确认视频链接将发给 SnapAny，成功解析会消耗积分。')
                if not c['secret'] or not c['verified']:
                    raise ValueError('请先保存密钥并检查余额连接，再开启自动解析。')
            c.update(mode=mode, daily_limit=limit)
            write_json(self.config_file, c)
            if (previous['secret'], previous['endpoint']) != (c['secret'], c['endpoint']):
                self.cache.clear()
        return {**self.snapshot(), 'message': 'SnapAny 设置已保存；保存本身不联网。更换密钥或接口区域后自动模式关闭，需重新检查。'}

    def check(self):
        with self.lock:
            c = self.config()
            key = self.key(c)
        value = request_json(c['endpoint'], '/credits/balance', key)
        balance = value.get('balance')
        if type(balance) is not int or balance < 0:
            raise ValueError('SnapAny 余额响应格式异常。')
        with self.lock:
            now = self.config()
            if (now['secret'], now['endpoint']) != (c['secret'], c['endpoint']):
                raise ValueError('设置在检查期间改变，请重新检查。')
            now.update(verified=True, balance=balance)
            write_json(self.config_file, now)
        return {**self.snapshot(), 'message': f'连接通过，查询到 {balance} 积分。余额查询免费；尚未解析或下载视频。'}

    def mode(self):
        with self.lock:
            c = self.config()
            return c['mode'] if c['verified'] and c['secret'] else 'off'

    def _finish(self, token, status):
        with self.lock:
            rows = self.ledger()
            for row in rows:
                if row['id'] == token:
                    row['status'] = status
                    break
            write_json(self.ledger_file, rows)

    def extract(self, url, consent=False, automatic=False):
        url = sources.canonical(url)
        if not url:
            raise ValueError('请输入公开 HTTPS 视频或帖子链接。')
        if not self.call_lock.acquire(blocking=False):
            raise ValueError('已有 SnapAny 请求进行中，请稍后再试。')
        token = None
        try:
            with self.lock:
                c = self.config()
                if not c['verified']:
                    raise ValueError('请先保存密钥并点击检查余额连接。')
                if (automatic and c['mode'] == 'off') or (not automatic and consent is not True):
                    raise ValueError('请先确认 SnapAny 链接外发与积分计费，当前没有调用 API。')
                cache_key = hashlib.sha256((c['secret'] + c['endpoint'] + url).encode()).hexdigest()
                self.cache = {k: v for k, v in self.cache.items() if v[0] > time.monotonic()}
                if cache_key in self.cache:
                    return copy.deepcopy(self.cache[cache_key][1]), True
                key = self.key(c)
                rows = self.ledger()
                today = datetime.now().astimezone().date().isoformat()
                if sum(r.get('date') == today for r in rows) >= c['daily_limit']:
                    raise ValueError('今日 SnapAny 请求已达本机上限；失败和超时也占次数，未继续调用。')
                token = uuid.uuid4().hex
                rows.append({'id': token, 'date': today, 'at': datetime.now().astimezone().isoformat(timespec='seconds'), 'status': 'started'})
                write_json(self.ledger_file, rows)
            post = request_json(c['endpoint'], '/extract/post', key, {'url': url})
            self._finish(token, 'extracted')
            video_summary(post, url)
            with self.lock:
                self.cache[cache_key] = (time.monotonic() + 600, copy.deepcopy(post))
            return post, False
        except Exception:
            if token:
                self._finish(token, 'failed_or_uncertain')
            raise
        finally:
            self.call_lock.release()

    def preview(self, payload):
        url = sources.canonical(payload.get('url'))
        post, cached = self.extract(url, payload.get('consent'))
        return {'ok': True, 'video': video_summary(post, url), 'cached': cached,
                'message': '解析成功，尚未下载。' + ('复用本机 10 分钟内的解析结果，未再调用 API。' if cached else '本次解析通常消耗 1 积分，以官方账单为准。')}

    def _download(self, url, folder, post):
        # No key, signed URL or CDN headers enter argv, environment or a log file.
        worker = ROOT / 'scripts/snapany_worker.py'
        proc = subprocess.Popen([sys.executable, '-B', str(worker)], stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=ROOT)
        try:
            out, _ = proc.communicate(json.dumps({'url': url, 'folder': str(folder), 'post': post}).encode('utf-8'), timeout=900)
        except subprocess.TimeoutExpired:
            subprocess.run(['taskkill', '/PID', str(proc.pid), '/T', '/F'], capture_output=True)
            proc.communicate()
            raise ValueError('SnapAny 素材下载超时；解析可能已扣积分，没有自动重新解析。') from None
        try:
            value = json.loads(out)
        except (ValueError, UnicodeDecodeError):
            raise ValueError('SnapAny 下载进程异常退出；没有重新调用解析接口。') from None
        if proc.returncode or not value.get('ok'):
            raise ValueError(value.get('error') or 'SnapAny 素材下载失败。')
        return value

    def download(self, task, url, folder, local):
        mode = self.mode()
        if mode != 'prefer':
            try:
                return local()
            except RuntimeError:
                if mode != 'fallback':
                    raise
        self.e.update(task, status='downloading', progress=28, message='使用已授权的 SnapAny 单帖解析；成功通常 1 积分，不自动重试…')
        post, cached = self.extract(url, automatic=True)
        self._download(url, folder, post)
        self.e.update(task, download_provider='snapany', snapany_cached=cached,
                      message='SnapAny 原片下载完成；继续沿用当前商品的剪辑与导出设置。')
