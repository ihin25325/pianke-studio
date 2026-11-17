"""Bounded, read-only XLSX/CSV imports. Never execute formulas, links, or macros."""
from __future__ import annotations
import csv
from decimal import Decimal,InvalidOperation
import io
from pathlib import PurePosixPath
import re
import xml.etree.ElementTree as ET
import zipfile

NS={'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main','r':'http://schemas.openxmlformats.org/package/2006/relationships'}
ALIASES={
    'id':['宝贝id','商品id','商品编号','宝贝编号','itemid','productid'],
    'title':['宝贝标题','商品标题','商品名称','宝贝名称','产品名称','名称','title'],
    'product_url':['宝贝链接','商品链接','淘宝链接','商品地址'],
    'existing_video':['宝贝视频链接','商品视频链接','已有视频链接','已有视频','视频链接'],
    'source_url':['youtube链接','youtube地址','素材链接','源视频链接','youtube'],
    'need':['需要制作','是否制作','是否需要视频','需要视频','制作状态','视频状态','待办状态'],
}


def norm(value): return re.sub(r'[\s_\-（）()：:]','','' if value is None else str(value)).lower()


def xml(data):
    if b'<!DOCTYPE' in data.upper() or b'<!ENTITY' in data.upper(): raise ValueError('不支持包含外部实体的表格。')
    return ET.fromstring(data)


def column(ref):
    value=0
    for char in re.match(r'[A-Z]+',ref).group(): value=value*26+ord(char)-64
    if value>100: return None
    return value-1


def read_table(raw,filename,sheet=''):
    if len(raw)>8*1024*1024: raise ValueError('表格最大 8MB，请拆分后导入。')
    suffix=PurePosixPath(filename).suffix.lower()
    if suffix in {'.csv','.tsv'}:
        text=None
        for encoding in ('utf-8-sig','gb18030'):
            try: text=raw.decode(encoding);break
            except UnicodeDecodeError: pass
        if text is None: raise ValueError('无法读取表格编码，请另存为 UTF-8 CSV。')
        dialect='excel-tab' if suffix=='.tsv' else 'excel'
        rows=[]
        for index,row in enumerate(csv.reader(io.StringIO(text),dialect=dialect)):
            if index>=20020: raise ValueError('每张表最多 20000 行商品，请分表导入。')
            if len(row)>100: raise ValueError('最多支持 100 列，请移除无关列。')
            rows.append(row)
        return {'sheets':['商品'],'sheet':'商品','rows':rows}
    if suffix!='.xlsx': raise ValueError('请使用 .xlsx、.csv 或 .tsv；旧版 .xls 请先另存为 .xlsx。')
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            entries=archive.infolist()
            if len(entries)>5000 or sum(item.file_size for item in entries)>80*1024*1024: raise ValueError('解压后的表格过大，请移除图片或拆分。')
            if any(item.flag_bits&1 for item in entries): raise ValueError('请先取消 Excel 文件密码。')
            workbook=xml(archive.read('xl/workbook.xml'))
            rels=xml(archive.read('xl/_rels/workbook.xml.rels'))
            targets={item.attrib['Id']:item.attrib.get('Target','') for item in rels if item.attrib.get('TargetMode')!='External'}
            sheets=[]
            for item in workbook.find('s:sheets',NS):
                rid=item.attrib.get('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id')
                target=targets.get(rid,'').replace('\\','/')
                target=target.lstrip('/') if target.startswith('/') else 'xl/'+target
                if not re.fullmatch(r'xl/worksheets/[\w.-]+\.xml',target): continue
                sheets.append((item.attrib.get('name','Sheet'),target))
            if not sheets: raise ValueError('Excel 中没有可读取的工作表。')
            chosen=next((item for item in sheets if item[0]==sheet),None) if sheet else sheets[0]
            if chosen is None: raise ValueError('工作表不存在，请重新选择。')
            shared=[]
            if 'xl/sharedStrings.xml' in archive.namelist():
                for element in xml(archive.read('xl/sharedStrings.xml')).findall('s:si',NS):
                    shared.append(''.join(element.itertext()))
            rows=[];row_numbers=[];hidden_rows=[]
            for row in xml(archive.read(chosen[1])).findall('s:sheetData/s:row',NS):
                if len(rows)>=20020: raise ValueError('每张表最多 20000 行商品，请分表导入。')
                values=[]
                for cell in row.findall('s:c',NS):
                    ref=cell.attrib.get('r','')
                    if not re.match(r'^[A-Z]+\d+$',ref): continue
                    index=column(ref)
                    if index is None: continue
                    while len(values)<=index: values.append('')
                    value=cell.find('s:v',NS);kind=cell.attrib.get('t')
                    text=value.text or '' if value is not None else ''
                    if kind=='s':
                        try: text=shared[int(text)]
                        except (IndexError,ValueError): text=''
                    elif kind=='inlineStr': text=''.join(node.text or '' for node in cell.findall('s:is//s:t',NS))
                    elif kind is None and text:
                        try:
                            number=Decimal(text)
                            if number.is_finite() and -25<=number.adjusted()<=25 and number==number.to_integral_value(): text=str(int(number))
                        except (InvalidOperation,ValueError,OverflowError): pass
                    values[index]=str(text)[:4000]
                rows.append(values)
                number=int(row.attrib.get('r') or len(rows));row_numbers.append(number)
                if row.attrib.get('hidden','').lower() in {'1','true'}: hidden_rows.append(number)
            return {'sheets':[name for name,_ in sheets],'sheet':chosen[0],'rows':rows,
                    'row_numbers':row_numbers,'hidden_rows':hidden_rows}
    except (zipfile.BadZipFile,KeyError,ET.ParseError,RuntimeError) as error:
        raise ValueError('表格损坏、加密或格式不受支持，请用 Excel 另存为 .xlsx。') from error


