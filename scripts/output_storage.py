"""Local export-directory preferences; never relocate originals or old exports."""
from pathlib import Path
import json
import os
import re
import tempfile
import uuid

ROOT=Path(__file__).resolve().parents[1]
SETTINGS_FILE=ROOT/'configs'/'storage.json'
DEFAULT_DIR=ROOT/'outputs'


def normalize_directory(value):
    if not isinstance(value,str): raise ValueError('请填写本机文件夹的完整路径。')
    value=value.strip()
    if len(value)>=2 and value[0]==value[-1]=='"': value=value[1:-1]
    if not value or len(value)>220 or any(ord(c)<32 for c in value):
        raise ValueError('保存路径不能为空，且最多 220 个字符。')
    if value.startswith(('\\\\','//')) or re.search(r'[*?<>|"\x00]',value):
        raise ValueError('请选择本机磁盘文件夹，不支持网络共享、设备路径或特殊字符。')
    if os.name=='nt' and (not re.match(r'^[A-Za-z]:[\\/]',value) or ':' in value[2:]):
        raise ValueError('请从资源管理器复制完整文件夹路径；不能只填写盘符或相对路径。')
    path=Path(value)
    if not path.is_absolute() or any(part in {'.','..'} for part in path.parts):
        raise ValueError('请使用不含“..”的完整文件夹路径。')
    if os.name=='nt':
        for part in path.parts[1:]:
            if part.endswith((' ','.')) or re.fullmatch(r'(?i:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?',part):
                raise ValueError('路径包含 Windows 不支持的文件夹名称。')
    path=path.resolve()
    if path==Path(path.anchor) or path==ROOT.resolve():
        raise ValueError('请选择专门的成片子文件夹，不要使用磁盘根目录或项目根目录。')
    protected=[ROOT/name for name in ('scripts','web','configs','data','models','tools','assets','sources')]
    if os.name=='nt':
        protected.extend(Path(p) for p in (r'C:\Windows',r'C:\Program Files',r'C:\Program Files (x86)',r'C:\ProgramData'))
    if any(path==p.resolve() or p.resolve() in path.parents for p in protected):
        raise ValueError('不能把成片保存到程序、模型、原始素材或系统文件夹，请选择独立目录。')
    return path


def prepare_directory(value):
    path=normalize_directory(value)
    try:
        path.mkdir(parents=True,exist_ok=True)
        # Probe a unique file only; never touch any existing user file.
        descriptor,name=tempfile.mkstemp(prefix='.video_editor_write_check_',dir=path)
        try:
            with os.fdopen(descriptor,'wb') as handle:
                handle.write(b'write check');handle.flush();os.fsync(handle.fileno())
        finally:
            Path(name).unlink(missing_ok=True)
    except OSError as exc:
        raise ValueError('该目录不可写或磁盘不可用，请换一个有写入权限的文件夹。') from exc
    return path


def load_settings():
    if not SETTINGS_FILE.is_file(): return {'output_dir':str(DEFAULT_DIR.resolve())}
    value=json.loads(SETTINGS_FILE.read_text(encoding='utf-8'))
    return {'output_dir':str(normalize_directory(value.get('output_dir')))}


def save_settings(value):
    path=prepare_directory(value)
    data={'output_dir':str(path)}
    temporary=SETTINGS_FILE.with_name(SETTINGS_FILE.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        with temporary.open('x',encoding='utf-8') as handle:
            json.dump(data,handle,ensure_ascii=False,indent=2)
            handle.flush();os.fsync(handle.fileno())
        os.replace(temporary,SETTINGS_FILE)
    finally:
        temporary.unlink(missing_ok=True)
    return data
