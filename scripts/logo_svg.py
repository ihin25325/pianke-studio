"""Offline bounded SVG rasterization helper; input sanitized by brand_logos."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools/logo_packages'))
import resvg_py
from xml.etree import ElementTree as ET

if __name__=='__main__':
    raw=Path(sys.argv[1]).read_text(encoding='utf-8-sig');root=ET.fromstring(raw)
    view=root.attrib.get('viewBox','').replace(',',' ').split()
    try:
        w,h=(float(view[2]),float(view[3])) if len(view)==4 else (float(root.attrib.get('width','100').removesuffix('px')),float(root.attrib.get('height','100').removesuffix('px')))
        if not 0<w<=100000 or not 0<h<=100000:raise ValueError()
    except (ValueError,IndexError):raise ValueError('Invalid SVG bounds')
    scale=min(1600/w,1000/h)
    png=resvg_py.svg_to_bytes(svg_string=raw,width=max(1,round(w*scale)),height=max(1,round(h*scale)),skip_system_fonts=True)
    Path(sys.argv[2]).write_bytes(png)
