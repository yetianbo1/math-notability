"""File preparation independent of Qt; extraction is streamed and cancellable."""
from dataclasses import dataclass
from pathlib import Path
import plistlib
import tempfile
import json
import re
from bisect import bisect_right
from zipfile import ZipFile
from lxml import etree
import formats.ne_ntb
import core.ne_core


class LoadCancelled(Exception):
    pass


@dataclass
class PreparedNote:
    path: str
    svg: bytes
    width: float
    height: float
    pdf_path: str | None = None
    temp_dir: object = None
    archive_path: str | None = None
    bundle_data: bytes | None = None
    render_index: object = None
    page_access_pending: bool = False
    page_access_result: object = None
    compatibility_result: object = None

    def cleanup(self):
        if self.temp_dir:
            self.temp_dir.cleanup()
            self.temp_dir = None


def _convert_embedded_note_layers(svg, sizes):
    """Place legacy global-coordinate strokes into note-local page coordinates.

    This special case is limited to our own experimental NOTE exports, which
    store explicit page dimensions in nv-export-layout.json.
    """
    if not sizes or len(sizes)>10000:
        raise ValueError('NOTE 内嵌背景的页数无效')
    tops=[0.0]
    for width,height in sizes:
        if not 0<float(width)<=14400 or not 0<float(height)<=14400:
            raise ValueError('NOTE 内嵌背景页面尺寸无效')
        tops.append(tops[-1]+float(height))
    match=re.compile(r'([ML])\s*([-+\d.eE]+)[,\s]+([-+\d.eE]+)')
    for el in svg.iter():
        if el.tag.rsplit('}',1)[-1]!='path':
            continue
        commands=match.findall(el.get('d',''))
        if not commands:
            continue
        first_y=float(commands[0][2])
        page=max(0,min(len(sizes)-1,bisect_right(tops,first_y)-1))
        def local(m):
            return f'{m.group(1)}{m.group(2)} {float(m.group(3))-tops[page]:.6f}'
        el.set('d',match.sub(local,el.get('d','')))
        el.set('data-page',str(page))
    svg.set('data-local-ink','1')
    svg.set('data-page-height',str(sizes[0][1]))
    svg.set('data-pages',json.dumps([
        {'width':float(w),'height':float(h),'pdf':i}
        for i,(w,h) in enumerate(sizes)],separators=(',',':')))
    return svg


def prepare_note(path, progress=lambda value, message: None, cancelled=lambda: False):
    def report(value, message):
        if cancelled():
            raise LoadCancelled()
        progress(value, message)
    report(0, '正在读取文件…')
    extension = Path(path).suffix.lower()
    if extension == '.pdf':
        return PreparedNote(path, b'<svg xmlns="http://www.w3.org/2000/svg" data-page-count="1"/>', 1, 1, pdf_path=path)
    if extension not in ('.ntb','.note'):
        raise ValueError('支持 .ntb、.note 和 .pdf 文件')
    temp_dir = None
    try:
        compatibility = None
        if extension == '.ntb':
            from formats.ne_compatibility import repair_on_open
            compatibility = repair_on_open(path, cancelled=cancelled)
        with ZipFile(path) as archive:
            if extension == '.note':
                session = next((n for n in archive.namelist() if n.endswith('Session.plist')), None)
                if not session:
                    raise ValueError('笔记中缺少 Session.plist')
                # Our experimental NOTE has an embedded multipage PDF for
                # backgrounds/pictures, plus separate vector handwriting.
                records=[n for n in archive.namelist() if n.endswith('/nv-export-layout.json')]
                if records:
                    svg,w,h = core.ne_core.convertPlistToSVG(plistlib.loads(archive.read(session)))
                    if len(records)!=1:raise ValueError('NOTE 页面映射重复')
                    layout=json.loads(archive.read(records[0]))
                    sizes=layout['sizes']
                    base=records[0].rsplit('/',1)[0]
                    pdf_entry=base+'/PDFs/'+layout['background']
                    if pdf_entry not in archive.namelist():
                        raise ValueError('NOTE 背景 PDF 文件丢失')
                    svg=_convert_embedded_note_layers(svg,sizes)
                    temp_dir=tempfile.TemporaryDirectory(prefix='notabilityviewer-note-')
                    pdf_path=str(Path(temp_dir.name)/'embedded.pdf')
                    with archive.open(pdf_entry) as original,open(pdf_path,'wb') as output:
                        while True:
                            chunk=original.read(4*1024*1024)
                            if not chunk:break
                            output.write(chunk)
                    report(100,'包含背景与图片的 NOTE 已就绪')
                    return PreparedNote(path,etree.tostring(svg),float(sizes[0][0]),
                                        float(sizes[0][1]),pdf_path,temp_dir)
                from formats.ne_note import read_note
                svg,w,h,pdf_members = read_note(archive,session,report)
                pdf_path = None
                if pdf_members:
                    temp_dir=tempfile.TemporaryDirectory(prefix='notabilityviewer-note-')
                    pdf_path=str(Path(temp_dir.name)/'embedded.pdf')
                    if len(pdf_members)==1:
                        with archive.open(pdf_members[0]) as original,open(pdf_path,'wb') as output:
                            while True:
                                report(95,'正在准备 NOTE 内嵌 PDF…')
                                chunk=original.read(4*1024*1024)
                                if not chunk:break
                                output.write(chunk)
                    else:
                        import pymupdf
                        with pymupdf.open() as merged:
                            for member in pdf_members:
                                report(95,'正在准备 NOTE 内嵌 PDF…')
                                with pymupdf.open(stream=archive.read(member),filetype='pdf') as original:
                                    merged.insert_pdf(original)
                            merged.save(pdf_path)
                report(100,'笔记已就绪')
                return PreparedNote(path,etree.tostring(svg),w,h,pdf_path,temp_dir,path)
            pdfs = []
            for member in archive.namelist():
                if member.lower().endswith('.pdf'):
                    pdfs.append(member)
                elif member.startswith('assets/') and len(member.rsplit('/', 1)[-1]) == 128:
                    # Official NTB exports store content hashes without suffixes.
                    with archive.open(member) as stream:
                        if stream.read(5) == b'%PDF-':
                            pdfs.append(member)
            if len(pdfs) > 1:
                raise ValueError('此笔记包含多个 PDF，当前版本尚不支持其页面映射')
            svg,w,h = formats.ne_ntb.make_svg(path, bool(pdfs),
                                     progress=lambda done,total: report(int(done*35/max(1,total)), '正在解析笔迹和图片…'))
            pdf_path = None
            if pdfs:
                temp_dir = tempfile.TemporaryDirectory(prefix='notabilityviewer-')
                pdf_path = str(Path(temp_dir.name)/'source.pdf')
                total = archive.getinfo(pdfs[0]).file_size
                copied = 0
                with archive.open(pdfs[0]) as source, open(pdf_path,'wb') as output:
                    while True:
                        report(35 + int(60*copied/max(1,total)), f'正在准备 PDF 原文… {copied//1048576} / {total//1048576} MB')
                        chunk = source.read(4*1024*1024)
                        if not chunk:
                            break
                        output.write(chunk)
                        copied += len(chunk)
            report(100,'文件已就绪')
            result = PreparedNote(path,etree.tostring(svg),w,h,pdf_path,temp_dir,path,archive.read('noteBundle'))
            result.compatibility_result = compatibility
            return result
    except BaseException:
        if temp_dir:
            temp_dir.cleanup()
        raise
