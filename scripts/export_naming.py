"""Human-readable, Unicode and Windows-safe export aliases; never rename old files."""
import re
import unicodedata


def safe_name(value,limit=84):
    text=unicodedata.normalize('NFC',str(value or ''))
    text=re.sub(r'[<>:"/\\|?*\x00-\x1f\x7f]', '_', text)
    text=re.sub(r'\s+',' ',text).strip(' ._')[:limit].rstrip(' .')
    if not text: text='商品视频'
    if re.match(r'^(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)',text,re.I):text='_'+text
    return text


def product_name(task):
    # Use product identity, not marketing copy or an edited on-screen slogan.
    from editor_server import concise_title,product_category,identity
    brand=str(task.get('brand') or '').strip()
    model=str(task.get('model') or '').strip()
    original=task.get('original_title') or task.get('title') or ''
    search=task.get('search_name') or ''
    search_brand,search_models=identity(search) if search else ('',[])
    if len(search_models)==1 and (not brand or brand.casefold()==search_brand.casefold()):
        brand=search_brand or brand;model=search_models[0]
        model_title=search
    else:model_title=original
    title=concise_title(model_title,brand,[model] if model else task.get('models',[]))
    if brand and title.lower().startswith(brand.lower()):title=title[len(brand):].strip(' _-')
    model_label=title or model
    category=product_category(original,brand,model)
    if re.search(r'(?:吉他|贝斯|综合).{0,12}效果器',original):category='综合效果器'
    if category=='商品展示':
        rules=[('音视频播放器',r'音视频播放器|多轨.{0,6}播放器'),('电子鼓机',r'鼓机|节奏机'),
               ('乐器无线收发器',r'(?:吉他|贝斯|乐器).{0,12}无线|无线.{0,12}(?:吉他|贝斯|乐器)'),
               ('无线音频收发器',r'无线.{0,12}(?:发射|接收|收发)|(?:发射|接收).{0,8}无线'),
               ('音频测试仪',r'音频测试仪|音频检测仪'),('无线监听系统',r'无线.{0,8}(?:监听|耳返)'),
               ('音频转接器',r'音频.{0,8}(?:转接|转换)')]
        category=next((name for name,pattern in rules if re.search(pattern,original,re.I)),category)
    pieces=[brand,model_label,category]
    unique=[]
    for part in pieces:
        if part and part.casefold() not in {p.casefold() for p in unique}:unique.append(part)
    return safe_name('_'.join(unique) or '商品视频_'+str(task.get('product_id') or task.get('id') or ''))


def unique_filename(stem,used):
    name=safe_name(stem)+'.mp4';n=2
    while name.casefold() in used:
        name=safe_name(stem)+f' ({n}).mp4';n+=1
    used.add(name.casefold())
    return name
