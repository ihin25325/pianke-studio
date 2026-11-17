"""Validated, persistent typography preferences shared by preview and export."""
from pathlib import Path
import copy
import json
import math
import re
from premium_fonts import PREMIUM_FONT_CHOICES

ROOT=Path(__file__).resolve().parents[1]
STYLE_FILE=ROOT/'configs'/'typography.json'
FONT_CHOICES=[
    {'value':'Oswald','label':'Oswald · 第5种窄体英文'},
    {'value':'Source Han Sans CN','label':'思源黑体 · 清晰利落'},
    {'value':'Source Han Serif CN','label':'思源宋体 · 经典精致'},
    {'value':'LXGW WenKai','label':'霞鹜文楷 · 温和自然'},
    {'value':'Montserrat','label':'Montserrat · 几何英文'},
] + PREMIUM_FONT_CHOICES
DEFAULT_STYLE={}
for section,face,bold,color in [
    ('brand','Oswald',True,'#FFFFFF'),
    ('title','Oswald',True,'#FFFFFF'),
    ('description','Source Han Sans CN',False,'#E6E6E6')]:
    DEFAULT_STYLE.update({f'{section}_font':face,f'{section}_scale':1.0,
                          f'{section}_bold':bold,f'{section}_color':color,f'{section}_offset':0})
DEFAULT_STYLE.update(subtitle_align='center',subtitle_y=994)


def normalize_style(value,base=None):
    if not isinstance(value,dict):
        raise ValueError('字体设置格式不正确。')
    if set(value)-set(DEFAULT_STYLE):
        raise ValueError('字体设置包含不支持的字段。')
    style=copy.deepcopy(DEFAULT_STYLE)
    if base is not None:
        style.update(base)
    style.update(value)
    for section in ('brand','title','description'):
        if style[f'{section}_font'] not in {item['value'] for item in FONT_CHOICES}:
            raise ValueError('请选择已下载的字体。')
        for suffix,minimum,maximum in [('scale',.5,1.6),('offset',-100,100)]:
            val=style[f'{section}_{suffix}']
            if isinstance(val,bool) or not isinstance(val,(int,float)) or not math.isfinite(val) or not minimum<=val<=maximum:
                raise ValueError('文字大小需为 50%–160%，上下位置需为 -100–100。')
        if not isinstance(style[f'{section}_bold'],bool):
            raise ValueError('加粗开关格式不正确。')
        color=style[f'{section}_color']
        if not isinstance(color,str) or not re.fullmatch(r'#[0-9a-fA-F]{6}',color):
            raise ValueError('请选择有效的文字颜色。')
        style[f'{section}_color']=color.upper()
    if style['subtitle_align'] not in ('left','center','right'):
        raise ValueError('请选择字幕左对齐、居中或右对齐。')
    y=style['subtitle_y']
    if isinstance(y,bool) or not isinstance(y,(int,float)) or not math.isfinite(y) or not 180<=y<=1380:
        raise ValueError('字幕上下位置需在 180–1380 像素之间。')
    style['subtitle_y']=round(y)
    return style


def load_style():
    return normalize_style(json.loads(STYLE_FILE.read_text(encoding='utf-8'))) if STYLE_FILE.is_file() else copy.deepcopy(DEFAULT_STYLE)


def normalize_text(value):
    if not isinstance(value,dict) or set(value)-{'brand_title','product_title','description'}:
        raise ValueError('请填写品牌、标题与商品类别。')
    result={}
    for key,limit,required in [('brand_title',120,True),('product_title',240,True),('description',180,False)]:
        raw=value.get(key,'')
        if not isinstance(raw,str):
            raise ValueError('文案必须是文字。')
        text=re.sub(r'\s+',' ',raw).strip()
        if len(text)>limit or (required and not text):
            raise ValueError('请填写简短的品牌和标题；商品类别最多 180 个字符。')
        result[key]=text
    return result
