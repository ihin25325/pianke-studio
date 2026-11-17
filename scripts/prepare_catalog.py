"""Read the workbook without modifying it; save compact reusable product metadata on D:."""
import argparse
import json
import re
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NS = {'m': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--workbook', type=Path, default=ROOT / '副本需要添加首图视频的商品 11-6.xlsx')
    args = parser.parse_args()
    with zipfile.ZipFile(args.workbook) as z:
        strings = []
        if 'xl/sharedStrings.xml' in z.namelist():
            strings = [''.join(n.itertext()) for n in ET.fromstring(z.read('xl/sharedStrings.xml')).findall('m:si', NS)]
        sheet = ET.fromstring(z.read('xl/worksheets/sheet1.xml'))
        rows = []
        for row in sheet.findall('m:sheetData/m:row', NS):
            cells = {}
            for cell in row.findall('m:c', NS):
                key = re.sub(r'\d+', '', cell.attrib['r'])
                value = cell.findtext('m:v', default='', namespaces=NS)
                if cell.attrib.get('t') == 's':
                    value = strings[int(value)] if value else ''
                elif cell.attrib.get('t') == 'inlineStr':
                    value = ''.join(cell.find('m:is', NS).itertext())
                cells[key] = value
            if cells.get('A') == '宝贝id' or not cells.get('A'):
                continue
            rows.append({'product_id': cells['A'], 'title': cells.get('B',''), 'taobao_url':cells.get('D',''), 'existing_video_url':cells.get('F',''), 'excel_row':int(row.attrib['r']), 'status':'needs_source' if not cells.get('F') else 'has_existing_video'})
    directory = ROOT / 'data'
    directory.mkdir(exist_ok=True)
    for filename, data in [('products.json', rows), ('missing_video_products.json', [r for r in rows if not r['existing_video_url']])]:
        (directory / filename).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'total':len(rows), 'missing_video':sum(not r['existing_video_url'] for r in rows)}, ensure_ascii=False))

if __name__ == '__main__':
    main()
