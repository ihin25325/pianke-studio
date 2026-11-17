"""Download a small OFL font collection from the authors' official repositories."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import hashlib
import json
from pathlib import Path
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / 'assets' / 'fonts'
PACK = [
    {'name':'思源黑体', 'family':'Source Han Sans CN', 'folder':'SourceHanSans',
     'homepage':'https://github.com/adobe-fonts/source-han-sans',
     'license_url':'https://raw.githubusercontent.com/adobe-fonts/source-han-sans/release/LICENSE.txt',
     'fonts':[
         ('SourceHanSansCN-Bold.otf','https://raw.githubusercontent.com/adobe-fonts/source-han-sans/release/SubsetOTF/CN/SourceHanSansCN-Bold.otf'),
         ('SourceHanSansCN-Regular.otf','https://raw.githubusercontent.com/adobe-fonts/source-han-sans/release/SubsetOTF/CN/SourceHanSansCN-Regular.otf')],
     'use':'清晰、利落；推荐用于商品标题和类别。'},
    {'name':'思源宋体', 'family':'Source Han Serif CN', 'folder':'SourceHanSerif',
     'homepage':'https://github.com/adobe-fonts/source-han-serif',
     'license_url':'https://raw.githubusercontent.com/adobe-fonts/source-han-serif/release/LICENSE.txt',
     'fonts':[('SourceHanSerifCN-SemiBold.otf','https://raw.githubusercontent.com/adobe-fonts/source-han-serif/release/SubsetOTF/CN/SourceHanSerifCN-SemiBold.otf'),
              ('SourceHanSerifCN-Regular.otf','https://raw.githubusercontent.com/adobe-fonts/source-han-serif/release/SubsetOTF/CN/SourceHanSerifCN-Regular.otf')],
     'use':'偏经典、精致，适合乐器或有质感的商品标题。'},
    {'name':'霞鹜文楷', 'family':'LXGW WenKai', 'folder':'LXGWWenKai',
     'homepage':'https://github.com/lxgw/LxgwWenKai',
     'license_url':'https://raw.githubusercontent.com/lxgw/LxgwWenKai/main/OFL.txt',
     'fonts':[('LXGWWenKai-Medium.ttf','https://github.com/lxgw/LxgwWenKai/releases/download/v1.530/LXGWWenKai-Medium.ttf'),
              ('LXGWWenKai-Regular.ttf','https://github.com/lxgw/LxgwWenKai/releases/download/v1.530/LXGWWenKai-Regular.ttf')],
     'use':'柔和、有书写感，适合较轻松的商品风格。'},
    {'name':'Montserrat', 'family':'Montserrat', 'folder':'Montserrat',
     'homepage':'https://github.com/google/fonts/tree/main/ofl/montserrat',
     'license_url':'https://raw.githubusercontent.com/google/fonts/main/ofl/montserrat/OFL.txt',
     'fonts':[('Montserrat-Variable.ttf','https://raw.githubusercontent.com/google/fonts/main/ofl/montserrat/Montserrat%5Bwght%5D.ttf'),
              ('Montserrat-Regular.ttf','https://raw.githubusercontent.com/JulietaUla/Montserrat/master/fonts/ttf/Montserrat-Regular.ttf'),
              ('Montserrat-Bold.ttf','https://raw.githubusercontent.com/JulietaUla/Montserrat/master/fonts/ttf/Montserrat-Bold.ttf')],
     'use':'几何感鲜明，推荐用于英文品牌与型号；不含中文字形。'},
    {'name':'Oswald', 'family':'Oswald', 'folder':'Oswald',
     'homepage':'https://github.com/google/fonts/tree/main/ofl/oswald',
     'license_url':'https://raw.githubusercontent.com/google/fonts/main/ofl/oswald/OFL.txt',
     'fonts':[
         ('Oswald-SemiBold.ttf','https://raw.githubusercontent.com/googlefonts/OswaldFont/main/fonts/ttf/Oswald-SemiBold.ttf'),
         ('Oswald-Regular.ttf','https://raw.githubusercontent.com/googlefonts/OswaldFont/main/fonts/ttf/Oswald-Regular.ttf'),
         ('Oswald-Variable.ttf','https://raw.githubusercontent.com/google/fonts/main/ofl/oswald/Oswald%5Bwght%5D.ttf')],
     'use':'窄长、醒目，适合英文大标题；不含中文字形。'},
]


def fetch(url, target, font=False):
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_file() and target.stat().st_size > (10000 if font else 1000):
        return target
    request=urllib.request.Request(url, headers={'User-Agent':'Local-Product-Video-Font-Pack/1.0'})
    partial=target.with_suffix(target.suffix+'.part')
    with urllib.request.urlopen(request, timeout=90) as response, partial.open('wb') as handle:
        while chunk:=response.read(1024*1024):
            handle.write(chunk)
    if font and partial.read_bytes()[:4] not in {b'OTTO',b'\x00\x01\x00\x00',b'ttcf'}:
        raise ValueError(f'Not an OpenType/TrueType font: {url}')
    if not font and b'SIL OPEN FONT LICENSE' not in partial.read_bytes():
        raise ValueError(f'Unexpected license text: {url}')
    partial.replace(target)
    return target


def download_family(item):
    folder=DEST/item['folder']
    license_path=fetch(item['license_url'],folder/'OFL.txt')
    files=[]
    for filename,url in item['fonts']:
        path=fetch(url,folder/filename,True)
        files.append({'file':str(path.relative_to(ROOT)), 'source_url':url,
                      'bytes':path.stat().st_size, 'sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
    return dict(item,fonts=files,license='SIL Open Font License 1.1',license_file=str(license_path.relative_to(ROOT)))


def main():
    DEST.mkdir(parents=True,exist_ok=True)
    successful=[]
    failures=[]
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures={pool.submit(download_family,item):item for item in PACK}
        for future in as_completed(futures):
            item=futures[future]
            try:
                result=future.result()
                successful.append(result)
                print(f"Downloaded: {item['name']}",flush=True)
            except Exception as error:
                failures.append({'name':item['name'],'error':str(error)})
                print(f"Failed: {item['name']}: {error}",flush=True)
    successful.sort(key=lambda item:next(i for i,p in enumerate(PACK) if p['name']==item['name']))
    manifest={'downloaded_at':datetime.now().astimezone().isoformat(),'families':successful,'failures':failures}
    (DEST/'font_manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    return bool(failures)


if __name__=='__main__':
    raise SystemExit(main())
