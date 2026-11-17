"""Original product wordmarks and real-source cover cards, no invented product art."""
from pathlib import Path
import math
import subprocess
from PIL import Image,ImageDraw,ImageFont,ImageFilter,ImageOps
from typography import normalize_style

ROOT=Path(__file__).resolve().parents[1]


def fit_font(path,text,width,size,minimum=28):
    while size>minimum:
        face=ImageFont.truetype(str(path),size)
        if face.getlength(text)<=width:return face
        size-=2
    return ImageFont.truetype(str(path),minimum)


def wrap(text,face,width):
    lines=[];line=''
    for char in text:
        if face.getlength(line+char)>width and line:lines.append(line);line=''
        line+=char
    if line:lines.append(line)
    return lines[:3]


def create(source,report,text,style,theme='editorial',brand_logo=None):
    import product_render as renderer
    folder=ROOT/'data'/'director_cache'/report['key'];folder.mkdir(parents=True,exist_ok=True)
    raw=folder/'cover_raw.jpg'
    r=subprocess.run([str(renderer.FFMPEG),'-hide_banner','-loglevel','error','-nostdin','-ss',str(report['cover_time']),
                      '-i',str(source),'-frames:v','1','-vf','scale=1600:-2','-q:v','2','-y',str(raw)],capture_output=True,timeout=45)
    if r.returncode or not raw.is_file():raise RuntimeError('封面画面提取失败，未生成虚构商品图。')
    prefs=normalize_style(style)
    cn=ROOT/'assets/fonts/SourceHanSans/SourceHanSansCN-Bold.otf'
    selected=renderer.PRIVATE_FONTS.get(prefs['title_font'].lower(),(cn,cn))
    title_path=selected[1 if prefs['title_bold'] else 0]
    title=str(text.get('product_title','商品展示')).strip()[:120]
    brand=str(text.get('brand_title','')).strip()[:70]
    category=str(text.get('description','')).strip()[:80]
    # Chinese glyph fallback is explicit for Latin-only display fonts.
    if any('\u3400'<=c<='\u9fff' for c in title) and prefs['title_font'] in {'Inter','Manrope','Space Grotesk','Bebas Neue','Outfit','Oswald','Montserrat'}:title_path=cn
    face=fit_font(title_path,title,920,132,48)
    wordmark=Image.new('RGBA',(1080,400),(0,0,0,0));wd=ImageDraw.Draw(wordmark)
    for i,line in enumerate(wrap(title,face,920)):
        wd.text((80,30+i*(face.size+12)),line,font=face,fill='white',stroke_width=0)
    wordmark.save(folder/'title.png')
    colors={'editorial':('#10141f','#ff625b'),'coral':('#f7f4ee','#ef5954'),'minimal':('#f5f7fc','#2860ee')}
    bg,accent=colors.get(theme,colors['editorial']);light=theme!='editorial'
    canvas=Image.new('RGB',(1080,1440),bg);draw=ImageDraw.Draw(canvas)
    draw.rounded_rectangle((64,62,240,108),radius=23,fill=accent)
    draw.text((84,69),'PRODUCT FILM',font=ImageFont.truetype(str(ROOT/'assets/fonts/Oswald/Oswald-Regular.ttf'),24),fill='white')
    if brand_logo:
        import brand_logos
        logo=brand_logos.asset_image({'brand_logo':brand_logo})
        brand_logos.place(canvas,logo,(64,127,360,78))
    else:draw.text((64,145),brand,font=fit_font(cn,brand,920,40,22),fill='#626979' if light else '#a6b0c4')
    # Keep the whole product in frame. Do not crop logos out of source imagery.
    with Image.open(raw) as image:
        image=image.convert('RGB');image.thumbnail((952,760))
        backdrop=ImageOps.fit(image,(952,760)).filter(ImageFilter.GaussianBlur(32))
        backdrop=Image.blend(backdrop,Image.new('RGB',backdrop.size,bg),.58)
        backdrop.paste(image,((952-image.width)//2,(760-image.height)//2))
        mask=Image.new('L',backdrop.size,0);ImageDraw.Draw(mask).rounded_rectangle((0,0,951,759),radius=28,fill=255)
        canvas.paste(backdrop,(64,224),mask)
    draw=ImageDraw.Draw(canvas)
    heading=fit_font(title_path,title,952,126,46);lines=wrap(title,heading,952)
    for i,line in enumerate(lines):draw.text((64,1022+i*(heading.size+8)),line,font=heading,fill='#171c29' if light else 'white')
    category_y=min(1335,1030+len(lines)*(heading.size+8)+24)
    draw.text((68,category_y),category,font=fit_font(cn,category,940,32,20),fill='#666d7a' if light else '#aeb9cb')
    draw.rounded_rectangle((64,1393,1016,1399),radius=3,fill=accent)
    canvas.save(folder/'cover.jpg',quality=94,subsampling=0)
    return {'cover_file':str(folder/'cover.jpg'),'title_asset_file':str(folder/'title.png'),
            'cover_time':report['cover_time'],'cover_style':theme,'wordmark_kind':'original_typography'}
