"""Editable, literal keyword rules. No model, network, regex, or media operations."""
from __future__ import annotations
import copy
import re

DEFAULT_RULES={'enabled':True,'groups':[
    {'label':'收纳与保护配件','keywords':['收纳包','收纳盒','手提包','手提箱','航空箱','保护盒','保护套','防尘罩','防护罩']},
    {'label':'线材配件','keywords':['音频线','数据线','USB线','替换线','耳机线','麦克风线','话筒线','卡农线','莲花线','连接线','延长线','转接线']},
    {'label':'安装与替换配件','keywords':['改造底座','底盖','防震架','防喷罩','防喷网','麦克风支架','话筒支架','麦架','固定夹','替换耳垫']},
], 'exceptions':['无线接收器','无线发射器','无线发射接收器','无线收发器','音频测试仪','音频检测器','耳机放大器','外置声卡','数模转换器','综合效果器']}

def folded(value): return re.sub(r'\s+','',str(value).casefold())

def keywords(value):
    if not isinstance(value,list) or len(value)>80: raise ValueError('每组关键词或例外词最多 80 个。')
    result=[];seen=set()
    for word in value:
        if not isinstance(word,str): raise ValueError('关键词必须是文字。')
        word=word.strip()
        if not 2<=len(word)<=80: raise ValueError('每个关键词需为 2–80 个字符，请勿仅填写“线”“盒”等单字。')
        key=folded(word)
        if key not in seen: result.append(word);seen.add(key)
    return result

def normalize(value):
    if not isinstance(value,dict): raise ValueError('免检索规则格式不正确。')
    enabled=value.get('enabled',True)
    if not isinstance(enabled,bool): raise ValueError('免检索开关需为勾选状态。')
    groups=value.get('groups',[])
    if not isinstance(groups,list) or len(groups)>30: raise ValueError('最多支持 30 组配件规则。')
    result=[]
    for group in groups:
        if not isinstance(group,dict): raise ValueError('配件规则格式不正确。')
        label=group.get('label','')
        if not isinstance(label,str) or not 1<=len(label.strip())<=30: raise ValueError('分类名称需为 1–30 个字符。')
        words=keywords(group.get('keywords',[]))
        if not words: raise ValueError('每组配件规则至少需要一个关键词。')
        result.append({'label':label.strip(),'keywords':words})
    return {'enabled':enabled,'groups':result,'exceptions':keywords(value.get('exceptions',[]))}

def defaults(): return copy.deepcopy(DEFAULT_RULES)

def match(title,rules):
    if not rules['enabled']: return None
    text=folded(title)
    if any(folded(word) in text for word in rules['exceptions']): return None
    for group in rules['groups']:
        for word in group['keywords']:
            if folded(word) in text: return {'label':group['label'],'keyword':word,'source':'rule'}
    return None