def prepare(table,mapping=None,include_hidden=False):
    if not isinstance(include_hidden,bool): raise ValueError('包含隐藏行需为勾选状态。')
    rows=table['rows']
    if not rows: raise ValueError('表格是空的。')
    def score(row): return sum(any(norm(cell) in aliases for cell in row) for aliases in ALIASES.values())
    header=max(range(min(20,len(rows))),key=lambda index:score(rows[index]))
    if score(rows[header])<1: raise ValueError('没有识别到表头，请至少包含“商品名称”或“宝贝标题”列。')
    headers=[str(value or f'未命名列 {index+1}') for index,value in enumerate(rows[header])]
    guessed={key:next((index for index,value in enumerate(headers) if norm(value) in aliases),-1) for key,aliases in ALIASES.items()}
    selected=guessed if mapping is None else {key:mapping.get(key,-1) for key in ALIASES}
    for value in selected.values():
        if not isinstance(value,int) or not -1<=value<len(headers): raise ValueError('列匹配无效，请重新选择。')
    if selected['title']<0 and selected['id']<0 and selected['product_url']<0: raise ValueError('请匹配商品名称、ID 或商品链接列。')
    records=[];source_rows=0;hidden_count=0
    row_numbers=table.get('row_numbers',list(range(1,len(rows)+1)))
    hidden=set(table.get('hidden_rows',[]))
    for index,row in enumerate(rows[header+1:],header+1):
        if not any(str(value).strip() for value in row): continue
        source_rows+=1;number=row_numbers[index]
        if number in hidden:
            hidden_count+=1
            if not include_hidden: continue
        records.append(dict(row=number,**{key:str(row[col]).strip() if 0<=col<len(row) else '' for key,col in selected.items()}))
    if len(records)>20000: raise ValueError('每次最多导入 20000 行商品。')
    return {'headers':headers,'mapping':selected,'guessed':guessed,'records':records,'header_row':row_numbers[header],
            'sheet':table['sheet'],'sheets':table['sheets'],'source_rows':source_rows,'hidden_rows':hidden_count,
            'hidden_excluded':0 if include_hidden else hidden_count,'include_hidden':include_hidden}
