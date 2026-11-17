"""Bounded, explainable YouTube search; no LLM, cookies, or paid API."""
import math
import re
import unicodedata

VERSION=2
NAMED_MODELS={
 'Behringer':[(r'\bPHARA[ -]?O\s+MINI\b','PHARAOMINI')],
 'Waldorf':[(r'\bBlofeld\s+Desktop\b','BLOFELDDESKTOP')],
 'Stylophone':[(r'\bDavid\s+Bowie\b','DAVIDBOWIE'),(r'\bBEAT\b','BEAT')],
 'Sonicake':[(r'\bMatribox\s*(?:II|2)\s*Pro\b','MATRIBOXIIPRO')],
 'M-Live':[(r'\bB[ .-]*BEAT\s+PRO\s*16\b','BBEATPRO16'),(r'\bB[ .-]*BEAT\s+X\b','BBEATX'),(r'\bGO[ .-]*8\b','GO8')],
 'CME':[(r'\bXkey\s+Air\s*25\b','XKEYAIR25'),(r'\bXkey\s+Air\s*37\b','XKEYAIR37')],
 'Midiplus':[(r'\bsoundbox\b','SOUNDBOX')],
 'SOYUZ':[(r'\bLauncher\s+Deluxe\s+Lakeside\b','LAUNCHERDELUXELAKESIDE')],
 'ESI':[(r'\bNEVA\s+UNO\b','NEVAUNO'),(r'\bNEVA\s+DUO\b','NEVADUO')],
}
DISPLAY={'PHARAOMINI':'PHARA-O MINI','BLOFELDDESKTOP':'Blofeld Desktop','DAVIDBOWIE':'David Bowie',
 'MATRIBOXIIPRO':'Matribox II Pro','BBEATPRO16':'B.Beat Pro 16','BBEATX':'B.Beat X','GO8':'GO.8',
 'XKEYAIR25':'Xkey Air 25','XKEYAIR37':'Xkey Air 37','LAUNCHERDELUXELAKESIDE':'Launcher Deluxe Lakeside',
 'NEVAUNO':'NEVA UNO','NEVADUO':'NEVA DUO'}


def clean(value):
    return re.sub(r'\s+',' ',unicodedata.normalize('NFKC',str(value or '')).replace('–','-').replace('—','-')).strip()


def plan(task,identify):
    name=clean(task.get('search_name') or task.get('original_title') or task.get('title'))
    brand,models=identify(name)
    english=' '.join(re.findall(r'[A-Za-z][A-Za-z0-9.+/_-]*|\d+',name))
    queries=[]
    if models and brand:
        for model in models[:2]:
            queries.append(f'{brand} {DISPLAY.get(model,model)}')
        if len(models)==1:
            base=queries[0]
            model=models[0]
            if model not in DISPLAY and re.match(r'^[A-Z]+\d',model):
                hyphen=re.sub(r'(?<=[A-Z])(?=\d)','-',model,count=1)
                spaced=re.sub(r'(?<=[A-Z])(?=\d)',' ',model,count=1)
                queries.extend([f'{brand} {hyphen} demo',f'{brand} {spaced}'])
            else:
                queries.extend([f'{base} demo overview',f'{brand} "{DISPLAY.get(model,model)}"'])
        else:
            queries.append((brand+' '+english)[:140])
    else:
        base=(english if len(english)>len(brand)+2 else name)[:120]
        queries=[base,base+' demo',base+' 介绍']
    return {'name':name,'brand':brand,'models':models,'queries':list(dict.fromkeys(clean(q) for q in queries if clean(q)))[:3]}


def evaluate(candidate,target,identify,brands):
    title=clean(candidate.get('title'))
    channel=clean(candidate.get('channel'))
    brand=target['brand'];models=target['models']
    found=identify(title)[1]
    hits=[m for m in models if m in found]
    result={'score':0,'auto_select':False,'match_reason':'型号未明确匹配，请人工确认'}
    seconds=candidate.get('duration')
    if not isinstance(seconds,(int,float)) or isinstance(seconds,bool) or not math.isfinite(seconds): seconds=0
    if candidate.get('is_live') or candidate.get('availability') in {'private','premium_only','subscriber_only','needs_auth','unavailable'}:
        return dict(result,match_reason='直播、不可用或需要权限的素材，不自动采用')
    if seconds and not 5<=seconds<=900:
        return dict(result,match_reason='时长不足 5 秒或超过 15 分钟，不自动采用')
    if re.search(r'\bvs\b|versus|fake|counterfeit|compar(?:e|ing|ison)|对比|比较|横评|假货',title,re.I):
        return dict(result,match_reason='对比或真假鉴别内容，需人工确认')
    if not hits:
        return result
    if len(models)!=1:
        return dict(result,score=60,match_reason='商品包含多个型号，找到部分款式；请选择实际要制作的型号')
    if len(found)!=1:
        return dict(result,score=55,match_reason='视频涉及多个型号，需人工核对')
    aliases=brands.get(brand,[brand])
    def has_brand(text):
        return any(alias.lower() in text.lower() if re.search(r'[\u3400-\u9fff]',alias) else
                   bool(re.search(r'(?<![A-Za-z])'+re.escape(alias)+r'(?![A-Za-z])',text,re.I)) for alias in aliases if alias)
    if not brand or not has_brand(title+' '+channel):
        return dict(result,score=55,match_reason='型号相符，但缺少对应品牌依据，请人工确认')
    if not seconds:
        return dict(result,score=75,match_reason='品牌、型号相符，但时长未知，请人工确认')
    # Longer exact-model demonstrations remain eligible; being over 3 minutes
    # no longer silently turns a correct 15-minute-or-less result into failure.
    score=80
    channel_match=has_brand(channel)
    if channel_match: score+=20
    if 10<=seconds<=180: score+=10
    if re.search(r'\bofficial\b|product|overview|introduction|介绍|演示',title,re.I): score+=5
    return {'score':score,'auto_select':True,'match_reason':'品牌及唯一型号相符'+('；频道名也匹配品牌（不代表已验证官方身份）' if channel_match else '；来源需核对授权')}
