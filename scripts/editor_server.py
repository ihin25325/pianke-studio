"""Loopback-only product video queue. All work, media and state live beside this script on D:."""
from __future__ import annotations

import argparse
import copy
from datetime import datetime
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading
import time
from urllib.parse import parse_qs, quote, unquote, urlparse
import uuid

import product_render as renderer
import speech_subtitles as speech
import chinese_translation as translation
from typography import DEFAULT_STYLE,FONT_CHOICES,STYLE_FILE,load_style,normalize_style,normalize_text
from project_manager import ProjectManager,extract_links
import search_strategy
import output_storage
import clip_edit
import batch_template
import export_naming
from export_downloads import ExportDownloads
from studio import Studio
from api_connections import ApiConnections
from brand_logos import BrandLogos
from review_tools import ReviewTools
import video_sources
from video_sources import canonical as video_url
from source_hub import SourceHub
from snapany import SnapAny

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'data' / 'editor'
STATE_FILE = DATA / 'queue.json'
PORT = 8765
PYTHON = sys.executable
NODE = shutil.which('node') or r'C:\Users\ADMIN\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe'
ACTIVE = {'queued', 'searching', 'downloading', 'analyzing', 'transcribing', 'rendering'}
BRANDS = {
    'Neumann': ['neumann', '诺音曼', '纽曼'], 'Shure': ['shure', '舒尔'],
    'Behringer': ['behringer', '百灵达'], 'Samson': ['samson', '山逊', '大力士'],
    'Sennheiser': ['sennheiser', '森海塞尔'], 'Audio-Technica': ['audio-technica', 'audiotechnica', '铁三角'],
    'Rode': ['rode', 'røde', '罗德'], 'AKG': ['akg', '爱科技'], 'Superlux': ['superlux', '舒伯乐'],
    'AKAI':['akai','雅家','雅佳'], 'miniDSP':['minidsp'], 'CME':['cme'],
    'Stylophone':['stylophone'], 'Sonicake':['sonicake','索尼凯克'], 'SOYUZ':['soyuz'],
    'Synido':['synido','森林岛'], 'Xvive':['xvive'], 'Waldorf':['waldorf'],
    'Alctron':['alctron','爱克创'], 'Platane':['platane','普拉坦'],
    'M-Live':['m-live','m live','mlive'], 'ESI':['esi'], 'Sweet Noise':['sweet noise','甜蜜噪音'], 'Radial':['radial'],
    'Focusrite': ['focusrite', '福克斯特'], 'Yamaha': ['yamaha', '雅马哈'],
    'Roland': ['roland', '罗兰'], 'Tascam': ['tascam'], 'Zoom': ['zoom'],
    'MOTU': ['motu'], 'Mackie': ['mackie', '美奇','美技','runningman'], 'PreSonus': ['presonus', '普瑞声纳'],
    'Midiplus': ['midiplus'], 'Novation': ['novation', '诺维逊'], 'Native Instruments': ['native instruments'],
    'Arturia': ['arturia'], 'Audient': ['audient'], 'Lewitt': ['lewitt', '莱维特'],
    'Warm Audio': ['warm audio'], 'Universal Audio': ['universal audio'],
}
# Separate model suffixes are variants, whereas USB/XLR are connection labels.
# Keep this deliberately narrow: uncertain product generations require review.
MODEL_SUFFIX = r'(?:XLII|XLS|MKIV|MKIII|MKII|MKI|MK\d+|V\d+|III|II|IV|AI|PRO|PLUS|MINI|[A-Z])'
MODEL_PATTERN = re.compile(
    r'(?<![A-Za-z0-9])('
    r'(?:[A-Za-z]{1,10}[ -]?\d{1,5}(?:[A-Za-z]{1,8}(?:\d{1,5})?|-[A-Za-z0-9]{1,8}){0,3}'
    r'|\d{1,3}[iIxX]\d{1,3}[A-Za-z]{0,4})'
    r'(?:[ \t]+' + MODEL_SUFFIX + r'(?![A-Za-z0-9]))?'
    r'\+?)(?![A-Za-z0-9])', re.I)


def now():
    return datetime.now().astimezone().isoformat(timespec='seconds')


def subtitle_options(payload):
    value=payload.get('subtitles',{})
    if not isinstance(value,dict): raise ValueError('字幕设置格式不正确。')
    enabled=value.get('enabled',False)
    if not isinstance(enabled,bool): raise ValueError('字幕开关需为勾选状态。')
    language=speech.language_code(value.get('language','auto'))
    target=translation.target_code(value.get('target','zh'))
    srt=value.get('srt','')
    speech.parse_srt(srt)
    if enabled and not srt.strip() and not speech.ready():
        raise ValueError('离线识别尚未安装，请双击项目中的“安装语音字幕.bat”。')
    if enabled and target=='zh' and not srt.strip() and language!='zh' and not translation.ready():
        raise ValueError('中文翻译尚未安装，请双击项目中的“安装中文翻译.bat”。')
    return {'enabled':enabled,'language':language,'target':target,'srt':srt}


def compact(value):
    return re.sub(r'[^a-z0-9\u4e00-\u9fff]', '', str(value).lower())


def identity(title):
    title = search_strategy.clean(title)
    def alias_matches(alias):
        if re.search(r'[\u3400-\u9fff]', alias):
            return alias in title.lower()
        return re.search(r'(?<![A-Za-z])' + re.escape(alias) + r'(?![A-Za-z])', title, re.I)
    matched_brands = [b for b, aliases in BRANDS.items() if any(alias_matches(a) for a in aliases)]
    brand = matched_brands[0] if len(matched_brands) == 1 else ''
    text = title
    for alias in BRANDS.get(brand, []):
        text = re.sub(re.escape(alias), ' ', text, flags=re.I)
    if brand=='Synido':
        text=re.sub(r'\bAI\b',' ',text,flags=re.I|re.ASCII)
    if brand=='Focusrite':
        text=re.sub(r'\bScarlett\b',' ',text,flags=re.I|re.ASCII)
    # Match the whole identifier, including C414 XLII, NT1-A, U 87 Ai, and 2i2.
    # Never append generic connection labels such as "USB" to a model number.
    models = []
    # Literal named families are scoped to a brand; keep editions/generations distinct.
    for pattern,model in search_strategy.NAMED_MODELS.get(brand,[]):
        matches=list(re.finditer(pattern,text,re.I|re.ASCII))
        for match in matches:
            if model not in models: models.append(model)
        text=re.sub(pattern,lambda m:' '*len(m[0]),text,flags=re.I|re.ASCII)
    for match in MODEL_PATTERN.finditer(text):
        value = match.group(1).replace('+', 'PLUS')
        model = re.sub(r'[^A-Za-z0-9]', '', value).upper()
        if re.fullmatch(r'(?:USB|HDMI|AI|GEN|FPS|V)\d+|\d+V',model):
            continue
        # Generations distinguish products too. Do not silently fall back to
        # the base model if a title explicitly specifies a numbered generation.
        generation = re.match(r'\s+(\d{1,2})(?:st|nd|rd|th)?\s*(?:generation|gen\b|代)', text[match.end():], re.I)
        if generation:
            model += 'GEN' + generation[1]
        if model not in models:
            models.append(model)
        # Listings such as IXO12/22 or AME-100/200pro contain multiple variants.
        short=re.match(r'(?:\s*/\s*\d{1,5}[A-Za-z]*)+',text[match.end():])
        prefix=re.match(r'[A-Z]+',model)
        if short and prefix:
            for suffix in re.findall(r'/\s*(\d{1,5}[A-Za-z]*)',short[0]):
                other=prefix[0]+suffix.upper()
                if other not in models: models.append(other)
    if not brand and not matched_brands:
        english = re.findall(r'[A-Za-z][A-Za-z-]{2,}', title)
        brand = next((word for word in english if word.lower() not in {'usb','otg','midi','fps','pro','max','gen','audio','hdmi'} and not any(compact(word) == compact(m) for m in models)), '')
    if brand=='CME' and re.search(r'Xkey\s+Air\s*25\s*/\s*37',title,re.I):
        models=list(dict.fromkeys(models+['XKEYAIR37']))
    if brand=='ESI' and re.search(r'NEVA\s+UNO\s*/\s*DUO',title,re.I):
        models=list(dict.fromkeys(models+['NEVADUO']))
    if re.search(r'\bVOLT\s*1\s+2\s+4',title,re.I):
        models=list(dict.fromkeys(models+['VOLT2','VOLT4']))
    return brand, models


def youtube_url(value):
    try:
        parsed = urlparse(str(value).strip())
        if parsed.scheme != 'https' or parsed.username or parsed.password:
            return None
        if parsed.hostname in {'youtu.be', 'www.youtu.be'}:
            video_id = parsed.path.strip('/').split('/')[0]
        elif parsed.hostname in {'youtube.com', 'www.youtube.com', 'm.youtube.com'}:
            video_id = parse_qs(parsed.query).get('v', [''])[0]
            if parsed.path.startswith('/shorts/'):
                video_id = parsed.path.split('/')[2]
        else:
            return None
        if re.fullmatch(r'[\w-]{11}', video_id):
            return f'https://www.youtube.com/watch?v={video_id}'
    except (ValueError, AttributeError):
        pass
    return None


def exact_model(model, text):
    target = re.sub(r'[^A-Z0-9]', '', str(model).upper().replace('+', 'PLUS'))
    return bool(target) and target in identity(str(text))[1]


