"""Render the actual downloaded typefaces; this is a specimen, not generated artwork."""
from pathlib import Path
import json
from PIL import Image, ImageDraw, ImageFont

ROOT=Path(__file__).resolve().parents[1]
FONTS=ROOT/'assets'/'fonts'
OUT=ROOT/'outputs'
BODY=FONTS/'SourceHanSans'/'SourceHanSansCN-Regular.otf'
BOLD=FONTS/'SourceHanSans'/'SourceHanSansCN-Bold.otf'


def font(path,size,weight=None):
    result=ImageFont.truetype(str(path),size)
    if weight is not None:
        result.set_variation_by_axes([weight])
    return result


def center(draw,text,y,face,color):
    box=draw.textbbox((0,0),text,font=face)
    draw.text(((1280-(box[2]-box[0]))/2,y-box[1]),text,font=face,fill=color)


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    canvas=Image.new('RGB',(1280,1670),'#f4f2ed')
    draw=ImageDraw.Draw(canvas)
    ink='#26392e'
    draw.text((70,46),'为商品视频挑的 5 款字体',font=font(BOLD,46),fill=ink)
    draw.text((72,112),'同一文案，实际字体渲染；当前视频默认采用第 5 种搭配。',font=font(BODY,24),fill='#6c756d')
    samples=[
        ('01  思源黑体','清晰利落 · 商品视频首选',BOLD,None,BODY),
        ('02  思源宋体','经典精致 · 偏杂志感',FONTS/'SourceHanSerif'/'SourceHanSerifCN-SemiBold.otf',None,FONTS/'SourceHanSerif'/'SourceHanSerifCN-SemiBold.otf'),
        ('03  霞鹜文楷','温和自然 · 书写感',FONTS/'LXGWWenKai'/'LXGWWenKai-Medium.ttf',None,FONTS/'LXGWWenKai'/'LXGWWenKai-Medium.ttf'),
        ('04  Montserrat','英文品牌与型号 · 中文搭配思源黑体',FONTS/'Montserrat'/'Montserrat-Variable.ttf',700,BODY),
        ('05  Oswald','窄长醒目 · 中文搭配思源黑体',FONTS/'Oswald'/'Oswald-Variable.ttf',600,BODY),
    ]
    for i,(name,note,title_path,weight,description_path) in enumerate(samples):
        y=178+i*289
        draw.text((72,y),name,font=font(BOLD,28),fill=ink)
        draw.text((445,y+4),note,font=font(BODY,22),fill='#6c756d')
        draw.rounded_rectangle((70,y+49,1210,y+264),radius=18,fill='#111512')
        title=font(title_path,84,weight)
        center(draw,'Neumann U87 Ai',y+80,title,'#ffffff')
        center(draw,'录音电容麦克风',y+183,font(description_path,44),'#dce4dc')
    canvas.save(OUT/'字体对比.png')
    print(OUT/'字体对比.png')
    manifest=json.loads((FONTS/'font_manifest.json').read_text(encoding='utf-8'))
    total=sum(f['bytes'] for family in manifest['families'] for f in family['fonts'])
    count=sum(len(family['fonts']) for family in manifest['families'])
    lines=['# 商品视频字体包','',f"已下载 {len(manifest['families'])} 款字体，共 {count} 个字体文件，约 {total/1024/1024:.1f} MiB。",'',
           '字体保存在 D 盘本目录，没有安装到 Windows 系统目录。当前视频默认采用第 5 种搭配：Oswald 英文＋思源黑体中文。',
           '实际效果见 D:\\剪辑视频自动化\\outputs\\字体对比.png。', '',
           '| 字体 | 内部字体名称 | 建议用途 |', '| --- | --- | --- |']
    for family in manifest['families']:
        lines.append(f"| {family['name']} | {family['family']} | {family['use']} |")
    lines += ['', '当前搭配：Oswald SemiBold 显示英文品牌/型号，思源黑体显示中文商品类别。', '',
              'Montserrat 与 Oswald 是可变字体，可选择不同粗细；对比图分别使用 700 和 600 字重。两者不含中文字形。', '',
              '各字体采用 SIL Open Font License 1.1，允许用于商业视频。每个子目录保留 OFL.txt；再分发字体文件时一并保留授权和版权信息。来源链接、下载时间、文件大小与 SHA-256 见 font_manifest.json。', '',
              '仅下载原始字体文件，未修改字形。视频从 D 盘 assets/fonts/render 私有目录加载，不必复制到 C 盘。']
    (FONTS/'使用说明.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')


if __name__=='__main__':
    main()
