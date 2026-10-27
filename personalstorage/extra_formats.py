"""Bounded read-only legacy Office and open-document parsers. Never run macros."""
import struct
import re
import zipfile
from html.parser import HTMLParser
from email import policy
from email.parser import BytesParser
from xml.etree import ElementTree as ET
from .common import SafetyError

FORMATS={'.doc','.ppt','.xls','.rtf','.html','.htm','.odt','.ods','.odp','.epub','.eml','.srt','.vtt','.tex','.log','.tsv'}

class TextHTML(HTMLParser):
    def __init__(self):super().__init__();self.ignore=0;self.text=[]
    def handle_starttag(self,tag,attrs):
        if tag in ('script','style'):self.ignore+=1
        if tag in ('p','div','br','tr','li','h1','h2'):self.text.append('\n')
        if tag in ('td','th'):self.text.append('\t')
    def handle_endtag(self,tag):
        if tag in ('script','style'):self.ignore=max(0,self.ignore-1)
    def handle_data(self,data):
        if not self.ignore:self.text.append(data)

def html(text):
    parser=TextHTML();parser.feed(text);return ''.join(parser.text)

def doc_text(word,table,limit):
    if len(word)<426 or struct.unpack_from('<H',word)[0]!=0xa5ec:raise SafetyError('不是支持的Word97–2003格式')
    flags=struct.unpack_from('<H',word,10)[0]
    if flags&0x8100:raise SafetyError('加密或混淆的DOC需要用户解密后再读取')
    start,size=struct.unpack_from('<II',word,418)
    if start+size>len(table):raise SafetyError('DOC片段表边界错误')
    clx=table[start:start+size];offset=0
    while offset<len(clx) and clx[offset]==1:
        if offset+3>len(clx):raise SafetyError('DOC片段描述截断')
        offset+=3+struct.unpack_from('<H',clx,offset+1)[0]
    if offset+5>len(clx) or clx[offset]!=2:raise SafetyError('未找到DOC文本片段表')
    length=struct.unpack_from('<I',clx,offset+1)[0];piece=clx[offset+5:offset+5+length]
    if len(piece)!=length or length<4 or (length-4)%12:raise SafetyError('DOC片段表无效')
    count=(length-4)//12
    if count>50000:raise SafetyError('DOC片段数量超预算')
    positions=struct.unpack_from('<'+'I'*(count+1),piece);texts=[];remaining=limit
    for i in range(count):
        n=min(positions[i+1]-positions[i],remaining)
        if n<0:raise SafetyError('DOC字符位置无效')
        fc=struct.unpack_from('<I',piece,4*(count+1)+8*i+2)[0];compressed=bool(fc&0x40000000);fc&=0x3fffffff
        if compressed:fc//=2
        size=n*(1 if compressed else 2)
        if fc+size>len(word):raise SafetyError('DOC文本越界')
        text=word[fc:fc+size].decode('cp1252' if compressed else 'utf-16le',errors='replace');texts.append(text);remaining-=len(text)
        if remaining<=0:break
    return ''.join(texts).replace('\r','\n').replace('\x07','\t')

def ppt_text(data,limit,depth=0):
    if depth>15:raise SafetyError('PPT记录嵌套超限')
    offset=0;texts=[];remaining=limit;records=0
    while offset+8<=len(data) and remaining>0:
        version,kind,size=struct.unpack_from('<HHI',data,offset);offset+=8;records+=1
        if records>100000 or offset+size>len(data):raise SafetyError('PPT记录边界或数量异常')
        payload=data[offset:offset+size];offset+=size
        if kind==4000:text=payload.decode('utf-16le',errors='replace')
        elif kind==4008:text=payload.decode('latin1')
        elif version&15==15:text=ppt_text(payload,remaining,depth+1)
        else:continue
        texts.append(text[:remaining]);remaining-=len(text)
    return '\n'.join(texts)

def read(path,limit=16000):
    suffix=path.suffix.lower();text='';warnings=[];tables=[]
    if suffix in ('.doc','.ppt'):
        import olefile
        with olefile.OleFileIO(path) as ole:
            if suffix=='.doc':
                word=ole.openstream('WordDocument').read(64*1024*1024);flags=struct.unpack_from('<H',word,10)[0]
                table=ole.openstream('1Table' if flags&0x200 else '0Table').read(64*1024*1024);text=doc_text(word,table,limit)
            else:text=ppt_text(ole.openstream('PowerPoint Document').read(64*1024*1024),limit)
        warnings.append('旧版Office只读提取，未读取宏、嵌入对象或全部版式；需核对文字完整性')
    elif suffix=='.xls':
        import xlrd
        book=xlrd.open_workbook(path,on_demand=True)
        try:
            chunks=[];budget=limit
            for sheet in book.sheets()[:8]:
                rows=[[str(sheet.cell_value(r,c)) for c in range(min(sheet.ncols,40))] for r in range(min(sheet.nrows,200))]
                part=sheet.name+'\n'+'\n'.join('\t'.join(row) for row in rows);chunks.append(part[:budget]);budget-=len(part)
                tables.append({'sheet':sheet.name,'rows':sheet.nrows,'columns':sheet.ncols})
                if budget<=0:break
            text='\n\n'.join(chunks)
        finally:book.release_resources()
    elif suffix in ('.odt','.ods','.odp','.epub'):
        with zipfile.ZipFile(path) as archive:
            names=['content.xml'] if suffix!='.epub' else [n for n in archive.namelist() if n.lower().endswith(('.html','.xhtml','.htm'))][:12]
            parts=[]
            for name in names:
                info=archive.getinfo(name)
                if info.file_size>2*1024*1024:continue
                raw=archive.read(name)
                if b'<!ENTITY' in raw:raise SafetyError('拒绝文档实体声明')
                if suffix=='.epub':parts.append(html(raw.decode('utf-8',errors='replace')))
                else:parts.append('\n'.join(ET.fromstring(raw).itertext()))
            text='\n'.join(parts)
    elif suffix=='.eml':
        with path.open('rb') as stream:raw=stream.read(2*1024*1024)
        message=BytesParser(policy=policy.default).parsebytes(raw);parts=[]
        for part in message.walk():
            if part.get_content_disposition()=='attachment':continue
            if part.get_content_type() in ('text/plain','text/html'):
                content=part.get_content();parts.append(html(content) if part.get_content_type()=='text/html' else content)
        text='\n'.join(parts)
    else:
        with path.open('rb') as stream:raw=stream.read(limit*8)
        text=raw.decode('utf-8-sig',errors='replace')
        if suffix in ('.html','.htm'):text=html(text)
        elif suffix=='.rtf':
            from striprtf.striprtf import rtf_to_text
            text=rtf_to_text(raw.decode('latin1'))
        elif suffix in ('.srt','.vtt'):text=re.sub(r'(?m)^\s*(?:\d+|WEBVTT|.*-->.*)\s*$','',text)
    return {'name':path.name,'kind':'file','text':text[:limit],'text_source':'native','complete':False,'warnings':warnings,'tables':tables}