def candidate_score(candidate, brand, model):
    return search_strategy.evaluate(candidate,{'brand':brand,'models':[model] if model else []},identity,BRANDS)['score']


def source_id(url):
    return video_sources.source_key(url)


def split_description(text):
    parts = [p.strip() for p in re.split(r'[\n；;。]+', text) if p.strip()]
    # Renderer handles visual line wrapping. Keep source text, do not fabricate claims.
    return parts[:3] if parts else []


def product_category(title, brand='', model=''):
    """Use category words present in the product name; do not invent benefits."""
    if brand.lower() == 'neumann' and model == 'U87AI':
        return '录音电容麦克风'
    rules = (
        ('麦克风防喷罩', r'防喷罩|防喷网|\bpop\s*filter\b'),
        ('麦克风防震架', r'防震架|\bshock\s*mount\b'),
        ('麦克风支架', r'(?:麦克风|话筒).{0,8}(?:支架|悬臂)|\bmicrophone\s*stand\b'),
        ('耳机放大器', r'耳(?:机|放).{0,3}放大器|耳放|\bheadphone\s*(?:amp|amplifier)\b'),
        ('麦克风前置放大器', r'话放|话筒放大器|麦克风前置|\b(?:mic|microphone)\s*preamp\b'),
        ('MIDI键盘', r'\bmidi\b.{0,12}(?:键盘|keyboard)|键盘.{0,6}(?:控制器|编曲)|编曲键盘'),
        ('音频接口', r'音频接口|声卡|\baudio\s*interface\b'),
        ('调音台', r'调音台|\b(?:audio\s*)?mixer\b|mixing\s*console'),
        ('合成器', r'合成器|\bsynthesizer\b'),
        ('监听音箱', r'监听音箱|监听扬声器|\bstudio\s*monitors?\b'),
        ('监听耳机', r'监听耳机|\bstudio\s*headphones?\b'),
        ('领夹麦克风', r'领夹|\blavalier\b'),
        ('无线麦克风', r'无线.{0,12}(?:麦克风|话筒)|(?:麦克风|话筒).{0,12}无线|\bwireless\s*(?:mic|microphone)\b'),
        ('铝带麦克风', r'铝带|\bribbon\s*(?:mic|microphone)\b'),
        ('动圈麦克风', r'动圈|\bdynamic\s*(?:mic|microphone)\b'),
        ('电容麦克风', r'电容|\bcondenser\s*(?:mic|microphone)\b'),
        ('枪式麦克风', r'枪式|\bshotgun\s*(?:mic|microphone)\b'),
        ('USB麦克风', r'\busb\b.{0,12}(?:麦克风|话筒|microphone)|(?:麦克风|话筒).{0,12}\busb\b'),
        ('麦克风', r'麦克风|话筒|\bmicrophone\b'),
        ('录音机', r'录音机|\baudio\s*recorder\b|\bfield\s*recorder\b'),
        ('耳机', r'耳机|\bheadphones?\b'),
        ('音箱', r'音箱|扬声器|\bloudspeaker\b'),
        ('效果器', r'效果器|\beffects?\s*(?:processor|pedal)\b'),
        ('功率放大器', r'功放|功率放大器|\bpower\s*amplifier\b'),
        ('音频分配器', r'音频.{0,5}分配器|信号分配器|音分器'),
        ('音频线材', r'音频线|话筒线|麦克风线|\b(?:audio|microphone)\s*cable\b'),
    )
    return next((label for label, pattern in rules if re.search(pattern, str(title), re.I | re.ASCII)), '商品展示')


def concise_title(title, brand, models):
    """Keep a clear brand/model heading and move the product category below it."""
    if not brand or len(models) != 1:
        return title
    model = models[0]
    display_model = search_strategy.DISPLAY.get(model,model)
    generation = re.fullmatch(r'(.+)GEN(\d+)', model)
    base_model = generation[1] if generation else model
    model_text=re.sub(r'\bScarlett\b',' ',str(title),flags=re.I) if brand=='Focusrite' else str(title)
    for match in MODEL_PATTERN.finditer(model_text):
        raw = match.group(1)
        normalized = re.sub(r'[^A-Za-z0-9]', '', raw.replace('+', 'PLUS')).upper()
        if normalized == base_model:
            display_model = re.sub(r'^([A-Za-z]+)[ \t]+(?=\d)', r'\1', raw)
            display_model = re.sub(r'(?<=\d)\s*AI$', ' Ai', display_model, flags=re.I)
            display_model = re.sub(r'\s+', ' ', display_model).strip()
            if generation:
                display_model += f' 第{generation[2]}代'
            break
    return f'{brand} {display_model}'


class Editor:
    def __init__(self, state_file=STATE_FILE, start_worker=True):
        self.state_file = Path(state_file)
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.wake = threading.Event()
        self.stopping = False
        self.worker_busy = False
        self.typography=load_style()
        self.storage=output_storage.load_settings()
        self.preview_lock=threading.Lock()
        self.state = {'tasks': [], 'paused': False, 'settings': {'max_duration_seconds':90, 'text_scale':1.1}}
        if self.state_file.is_file():
            self.state.update(json.loads(self.state_file.read_text(encoding='utf-8')))
        self.state['settings'].setdefault('subtitles_enabled',False)
        self.state['settings'].setdefault('subtitle_language','auto')
        self.state['settings'].setdefault('subtitle_target','zh')
        self.state['settings'].update(batch_template.normalize({},self.state['settings']))
        for task in self.state['tasks']:
            if task['status'] in ACTIVE - {'queued'}:
                task.update(status='queued', progress=0, message='上次任务中断，已排队继续。')
            if task.get('transcription',{}).get('status')=='transcribing':
                task['transcription'].update(status='queued',progress=0,message='上次识别中断，已重新排队。')
        catalog = ROOT / 'data' / 'products.json'
        self.catalog = json.loads(catalog.read_text(encoding='utf-8')) if catalog.is_file() else []
        self.by_id = {r['product_id']:r for r in self.catalog}
        self.by_title = {compact(r['title']):r for r in self.catalog}
        self.sample = json.loads((ROOT / 'configs' / 'neumann_u87ai.json').read_text(encoding='utf-8-sig'))
        self.board=ProjectManager(self)
        self.review=ReviewTools(self)
        self.sources=SourceHub(self)
        self.snapany=SnapAny(self)
        self.downloads=ExportDownloads(self)
        self.connections=ApiConnections(self)
        self.logos=BrandLogos(self)
        self.studio=Studio(self)
        if start_worker:
            threading.Thread(target=self.worker, daemon=True, name='video-worker').start()
            self.wake.set()

    def save(self):
        with self.lock:
            temp = self.state_file.with_suffix('.tmp')
            temp.write_text(json.dumps(self.state, ensure_ascii=False, indent=2), encoding='utf-8')
            os.replace(temp, self.state_file)

    def snapshot(self):
        with self.lock:
            snapshot = copy.deepcopy(self.state)
            snapshot.update(storage_root=str(ROOT), worker_busy=self.worker_busy)
            snapshot['typography']=copy.deepcopy(self.typography)
            snapshot['speech_ready']=speech.ready()
            snapshot['translation_ready']=translation.ready()
            snapshot['counts'] = {status:sum(t['status'] == status for t in snapshot['tasks']) for status in ACTIVE | {'completed','needs_review','failed'}}
            return snapshot

    def update(self, task, **changes):
        with self.lock:
            task.update(changes, updated_at=now())
            self.save()

    def storage_state(self):
        with self.lock:
            directory=Path(self.storage['output_dir'])
            return {'ok':True,**self.storage,'default_dir':str(output_storage.DEFAULT_DIR.resolve()),
                    'exists':directory.is_dir(),'can_save':not self.worker_busy and not any(
                        t['status'] in ACTIVE-{'queued'} or t.get('transcription',{}).get('status')=='transcribing' for t in self.state['tasks'])}

    def save_storage(self,payload):
        output_storage.normalize_directory(payload.get('output_dir'))
        with self.lock:
            if not self.storage_state()['can_save']:
                raise ValueError('当前有任务正在处理，请等本条完成后再修改保存路径；可以先暂停后续队列。')
            storage=output_storage.save_settings(payload['output_dir'])
            self.storage=storage
        return {'ok':True,**storage,'message':'保存路径已记住。之后开始导出的成片、字幕和制作记录使用新目录；旧文件不移动。'}

    def resolve_media(self,value):
        path=unquote(str(value))
        legacy=re.fullmatch(r'/media/([^/\\]+)',path)
        if legacy:
            name=legacy[1]
            if Path(name).name!=name or Path(name).suffix not in {'.mp4','.json','.srt'}: return None
            file=ROOT/'outputs'/name
        else:
            match=re.fullmatch(r'/media/task/([a-f0-9]{12})/([^/\\]+)',path)
            if not match: return None
            with self.lock:
                task=next((t for t in self.state['tasks'] if t['id']==match[1]),None)
                if not task or path not in {unquote(task.get(key,'') or '') for key in ('output_url','metadata_url','subtitle_url','cover_url','title_asset_url')}: return None
                output=task.get('output_file')
                if not output: return None
                video=Path(output)
                if not video.is_absolute() or video.suffix!='.mp4': return None
                allowed={video.name:video,video.with_suffix('.json').name:video.with_suffix('.json'),video.with_suffix('.srt').name:video.with_suffix('.srt')}
                allowed.update({video.with_suffix('.cover.jpg').name:video.with_suffix('.cover.jpg'),video.with_suffix('.title.png').name:video.with_suffix('.title.png')})
                file=allowed.get(match[2])
                if file is None: return None
        # Do not follow a media symlink to an unrelated file on the machine.
        return file if not file.is_symlink() and file.is_file() and file.resolve().parent==file.parent.resolve() else None

    def parse_line(self, text):
        cols = text.split('\t')
        urls = re.findall(r'https?://[^\s|<>]+', text)
        product_id = next((parse_qs(urlparse(u).query).get('id', [''])[0] for u in urls if urlparse(u).hostname in {'item.taobao.com','detail.tmall.com'}), '')
        if not product_id and re.fullmatch(r'\d{6,20}', cols[0].strip()):
            product_id = cols[0].strip()
        record = self.by_id.get(product_id)
        explicit_url = next((video_url(u.rstrip('。,.')) for u in urls if video_url(u.rstrip('。,.')) and urlparse(u).hostname not in {'item.taobao.com','detail.tmall.com'}), None)
        if len(cols) > 1 and re.fullmatch(r'\d{6,20}', cols[0].strip()):
            title = cols[1].strip()
            description = ''
        else:
            fields = [part.strip() for part in text.split('|')]
            title = fields[0]
            description = fields[1] if len(fields) > 1 and not fields[1].startswith('http') else ''
            title = re.sub(r'https?://\S+', '', title).strip()
        if record:
            title = record['title']
        elif compact(title) in self.by_title:
            record = self.by_title[compact(title)]
            product_id = record['product_id']
        brand, models = identity(title)
        if not record and brand and len(models) == 1:
            matches = [row for row in self.catalog if identity(row['title']) == (brand, models)]
            if len(matches) == 1:
                record = matches[0]
                product_id = record['product_id']
        if description:
            description_source = '粘贴的商品简介'
        else:
            category_title = title + ' ' + (record['title'] if record else '')
            description = product_category(category_title, brand, models[0] if len(models) == 1 else '')
            description_source = '商品类别识别（仅说明是什么商品，不生成卖点）'
        requires_review = not title or (not explicit_url and (not brand or len(models) != 1))
        if product_id and not record and re.fullmatch(r'\d+', title or ''):
            title = ''
            requires_review = True
        reason = '请补充商品名称和 YouTube 链接。' if not title else '多个型号或型号不明确，请选择对应视频后继续。'
        return {
            'id': uuid.uuid4().hex[:12], 'input':text,
            'title':concise_title(title, brand, models) if title else f'商品 {product_id}',
            'original_title':title,
            'product_id':product_id, 'brand':brand, 'model':models[0] if len(models)==1 else '',
            'models':models, 'description':description, 'description_source':description_source,
            'status':'needs_review' if requires_review else 'queued', 'progress':0,
            'message':reason if requires_review else '等待处理', 'candidates':[],
            'selected_url':explicit_url or '', 'output_url':'', 'metadata_url':'', 'created_at':now(),
            'taobao_url':record.get('taobao_url','') if record else '',
        }

    def add(self, payload):
        subtitles=subtitle_options(payload)
        text = str(payload.get('text','')).strip()
        if not text:
            raise ValueError('请先粘贴商品名称、商品链接或 Excel 行。')
        template=batch_template.normalize(payload,self.state['settings'])
        maximum = template['max_duration_seconds']
        scale = float(payload.get('text_scale',1.1))
        if not .9 <= scale <= 1.25:
            raise ValueError('字体大小需为 0.9–1.25。')
        lines = [line.strip() for line in text.splitlines() if line.strip() and not line.lower().startswith('宝贝id\t')]
        if len(lines)>100:
            raise ValueError('一次最多 100 个商品，请分批粘贴。')
        added, errors = [], []
        with self.lock:
            if sum(t['status'] in ACTIVE for t in self.state['tasks']) + len(lines) > 100:
                raise ValueError('待处理队列最多 100 条，请等当前队列完成后再添加。')
            keys = {compact(t['input']) for t in self.state['tasks'] if t['status'] in ACTIVE}
            for index, line in enumerate(lines,1):
                key = compact(line)
                if key in keys:
                    errors.append(f'第 {index} 行已在队列，已跳过重复。')
                    continue
                if len(line)>3000:
                    errors.append(f'第 {index} 行超过 3000 字符，请简化。')
                    continue
                task = self.parse_line(line)
                task.update(max_duration_seconds=maximum, text_scale=scale,typography=copy.deepcopy(self.typography),
                            subtitles_enabled=subtitles['enabled'],subtitle_language=subtitles['language'],subtitle_target=subtitles['target'])
                task['batch_clip']=batch_template.snapshot(template)
                self.state['tasks'].append(task)
                self.board.attach(task)
                added.append(task['id'])
                keys.add(key)
            self.state['settings'].update(template,text_scale=scale)
            self.state['settings'].update(subtitles_enabled=subtitles['enabled'],subtitle_language=subtitles['language'],subtitle_target=subtitles['target'])
            self.save()
        self.wake.set()
        return {'ok':True, 'added':len(added), 'task_ids':added, 'errors':errors}

    def save_settings(self,payload):
        template=batch_template.normalize(payload,self.state['settings'])
        scale=float(payload.get('text_scale',self.state['settings'].get('text_scale',1.1)))
        if not .9<=scale<=1.25:raise ValueError('字号倍率需为 0.9–1.25。')
        subtitles=subtitle_options(payload)
        with self.lock:
            before=copy.deepcopy(self.state['settings'])
            self.state['settings'].update(template,text_scale=scale,subtitles_enabled=subtitles['enabled'],
                subtitle_language=subtitles['language'],subtitle_target=subtitles['target'])
            try:self.save()
            except Exception:
                self.state['settings']=before;raise
        return {'ok':True,'settings':copy.deepcopy(self.state['settings']),
                'message':'批量模板已保存，用于之后新建的制作任务；单品时段优先，旧版重做沿用原版设置。'}

    def action(self, task_id, action, payload):
        with self.lock:
            task = next((t for t in self.state['tasks'] if t['id']==task_id), None)
            if not task:
                raise ValueError('任务不存在。')
            if task['status'] in ACTIVE:
                raise ValueError('该任务已在处理队列中。')
            if task.get('transcription',{}).get('status') in {'queued','transcribing'}:
                raise ValueError('请等待这条视频的语音识别完成。')
            if action == 'source':
                url = video_url(payload.get('url',''))
                if not url:
                    raise ValueError('请输入公开 HTTPS 视频页面、分享短链或视频直链。')
                title = str(payload.get('title') or task['title']).strip()
                if not title or len(title)>300:
                    raise ValueError('请输入不超过 300 字符的商品名称。')
                brand, models = identity(title)
                task.update(selected_url=url,title=concise_title(title,brand,models),original_title=title,brand=brand or 'PRODUCT',model=models[0] if len(models)==1 else '')
                task.pop('transcription',None)
                task.pop('subtitle_srt',None)
                if payload.get('description') is not None:
                    task.update(description=str(payload['description'])[:1800], description_source='手动填写的商品简介')
            elif action != 'retry':
                raise ValueError('未知操作。')
            task.update(status='queued',progress=0,message='已重新加入队列',output_url='',metadata_url='',subtitle_url='')
            self.save()
        self.wake.set()
        return {'ok':True}

    def typography_state(self,task_id=''):
        with self.lock:
            task=next((t for t in self.state['tasks'] if t['id']==task_id),None) if task_id else None
            if task_id and task is None:
                raise ValueError('没有找到这条视频，请返回工作台选择。')
            text=copy.deepcopy(task.get('render_text')) if task else None
            text=text or {'brand_title':task['brand'].upper() if task else self.sample['brand_title'],
                         'product_title':task['title'] if task else self.sample['product_title'],
                         'description':task['description'] if task else '\n'.join(self.sample['description_lines'])}
            return {'ok':True,'style':normalize_style(task.get('typography') or self.typography) if task else normalize_style(self.typography),
                    'defaults':copy.deepcopy(DEFAULT_STYLE),'fonts':FONT_CHOICES,'text':text,
                    'task':copy.deepcopy(task),'text_scale':task.get('text_scale',1.1) if task else self.state['settings'].get('text_scale',1.1),
                    'tasks':[{'id':t['id'],'title':t['title'],'status':t['status']} for t in reversed(self.state['tasks'])],
                    'speech_ready':speech.ready(),
                    'translation_ready':translation.ready(),
                    'subtitles':{'enabled':task.get('subtitles_enabled',False) if task else self.state['settings']['subtitles_enabled'],
                                 'language':task.get('subtitle_language','auto') if task else self.state['settings']['subtitle_language'],
                                 'target':task.get('subtitle_target','original') if task else self.state['settings']['subtitle_target'],
                                 'srt':(task.get('subtitle_srt') or task.get('transcription',{}).get('srt','')) if task else ''},
                    'storage_root':str(ROOT)}

    def save_typography(self,payload):
        style=normalize_style(payload.get('style'))
        subtitles=subtitle_options(payload) if 'subtitles' in payload else None
        with self.lock:
            temporary=STYLE_FILE.with_suffix('.tmp')
            temporary.write_text(json.dumps(style,ensure_ascii=False,indent=2),encoding='utf-8')
            os.replace(temporary,STYLE_FILE)
            self.typography=style
            if subtitles:
                self.state['settings'].update(subtitles_enabled=subtitles['enabled'],subtitle_language=subtitles['language'],subtitle_target=subtitles['target'])
                self.save()
        return {'ok':True,'message':'已保存。以后新添加的商品使用这套字体、排版和字幕开关。','style':style}

    def edited_copy(self,task_id,payload):
        style=normalize_style(payload.get('style'))
        text=normalize_text(payload.get('text'))
        subtitles=subtitle_options(payload)
        with self.lock:
            source=next((t for t in self.state['tasks'] if t['id']==task_id),None)
            if source is None:
                raise ValueError('原视频任务不存在。')
            if sum(t['status'] in ACTIVE for t in self.state['tasks'])>=100:
                raise ValueError('队列已满，请等部分视频完成后再生成。')
            task=copy.deepcopy(source)
            task.update(id=uuid.uuid4().hex[:12],title=text['product_title'],render_text=text,
                        description=text['description'],description_source='手动填写的商品简介',typography=style,
                        status='queued',progress=0,message='已保存修改，等待生成新版视频。',
                        output_url='',metadata_url='',created_at=now(),updated_at=now(),edited_from=task_id)
            task.update(subtitles_enabled=subtitles['enabled'],subtitle_language=subtitles['language'],
                        subtitle_srt=subtitles['srt'],subtitle_target=subtitles['target'],subtitle_url='')
            if task.get('transcription',{}).get('status')!='ready':
                task.pop('transcription',None)
            self.state['tasks'].append(task)
            self.board.attach(task)
            self.save()
        self.wake.set()
        return {'ok':True,'task_id':task['id'],'message':'新版已加入队列，原成片仍然保留。'}

    def preview_typography(self,payload):
        style=normalize_style(payload.get('style'))
        text=normalize_text(payload.get('text'))
        task_id=payload.get('task_id') or ''
        state=self.typography_state(task_id)
        task=state['task']
        source=self.existing_source(source_id(task['selected_url'])) if task and task.get('selected_url') else None
        using_sample=source is None
        source=source or renderer.resolve_input(self.sample['source'])
        config=dict(self.sample,typography=style,**{'brand_title':text['brand_title'],'product_title':text['product_title']},
                    description_lines=split_description(text['description']),text_scale=state['text_scale'])
        self.logos.apply(config)
        subtitles=subtitle_options(payload)
        cues=speech.parse_srt(subtitles['srt']) if subtitles['enabled'] else []
        sample=payload.get('subtitle_sample',False)
        if not isinstance(sample,bool):
            raise ValueError('字幕位置示例开关格式不正确。')
        speech_preview_kind='actual' if cues else 'sample' if sample else ''
        if not cues and sample:
            cues=[{'start':0,'end':1,'text':'这是语音字幕位置预览'}]
        source_duration=renderer.duration(renderer.probe(source))
        clip=(task.get('clip') or task.get('batch_clip')) if task and not using_sample else None
        clip_start=clip.get('start',0) if clip else 0
        clip_end=min(source_duration,clip.get('end') or clip_start+state['task'].get('max_duration_seconds',90)) if clip else source_duration
        preview_at=clip_start+min(10,max(.1,clip_end-clip_start)/3)
        if cues:
            cue=cues[0]
            if speech_preview_kind=='actual':
                preview_at=min(clip_start+(cue['start']+cue['end'])/2,source_duration-.1)
            config.update(subtitles_enabled=True,subtitle_srt=speech.write_srt([dict(cue,start=0,end=1)]),speech_preview_time=preview_at)
        key=hashlib.sha256(json.dumps({'config':config,'source':str(source),'mtime':source.stat().st_mtime_ns,
                            'renderer':(ROOT/'scripts/product_render.py').stat().st_mtime_ns,
                            'speech_renderer':(ROOT/'scripts/speech_subtitles.py').stat().st_mtime_ns},sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        folder=ROOT/'work'/'style_previews'/key
        preview=folder/'preview.png'
        layout={}
        overlay=renderer.make_overlay(config,1,layout)
        if not preview.is_file():
            if not self.preview_lock.acquire(timeout=12):
                raise ValueError('预览正在更新，请稍后再试。')
            try:
                if not preview.is_file():
                    folder.mkdir(parents=True,exist_ok=True)
                    renderer.stage_private_fonts(config)
                    (folder/'overlay.ass').write_text(overlay,encoding='utf-8-sig')
                    import brand_logos
                    brand_overlay=brand_logos.overlay(config,folder/'brand_logo.png')
                    (folder/'preview.ffscript').write_text(renderer.make_graph([(0,1)],None,fade_out_seconds=0,brand_index=1 if brand_overlay else None),encoding='utf-8')
                    partial=folder/'preview.partial.png'
                    preview_command=[str(renderer.FFMPEG),'-hide_banner','-loglevel','error','-nostdin','-y','-ss',str(preview_at),'-i',str(source)]
                    if brand_overlay:preview_command+=['-loop','1','-i',str(brand_overlay)]
                    result=subprocess.run(preview_command+[
                        '-filter_complex_threads','1','-/filter_complex','preview.ffscript','-map','[vout]','-frames:v','1',str(partial)],
                        cwd=folder,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=35)
                    if result.returncode or not partial.is_file():
                        raise ValueError('预览生成失败：'+result.stderr.decode('utf-8',errors='replace')[-300:])
                    os.replace(partial,preview)
            finally:
                self.preview_lock.release()
        warnings=layout.get('warnings',[])
        if using_sample and task:
            warnings.append('此商品素材尚未就绪，当前用示例画面预览文字。')
        return {'ok':True,'url':'/preview/'+key+'.png','warnings':warnings,'layout':layout.get('boxes',{}),'using_sample':using_sample,
                'speech_preview':bool(cues),'speech_preview_kind':speech_preview_kind}

    def transcript_state(self,task_id):
        with self.lock:
            task=next((t for t in self.state['tasks'] if t['id']==task_id),None)
            if not task: raise ValueError('任务不存在。')
            return {'ok':True,'transcription':copy.deepcopy(task.get('transcription',{'status':'none'}))}

    def queue_transcription(self,task_id,payload):
        language=speech.language_code(payload.get('language','auto'))
        target=translation.target_code(payload.get('target','zh'))
        if not speech.ready(): raise ValueError('请先双击 D 盘项目中的“安装语音字幕.bat”。')
        if target=='zh' and language!='zh' and not translation.ready(): raise ValueError('请先双击项目中的“安装中文翻译.bat”。')
        with self.lock:
            task=next((t for t in self.state['tasks'] if t['id']==task_id),None)
            if not task: raise ValueError('任务不存在。')
            if task['status'] in ACTIVE: raise ValueError('请等这条视频处理完成，再识别或校对字幕。')
            if task.get('transcription',{}).get('status') in {'queued','transcribing'}:
                raise ValueError('这条视频已经在等待识别。')
            if not (ROOT/'configs'/('task_'+task_id+'.json')).is_file():
                raise ValueError('这条视频还没有原片，请先返回工作台完成素材下载。')
            task['transcription']={'status':'queued','progress':0,'requested_language':language,'requested_target':target,'message':'等待本机识别与翻译；暂停队列时需先恢复。'}
            self.save()
        self.wake.set()
        return {'ok':True,'message':'已排队进行本机语音识别，原成片不变。'}

    def recognize(self,task,config_file,language,batch=False,target='original'):
        output=ROOT/'work'/'editor'/task['id']/'transcript.json'
        self.update(task,transcription={'status':'transcribing','progress':0,'requested_language':language,'requested_target':target,'message':'正在本机识别语音…'})
        def progress(line):
            match=re.search(r'Speech: (\d+)%',line)
            if match:
                percent=int(match[1])
                with self.lock:
                    state=dict(task['transcription'],progress=percent*.7 if target=='zh' else percent)
                    self.update(task,transcription=state,**({'progress':30+percent*.1} if batch else {}))
            translated=re.search(r'Translation: (\d+)%',line)
            if translated:
                with self.lock:
                    self.update(task,transcription=dict(task['transcription'],progress=70+int(translated[1])*.3,message='正在本机翻译成中文…'))
        try:
            self.command([PYTHON,'-B',str(ROOT/'scripts/speech_subtitles.py'),'--config',str(config_file),
                          '--output',str(output),'--language',language,'--target',target],task,'speech',timeout=900,progress_handler=progress)
            result=json.loads(output.read_text(encoding='utf-8'))
            speech.parse_srt(result['srt'])
            self.update(task,transcription=dict(result,status='ready',progress=100,message=('中文字幕已生成，请校对品牌和型号。' if target=='zh' else '识别完成，请校对品牌和型号。') if result['segments'] else '未识别到可靠人声，字幕为空；可手动输入 SRT。'))
            if not batch: self.update(task,subtitle_srt=result['srt'],subtitle_target=target)
            return result
        except Exception as exc:
            self.update(task,transcription={'status':'failed','progress':0,'message':str(exc)[-1200:]})
            raise

    def command(self, args, task, stage, timeout=180, progress_handler=None):
        folder = ROOT / 'work' / 'editor' / task['id']
        folder.mkdir(parents=True, exist_ok=True)
        temp = ROOT / 'work' / 'editor_temp'
        temp.mkdir(parents=True,exist_ok=True)
        env = dict(os.environ, PYTHONPATH=str(ROOT/'tools'/'python_packages'), PYTHONUTF8='1', PYTHONIOENCODING='utf-8', PYTHONDONTWRITEBYTECODE='1', TEMP=str(temp), TMP=str(temp))
        log = folder / (stage + '.log')
        with log.open('w',encoding='utf-8') as handle:
            if progress_handler:
                proc = subprocess.Popen(args,stdout=subprocess.PIPE,stderr=handle,cwd=ROOT,env=env,text=True,encoding='utf-8',errors='replace')
                deadline_hit = threading.Event()
                def expire():
                    if proc.poll() is None:
                        deadline_hit.set()
                        subprocess.run(['taskkill','/PID',str(proc.pid),'/T','/F'],capture_output=True)
                timer = threading.Timer(timeout,expire)
                timer.daemon=True
                timer.start()
                result_lines=[]
                try:
                    for line in proc.stdout:
                        result_lines.append(line)
                        handle.write(line)
                        handle.flush()
                        progress_handler(line)
                    code=proc.wait()
                finally:
                    timer.cancel()
                    if proc.stdout:proc.stdout.close()
                if deadline_hit.is_set():
                    raise RuntimeError(f'{stage} 超时，请重试或更换较短的素材。')
                stdout=''.join(result_lines)
            else:
                proc=subprocess.Popen(args,stdout=subprocess.PIPE,stderr=handle,cwd=ROOT,env=env,text=True,encoding='utf-8',errors='replace')
                try:
                    stdout,_=proc.communicate(timeout=timeout)
                except subprocess.TimeoutExpired as exc:
                    subprocess.run(['taskkill','/PID',str(proc.pid),'/T','/F'],capture_output=True)
                    proc.communicate()
                    raise RuntimeError(f'{stage} 超时，请检查网络后重试。') from exc
                code=proc.returncode
        if code:
            detail=log.read_text(encoding='utf-8')[-1200:]
            raise RuntimeError(f'{stage} 失败：{detail or stdout[-500:]}')
        return stdout

    def ytdlp(self):
        return [PYTHON,'-B','-m','yt_dlp','--ignore-config','--no-playlist','--socket-timeout','20','--retries','1','--js-runtimes',f'node:{NODE}','--remote-components','ejs:github','--cache-dir',str(ROOT/'cache'/'yt-dlp')]

    def search(self,task):
        selected=self.state['settings'].get('source_scope','all')
        video_sources.scope(selected)
        merged={};attempts=[];responses=0
        if selected!='domestic':
            try:
                for item in self.search_youtube(task):merged[item['url']]=dict(item,platform='YouTube',verified_video=True)
                responses+=1
            except RuntimeError:pass
            attempts.extend(dict(a,engine='YouTube') for a in task.get('search_report',{}).get('attempts',[]))
        target=search_strategy.plan(task,identity)
        self.update(task,brand=target['brand'],models=target['models'],model=target['models'][0] if len(target['models'])==1 else '')
        if selected!='youtube':
            entries,extra,success=self.sources.additional(task,target,selected);attempts.extend(extra);responses+=success
            for item in entries:merged.setdefault(item['url'],item)
        ranked=[]
        for candidate in merged.values():
            item=dict(candidate,**search_strategy.evaluate(candidate,target,identity,BRANDS))
            if not candidate.get('verified_video',False):item.update(auto_select=False,match_reason=item['match_reason']+'；网页结果尚未验证，请预览或下载核对')
            ranked.append(item)
        ranked.sort(key=lambda c:(c['auto_select'],c['score']),reverse=True)
        self.update(task,search_report={'version':search_strategy.VERSION,'scope':selected,'name':target['name'],'attempts':attempts,
            'unique_candidates':len(ranked),'target_models':target['models'],'requires_variant':len(target['models'])!=1})
        if not responses and not ranked:raise RuntimeError('所选来源检索连接失败，不代表没有对应视频。可调整来源范围、换关键词或直接粘贴视频链接。')
        return ranked[:40]

    def search_youtube(self, task):
        target=search_strategy.plan(task,identity)
        self.update(task,brand=target['brand'],models=target['models'],model=target['models'][0] if len(target['models'])==1 else '')
        cache=DATA/'search_cache_v2'
        cache.mkdir(parents=True,exist_ok=True)
        merged={};attempts=[];responses=0
        for index,query in enumerate(target['queries']):
            self.update(task,progress=5+index*4,message=f'扩大检索：第 {index+1}/{len(target["queries"])} 轮 · {query}')
            filename=cache/(hashlib.sha256((str(search_strategy.VERSION)+query).encode()).hexdigest()[:24]+'.json')
            candidates=None;cached=False
            if filename.is_file():
                try:
                    saved=json.loads(filename.read_text(encoding='utf-8'))
                    ttl=86400 if saved.get('candidates') else 600
                    valid=isinstance(saved.get('candidates'),list) and len(saved['candidates'])<=12 and all(isinstance(c,dict) and youtube_url(c.get('url'))==c.get('url') for c in saved['candidates'])
                    if valid and saved.get('version')==search_strategy.VERSION and time.time()-saved['created']<ttl:
                        candidates=saved['candidates'];cached=True
                except (OSError,ValueError,KeyError,TypeError): pass
            try:
                if candidates is None:
                    output=self.command(self.ytdlp()+['--flat-playlist','--dump-single-json','--skip-download',f'ytsearch12:{query}'],task,f'search_{index+1}',timeout=60)
                    data=json.loads(output)
                    candidates=[]
                    for entry in (data.get('entries') or [])[:12]:
                        if not isinstance(entry,dict): continue
                        url=youtube_url(entry.get('webpage_url') or entry.get('url') or '')
                        if not url and re.fullmatch(r'[\w-]{11}',str(entry.get('id',''))): url=f"https://www.youtube.com/watch?v={entry['id']}"
                        if url:
                            candidates.append({'id':source_id(url),'url':url,'title':entry.get('title',''),
                                'channel':entry.get('channel') or entry.get('uploader',''),'duration':entry.get('duration') or 0,
                                'is_live':entry.get('is_live',False),'availability':entry.get('availability')})
                    temporary=filename.with_suffix('.tmp')
                    temporary.write_text(json.dumps({'version':search_strategy.VERSION,'created':time.time(),'candidates':candidates},ensure_ascii=False),encoding='utf-8')
                    os.replace(temporary,filename)
                responses+=1
                attempts.append({'query':query,'count':len(candidates),'cached':cached})
                for candidate in candidates:
                    url=candidate['url']
                    if url not in merged: merged[url]=dict(candidate,queries=[])
                    if query not in merged[url]['queries']: merged[url]['queries'].append(query)
            except (RuntimeError,ValueError,OSError) as error:
                attempts.append({'query':query,'error':str(error)[-500:]})
            ranked=[dict(c,**search_strategy.evaluate(c,target,identity,BRANDS)) for c in merged.values()]
            if ranked and max(c['score'] for c in ranked)>=100: break
            if index>=1 and any(c['auto_select'] for c in ranked): break
        # The known verified demo is a reusable exact-model candidate, after a real initial search.
        if target['brand'].lower()=='neumann' and target['models']==['U87AI']:
            url=self.sample['source_references']['video']
            merged.setdefault(url,{'id':'FlT0bAc9Xp4','url':url,'title':'Get Real, Get the Original | The Neumann U 87 Ai','channel':'Georg Neumann GmbH','duration':43,'queries':['本机已核对的素材']})
        ranked=[dict(c,**search_strategy.evaluate(c,target,identity,BRANDS)) for c in merged.values()]
        ranked.sort(key=lambda c:(c['auto_select'],c['score']),reverse=True)
        report={'version':search_strategy.VERSION,'name':target['name'],'attempts':attempts,'unique_candidates':len(ranked),
                'target_models':target['models'],'requires_variant':len(target['models'])!=1}
        self.update(task,search_report=report)
        if not responses and not ranked:
            raise RuntimeError('YouTube 检索连接失败，不是商品没有视频。已尝试不同关键词，请检查网络后重试；详情中可查看检索记录。')
        return ranked[:30]

    def existing_source(self, video_id):
        for info in (ROOT/'sources').glob('*/source.info.json'):
            try:
                data=json.loads(info.read_text(encoding='utf-8'))
                source=info.with_name('source.mp4')
                if (data.get('_workbench_source_id') or data.get('id'))==video_id and source.is_file() and source.stat().st_size>10000:
                    return source
            except (OSError,ValueError):
                continue
        return None

    def process(self, task):
        if self.board.skip_task_if_needed(task): return
        self.update(task,status='searching',progress=5,message='按品牌和型号检索所选国内外来源…')
        if not task.get('selected_url'):
            candidates=self.search(task)
            self.update(task,candidates=candidates)
            if not candidates or not candidates[0].get('auto_select',False):
                reason='商品有多个型号或缺少可唯一识别的型号；已检索候选，请指定具体型号或人工采用。' if len(task.get('models',[]))!=1 else '已扩大检索，但没有可自动采用的明确匹配；请查看候选原因，或调整“检索名称”重试。'
                self.update(task,status='needs_review',progress=0,message=reason)
                return
            self.update(task,selected_url=candidates[0]['url'],message=f"已选：{candidates[0]['title']}")
        if task.get('job_type')=='collect':
            with self.lock:
                product=self.board.by_id.get(task.get('board_product_id'))
                if product:
                    if product.get('clip',{}).get('source_url')!=task['selected_url']:product.pop('clip',None)
                    product.update(source_url=task['selected_url'],updated_at=now())
                    self.save()
            self.update(task,status='links_ready',progress=100,message='链接已收集，尚未下载或制作。请核对型号和素材使用授权。')
            return
        video_id=source_id(task['selected_url'])
        self.update(task,status='downloading',progress=25,message='下载原片（优先 1080p），已有素材直接复用…')
        source=self.existing_source(video_id)
        if source is None:
            folder=ROOT/'sources'/(f'yt_{video_id}' if youtube_url(task['selected_url']) else video_id)
            folder.mkdir(parents=True,exist_ok=True)
            def local_download():
                if youtube_url(task['selected_url']):
                    self.command(self.ytdlp()+['--max-filesize','300M','--match-filter','duration <= 900 & !is_live','--no-overwrites','--no-progress','--write-info-json','--ffmpeg-location',str(ROOT/'tools'/'ffmpeg'/'bin'),'-f','bv[height<=1080][ext=mp4]+ba[ext=m4a]/b[height<=1080][ext=mp4]','--merge-output-format','mp4','-o',str(folder/'source.%(ext)s'),task['selected_url']],task,'download',timeout=900)
                else:
                    try:self.command([PYTHON,'-B',str(ROOT/'scripts/multisite_worker.py'),'download',task['selected_url'],str(folder)],task,'download',timeout=900)
                    except RuntimeError as error:raise RuntimeError(video_sources.failure(error)) from error
            self.snapany.download(task,task['selected_url'],folder,local_download)
            source=folder/'source.mp4'
            if not source.is_file():
                raise RuntimeError('素材未下载完成，可能超过 15 分钟/体积限制，或需要登录。请换一个较短视频。')
        if task.get('job_type')=='download':
            self.update(task,status='downloaded',progress=100,source_path=str(source),message='素材已下载到 D 盘，尚未制作成片。')
            return
        config=copy.deepcopy(self.sample) if task['brand'].lower()=='neumann' and task['model']=='U87AI' else {}
        original_title=task.get('original_title') or task['title']
        record=self.by_id.get(task.get('product_id'))
        if task.get('description_source') not in {'粘贴的商品简介','手动填写的商品简介'}:
            category_title=original_title+' '+(record['title'] if record else '')
            # Refresh only the task being processed; completed history and its
            # existing media/configuration files are never migrated in place.
            self.update(task,description=product_category(category_title,task['brand'],task['model']),description_source='商品类别识别（仅说明是什么商品，不生成卖点）')
        display_title=concise_title(original_title,task['brand'],[task['model']] if task['model'] else [])
        references=copy.deepcopy(config.get('source_references',{}))
        references.update(video=task['selected_url'],taobao_item=task.get('taobao_url',''),original_title=original_title,description_source=task['description_source'])
        config.update(product_id=task['product_id'] or task['id'],output_prefix='product_'+(task['product_id'] or task['id']),source=str(source.relative_to(ROOT)),source_audio=None,allow_silent_output=not bool(youtube_url(task['selected_url'])),brand_title=task['brand'].upper() or 'PRODUCT',product_title=display_title,description_lines=split_description(task['description']),font='Source Han Sans CN',title_font='Oswald',brand_font='Oswald',description_font='Source Han Sans CN',text_scale=task['text_scale'],max_duration_seconds=task['max_duration_seconds'],segments=[{'start':0,'end':None}],source_references=references,text_review_note='按商品型号匹配素材；简介来源：'+task['description_source']+'。超过上限时保留片头起的连续片段，未做语义高光选择。')
        config['typography']=normalize_style(task.get('typography') or self.typography)
        config['fade_out_seconds']=1.0
        clip=task.get('clip')
        if clip and clip.get('source_url')==task.get('selected_url'):
            available=renderer.duration(renderer.probe(source))
            clip=clip_edit.normalize(clip,video_url,available)
            config['segments']=[{'start':clip['start'],'end':clip['end']}]
        elif task.get('batch_clip'):
            # The batch range is not source-bound: short sources are clipped at
            # EOF by choose_segments; a start past EOF fails this video only.
            config['segments']=[copy.deepcopy(task['batch_clip'])]
        config['output_name']=export_naming.product_name(task)
        with self.lock:
            config['output_dir']=self.storage['output_dir']
        if task.get('render_text'):
            text=normalize_text(task['render_text'])
            config.update(brand_title=text['brand_title'],product_title=text['product_title'],description_lines=split_description(text['description']))
        self.logos.prepare(config)
        if task.get('director_options') or task.get('job_type')=='analyze':
            config=self.studio.direct(task,source,config)
            if config is None:return
        config_file=ROOT/'configs'/('task_'+task['id']+'.json')
        config_file.write_text(json.dumps(config,ensure_ascii=False,indent=2),encoding='utf-8')
        if task.get('subtitles_enabled'):
            srt=task.get('subtitle_srt','')
            if not srt.strip():
                self.update(task,status='transcribing',progress=30,message='本机识别原声字幕，无 API 费用…')
                transcript=self.recognize(task,config_file,task.get('subtitle_language','auto'),batch=True,target=task.get('subtitle_target','original'))
                srt=transcript['srt']
            speech.parse_srt(srt)
            config.update(subtitles_enabled=True,subtitle_srt=srt)
            config['subtitle_target']=task.get('subtitle_target','original')
            if task.get('transcription',{}).get('status')=='ready':
                config['transcription']={key:task['transcription'].get(key) for key in ('engine','engine_version','model','language','elapsed_seconds','api_calls','cache_hit')}
                config['translation']=task['transcription'].get('translation')
                config['original_subtitle_srt']=task['transcription'].get('original_srt','')
            config_file.write_text(json.dumps(config,ensure_ascii=False,indent=2),encoding='utf-8')
        self.update(task,status='rendering',progress=40,message='正在排版、压缩并导出 1080×1440 成片…')
        output_path=None
        def progress(line):
            nonlocal output_path
            match=re.search(r'Rendering: (\d+)%',line)
            if match:
                self.update(task,progress=min(99,40+int(match[1])*.59))
            if line.startswith('Saved: '):
                output_path=Path(line.removeprefix('Saved: ').strip())
        self.command([PYTHON,'-B',str(ROOT/'scripts'/'product_render.py'),'--config',str(config_file)],task,'render',timeout=1800,progress_handler=progress)
        if not output_path or not output_path.is_file() or output_path.parent.resolve()!=Path(config['output_dir']).resolve():
            raise RuntimeError('渲染完成但没有找到可交付文件，请查看任务日志。')
        subtitle_file=output_path.with_suffix('.srt')
        media_prefix='/media/task/'+task['id']+'/'
        self.update(task,status='completed',progress=100,message='已完成，可播放或下载。'+(' 未识别到可靠人声，本条没有语音字幕。' if task.get('subtitles_enabled') and not config.get('subtitle_srt','').strip() else ''),output_file=str(output_path.resolve()),output_url=media_prefix+quote(output_path.name),metadata_url=media_prefix+quote(output_path.with_suffix('.json').name),
                    subtitle_url=media_prefix+quote(subtitle_file.name) if subtitle_file.is_file() else '',
                    cover_url=media_prefix+quote(output_path.with_suffix('.cover.jpg').name) if output_path.with_suffix('.cover.jpg').is_file() else '',
                    title_asset_url=media_prefix+quote(output_path.with_suffix('.title.png').name) if output_path.with_suffix('.title.png').is_file() else '')

    def worker(self):
        while not self.stopping:
            self.wake.wait(2)
            self.wake.clear()
            with self.lock:
                task=None if self.state['paused'] else next((t for t in self.state['tasks'] if t['status']=='queued'),None)
                speech_task=None if self.state['paused'] or task else next((t for t in self.state['tasks'] if t.get('transcription',{}).get('status')=='queued'),None)
                self.worker_busy=bool(task or speech_task)
            if task is None and speech_task is None:
                continue
            try:
                if task:
                    self.process(task)
                else:
                    self.recognize(speech_task,ROOT/'configs'/('task_'+speech_task['id']+'.json'),speech_task['transcription']['requested_language'],target=speech_task['transcription'].get('requested_target','original'))
            except Exception as exc:
                if task: self.update(task,status='failed',message=str(exc)[-1500:],progress=0)
            finally:
                self.worker_busy=False
                self.wake.set()


EDITOR=None


class Handler(BaseHTTPRequestHandler):
    def log_message(self,fmt,*args):
        if args and any(path in str(args[0]) for path in ('/api/state','/api/board','/media/')):
            return
        super().log_message(fmt,*args)

    def allowed_host(self):
        return self.headers.get('Host','') in {f'127.0.0.1:{PORT}',f'localhost:{PORT}'}

    def send_json(self,data,status=200):
        body=json.dumps(data,ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Content-Length',str(len(body)))
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if not self.allowed_host() or self.headers.get('Origin') not in {f'http://127.0.0.1:{PORT}',f'http://localhost:{PORT}'}:
            return self.send_json({'error':'只接受本机页面发起的请求。'},403)
        if self.path=='/api/brand-logos/upload':
            try:
                length=int(self.headers.get('Content-Length','0'))
                if not 0<length<=8*1024*1024:raise ValueError('图片为空或超过 8 MB。')
                return self.send_json(EDITOR.logos.upload(unquote(self.headers.get('X-Brand-Name','')),self.rfile.read(length)))
            except (ValueError,TypeError) as error:return self.send_json({'error':str(error)},400)
            except Exception:return self.send_json({'error':'Logo 上传失败，请检查文件或磁盘权限。'},500)
        upload_image=re.fullmatch(r'/api/products/([a-f0-9]{12})/image-upload',self.path)
        if upload_image:
            try:
                length=int(self.headers.get('Content-Length','0'))
                if not 0<length<=8*1024*1024:raise ValueError('图片为空或超过 8 MB。')
                return self.send_json(EDITOR.review.upload_image(upload_image[1],self.rfile.read(length)))
            except (ValueError,TypeError) as exc:return self.send_json({'error':str(exc)},400)
            except Exception:return self.send_json({'error':'图片保存失败，请检查 D 盘写入权限或剩余空间。'},500)
        if self.path=='/api/imports/preview':
            try:
                if self.headers.get('Content-Type','').split(';')[0]!='application/octet-stream':
                    return self.send_json({'error':'请选择 Excel 或 CSV 文件。'},415)
                length=int(self.headers.get('Content-Length','0'))
                if not 0<length<=8*1024*1024: raise ValueError('文件为空或超过 8 MB。')
                name=unquote(self.headers.get('X-Upload-Filename',''))
                return self.send_json(EDITOR.board.upload(name,self.rfile.read(length)))
            except (ValueError,TypeError) as exc:
                return self.send_json({'error':str(exc)},400)
        if self.headers.get('Content-Type','').split(';')[0]!='application/json':
            return self.send_json({'error':'需要 JSON 请求。'},415)
        try:
            length=int(self.headers.get('Content-Length','0'))
            if not 0<length<=256000:
                raise ValueError('请求体为空或过大。')
            payload=json.loads(self.rfile.read(length))
            if not isinstance(payload,dict):
                raise ValueError('请求格式不正确。')
            if self.path=='/api/board/paste': return self.send_json(EDITOR.board.paste(payload))
            if self.path=='/api/storage': return self.send_json(EDITOR.save_storage(payload))
            if self.path=='/api/studio/settings':return self.send_json(EDITOR.studio.save(payload))
            if self.path=='/api/studio/jobs':return self.send_json(EDITOR.studio.jobs(payload))
            if self.path=='/api/connections':return self.send_json(EDITOR.connections.save(payload))
            if self.path=='/api/connections/check':return self.send_json(EDITOR.connections.check())
            if self.path=='/api/brand-logos/settings':return self.send_json(EDITOR.logos.save(payload))
            if self.path=='/api/sources/settings':return self.send_json(EDITOR.sources.save(payload))
            if self.path=='/api/sources/probe':return self.send_json(EDITOR.sources.probe(payload))
            if self.path=='/api/snapany/settings':return self.send_json(EDITOR.snapany.save(payload))
            if self.path=='/api/snapany/check':return self.send_json(EDITOR.snapany.check())
            if self.path=='/api/snapany/extract':return self.send_json(EDITOR.snapany.preview(payload))
            if self.path=='/api/brand-logos/find':return self.send_json(EDITOR.logos.discover(payload))
            if self.path=='/api/brand-logos/frame':return self.send_json(EDITOR.logos.frame(payload))
            if self.path=='/api/brand-logos/crop':return self.send_json(EDITOR.logos.crop(payload))
            if self.path=='/api/brand-logos/generate':return self.send_json({'ok':True,'asset':EDITOR.logos.generate(payload.get('brand')),'message':'已生成本地文字字标，明确标为非官方。未调用 AI/API；若已有采用素材，不会自动替换。'})
            cloud_review=re.fullmatch(r'/api/products/([a-f0-9]{12})/cloud-review',self.path)
            if cloud_review:return self.send_json(EDITOR.connections.review(cloud_review[1],payload))
            if self.path=='/api/exports/batches':return self.send_json(EDITOR.downloads.prepare(payload))
            if self.path=='/api/exports/video':return self.send_json(EDITOR.downloads.video_ticket(payload))
            if self.path=='/api/board/jobs': return self.send_json(EDITOR.board.bulk(payload))
            if self.path=='/api/board/reset': return self.send_json(EDITOR.board.reset(payload))
            if self.path=='/api/skip-rules/preview': return self.send_json(EDITOR.board.rules_preview(payload))
            if self.path=='/api/skip-rules': return self.send_json(EDITOR.board.save_rules(payload))
            if self.path=='/api/links/extract': return self.send_json(extract_links(payload.get('text','')))
            if self.path=='/api/imports/existing':
                name='副本需要添加首图视频的商品 11-6.xlsx'
                file=ROOT/name
                if not file.is_file(): raise ValueError('项目中的原始表格不存在，请上传文件。')
                if file.stat().st_size>8*1024*1024: raise ValueError('文件超过 8 MB。')
                return self.send_json(EDITOR.board.upload(name,file.read_bytes()))
            if self.path=='/api/imports/recent':
                files=[file for file in EDITOR.board.import_folder().glob('*.json') if re.fullmatch('[a-f0-9]{32}',file.stem)]
                if not files: raise ValueError('还没有上传过表格，请先选择 Excel 文件。')
                token=max(files,key=lambda file:file.stat().st_mtime_ns).stem
                return self.send_json(EDITOR.board.analyze({'token':token,'rule':'all'}))
            if self.path=='/api/imports/analyze': return self.send_json(EDITOR.board.analyze(payload))
            if self.path=='/api/imports/commit': return self.send_json(EDITOR.board.commit(payload))
            if self.path=='/api/settings':
                return self.send_json(EDITOR.save_settings(payload))
            approval=re.fullmatch(r'/api/products/([a-f0-9]{12})/approval',self.path)
            if approval:return self.send_json(EDITOR.studio.approval(approval[1],payload))
            cover=re.fullmatch(r'/api/products/([a-f0-9]{12})/cover',self.path)
            if cover:return self.send_json(EDITOR.studio.cover_choice(cover[1],payload))
            product=re.fullmatch(r'/api/products/([a-f0-9]{12})/(redo|source|mark|skip|search-name|clip|generate|image-url|image-fetch)',self.path)
            if product:
                method={'redo':EDITOR.board.redo,'source':EDITOR.board.set_source,'mark':EDITOR.board.mark,'skip':EDITOR.board.set_skip,'search-name':EDITOR.board.set_search_name,
                        'clip':EDITOR.board.save_clip,'generate':EDITOR.board.generate,'image-url':EDITOR.review.set_image_url,'image-fetch':EDITOR.review.fetch_image}[product[2]]
                return self.send_json(method(product[1],payload))
            if self.path=='/api/tasks':
                return self.send_json(EDITOR.add(payload))
            if self.path=='/api/typography':
                return self.send_json(EDITOR.save_typography(payload))
            if self.path=='/api/typography/preview':
                return self.send_json(EDITOR.preview_typography(payload))
            appearance=re.fullmatch(r'/api/tasks/([a-f0-9]{12})/appearance',self.path)
            if appearance:
                return self.send_json(EDITOR.edited_copy(appearance[1],payload))
            transcription=re.fullmatch(r'/api/tasks/([a-f0-9]{12})/transcribe',self.path)
            if transcription:
                return self.send_json(EDITOR.queue_transcription(transcription[1],payload))
            if self.path=='/api/control':
                action=payload.get('action')
                if action not in {'pause','resume'}:
                    raise ValueError('未知操作。')
                with EDITOR.lock:
                    EDITOR.state['paused']=action=='pause'
                    EDITOR.save()
                EDITOR.wake.set()
                return self.send_json({'ok':True})
            match=re.fullmatch(r'/api/tasks/([a-f0-9]{12})/(retry|source)',self.path)
            if match:
                return self.send_json(EDITOR.action(match[1],match[2],payload))
            return self.send_json({'error':'路径不存在。'},404)
        except (ValueError,TypeError) as exc:
            return self.send_json({'error':str(exc)},400)
        except Exception as exc:
            return self.send_json({'error':str(exc)},500)

    def do_GET(self):
        if not self.allowed_host():
            return self.send_json({'error':'不支持的访问地址。'},403)
        path=unquote(urlparse(self.path).path)
        if path=='/api/exports':return self.send_json(EDITOR.downloads.snapshot(parse_qs(urlparse(self.path).query)))
        if path=='/api/studio':return self.send_json(EDITOR.studio.snapshot())
        if path=='/api/sources':return self.send_json(EDITOR.sources.snapshot())
        if path=='/api/snapany':
            try:return self.send_json(EDITOR.snapany.snapshot())
            except ValueError as error:return self.send_json({'error':str(error)},400)
        if path=='/api/brand-logos':
            try:return self.send_json(EDITOR.logos.snapshot())
            except ValueError as error:return self.send_json({'error':str(error)},400)
        if path=='/api/connections':
            try:return self.send_json(EDITOR.connections.snapshot())
            except ValueError as error:return self.send_json({'error':str(error)},400)
        if re.fullmatch(r'/export-download/[a-f0-9]{32}',path):
            return self.serve_export(path.rsplit('/',1)[1])
        if path=='/api/source-review':
            try:
                url=parse_qs(urlparse(self.path).query).get('url',[''])[0]
                if not video_url(url):raise ValueError('请输入公开 HTTPS 视频页面、分享短链或视频直链。')
                return self.send_json({'ok':True,'source':EDITOR.review.source(url)})
            except (ValueError,TypeError) as exc:return self.send_json({'error':str(exc)},400)
        if path=='/api/storage': return self.send_json(EDITOR.storage_state())
        if path=='/api/skip-rules': return self.send_json(EDITOR.board.rules_preview())
        if path=='/api/board/reset-preview': return self.send_json(EDITOR.board.reset_preview())
        if path=='/api/board':
            try: return self.send_json(EDITOR.board.snapshot(parse_qs(urlparse(self.path).query)))
            except (ValueError,TypeError) as error: return self.send_json({'error':str(error)},400)
        product=re.fullmatch(r'/api/products/([a-f0-9]{12})',path)
        if product:
            try: return self.send_json(EDITOR.board.detail(product[1]))
            except ValueError as error: return self.send_json({'error':str(error)},404)
        if path=='/api/state':
            return self.send_json(EDITOR.snapshot())
        if path=='/api/health':
            return self.send_json({'ok':True,'app':'product-video-editor'})
        if path=='/api/typography':
            try:
                return self.send_json(EDITOR.typography_state(parse_qs(urlparse(self.path).query).get('task',[''])[0]))
            except ValueError as error:
                return self.send_json({'error':str(error)},404)
        transcription=re.fullmatch(r'/api/tasks/([a-f0-9]{12})/transcript',path)
        if transcription:
            try: return self.send_json(EDITOR.transcript_state(transcription[1]))
            except ValueError as error: return self.send_json({'error':str(error)},404)
        if path=='/':
            file=ROOT/'web'/'board.html'
        elif path=='/typography':
            file=ROOT/'web'/'typography.html'
        elif path=='/api-settings':
            file=ROOT/'web'/'api-settings.html'
        elif path=='/brand-assets':
            file=ROOT/'web'/'brand-assets.html'
        elif path=='/sources':file=ROOT/'web'/'sources.html'
        elif path in {'/web/sources.js','/web/sources.css'}:file=ROOT/path.lstrip('/')
        elif path in {'/web/brand-assets.js','/web/brand-assets.css'}:
            file=ROOT/path.lstrip('/')
        elif path.startswith('/brand-logo/'):
            file=EDITOR.logos.resolve(path)
            if file is None:return self.send_json({'error':'Logo 素材不存在。'},404)
        elif path in {'/web/api-settings.js','/web/api-settings.css'}:
            file=ROOT/path.lstrip('/')
        elif path in {'/web/app.js','/web/style.css','/web/typography.js','/web/typography.css','/web/speech.css','/web/board.js','/web/board.css','/web/board-responsive.css','/web/review.js','/web/review.css','/web/downloads.js','/web/downloads.css','/web/studio.css','/web/studio.js','/web/font-library.css','/web/typography-presets.js'}:
            file=ROOT/path.lstrip('/')
        elif re.fullmatch(r'/font-assets/[a-zA-Z0-9_-]+/[a-zA-Z0-9_.-]+\.(?:ttf|otf)',path):
            file=ROOT/'assets/fonts'/path.removeprefix('/font-assets/')
            allowed_fonts={p for paths in renderer.PRIVATE_FONTS.values() for p in paths}
            if file not in allowed_fonts or file.is_symlink():return self.send_json({'error':'字体不存在。'},404)
        elif path.startswith('/director/'):
            file=EDITOR.studio.resolve_asset(path)
            if file is None:return self.send_json({'error':'分析画面不存在。'},404)
        elif re.fullmatch(r'/preview/[a-f0-9]{64}\.png',path):
            file=ROOT/'work'/'style_previews'/path[9:-4]/'preview.png'
        elif path.startswith('/media/'):
            file=EDITOR.resolve_media(urlparse(self.path).path)
            if file is None:
                return self.send_json({'error':'不支持的文件。'},404)
        elif re.fullmatch(r'/source-preview/(?:[\w-]{11}|v_[a-f0-9]{24})\.mp4',path):
            file=EDITOR.existing_source(path[len('/source-preview/'):-4])
            if file is None or file.is_symlink():return self.send_json({'error':'本地原片尚未下载完成。'},404)
        elif re.fullmatch(r'/product-image/[a-f0-9]{12}/[a-f0-9]{32}\.(?:jpg|png|webp)',path):
            pid=path.split('/')[2]
            with EDITOR.lock:
                product=EDITOR.board.by_id.get(pid)
                if not product or product.get('product_image',{}).get('url')!=path:return self.send_json({'error':'图片不存在。'},404)
            file=ROOT/'data'/'editor'/'product_images'/pid/path.split('/')[-1]
            if file.is_symlink():return self.send_json({'error':'图片不存在。'},404)
        else:
            return self.send_json({'error':'路径不存在。'},404)
        if not file.is_file():
            return self.send_json({'error':'文件不存在。'},404)
        size=file.stat().st_size
        start,end,status=0,size-1,200
        range_header=self.headers.get('Range')
        if range_header:
            match=re.fullmatch(r'bytes=(\d*)-(\d*)',range_header)
            if not match or (not match[1] and not match[2]):
                return self.send_json({'error':'不支持的字节范围。'},416)
            if not match[1]:
                start=max(0,size-int(match[2]))
            else:
                start=int(match[1])
                end=min(size-1,int(match[2]) if match[2] else size-1)
            if start>end or start>=size:
                return self.send_json({'error':'字节范围超出文件。'},416)
            status=206
        self.send_response(status)
        self.send_header('Content-Type',mimetypes.guess_type(file.name)[0] or 'application/octet-stream')
        self.send_header('Content-Length',str(end-start+1))
        self.send_header('Accept-Ranges','bytes')
        if path.startswith('/media/'):
            self.send_header('Content-Disposition',"inline; filename*=UTF-8''"+quote(file.name))
        if file.suffix in {'.html','.js','.css'}:
            self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Referrer-Policy','strict-origin-when-cross-origin')
        self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self' https://www.youtube.com/iframe_api https://www.youtube.com/s/player/; style-src 'self' 'unsafe-inline'; img-src 'self' data: https://i.ytimg.com https://*.alicdn.com https://*.tbcdn.cn; media-src 'self'; frame-src https://www.youtube.com https://www.youtube-nocookie.com https://player.bilibili.com; connect-src 'self'; frame-ancestors 'none'")
        if status==206:
            self.send_header('Content-Range',f'bytes {start}-{end}/{size}')
        self.end_headers()
        try:
            with file.open('rb') as handle:
                handle.seek(start)
                remaining=end-start+1
                while remaining>0:
                    data=handle.read(min(256*1024,remaining))
                    if not data:
                        break
                    self.wfile.write(data)
                    remaining-=len(data)
        except (BrokenPipeError,ConnectionResetError,ConnectionAbortedError):
            pass

    def serve_export(self,token):
        # A separate, unguessable attachment URL: playing /media never counts
        # as downloading. Partial transfers and cancelled responses do not count.
        try:
            file,name,items,kind=EDITOR.downloads.delivery(token)
        except ValueError as exc:return self.send_json({'error':str(exc)},404)
        if self.headers.get('Range'):return self.send_json({'error':'下载不支持分段续传，请重新点击下载。'},416)
        started=False
        try:
            with file.open('rb') as handle:
                size=os.fstat(handle.fileno()).st_size
                self.send_response(200)
                self.send_header('Content-Type','application/zip' if kind=='batch' else 'video/mp4')
                self.send_header('Content-Disposition',"attachment; filename*=UTF-8''"+quote(name))
                self.send_header('Content-Length',str(size))
                self.send_header('Cache-Control','no-store')
                self.send_header('X-Content-Type-Options','nosniff')
                self.end_headers()
                started=True
                remaining=size
                while remaining:
                    chunk=handle.read(min(1024*1024,remaining))
                    if not chunk:raise OSError('文件在传输中发生变化。')
                    self.wfile.write(chunk);remaining-=len(chunk)
                self.wfile.flush()
            EDITOR.downloads.record_sent(items,kind,token)
        except (BrokenPipeError,ConnectionResetError,OSError):
            if not started:return self.send_json({'error':'下载文件已移走或不可读，请重新打包。'},404)
            return


def main():
    global EDITOR,PORT
    parser=argparse.ArgumentParser()
    parser.add_argument('--port',type=int,default=8765)
    args=parser.parse_args()
    PORT=args.port
    if ROOT.drive.upper()!='D:':
        raise RuntimeError('请将项目保留在 D 盘。')
    # Hold a process-wide lock before starting any worker, including on other ports.
    # A second launcher must never create a second renderer for the same queue.
    import msvcrt
    DATA.mkdir(parents=True,exist_ok=True)
    lock_file=(DATA/'server.lock').open('a+b')
    if os.fstat(lock_file.fileno()).st_size==0:
        lock_file.write(b'0')
        lock_file.flush()
    lock_file.seek(0)
    try:
        msvcrt.locking(lock_file.fileno(),msvcrt.LK_NBLCK,1)
    except OSError:
        lock_file.close()
        print('工作台已经运行，请打开原来的本机页面。',file=sys.stderr)
        return 1
    server=ThreadingHTTPServer(('127.0.0.1',PORT),Handler)
    EDITOR=Editor()
    print(f'Local: http://127.0.0.1:{PORT}',flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        EDITOR.stopping=True
        server.server_close()
        lock_file.close()


if __name__=='__main__':
    raise SystemExit(main())
