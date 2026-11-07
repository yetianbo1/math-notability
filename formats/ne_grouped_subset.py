"""Lossless PDF-page subset from a known, annotation-free official PDF-group NTB.

This is intentionally a *separate-copy* command, not the in-place page-event
writer. Rebuilds a fresh official-tested 2/3/4-page bundle, never edits input.
It rejects handwriting and every unfamiliar event/asset: losing hidden note
content to make deletion appear successful would be worse than refusing.
"""
from __future__ import annotations

from hashlib import sha512
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZipFile

import fitz

import formats.ne_ntb as ntb
from import_export.ne_official_pdf import (_record_tables, pdf_to_official_template_ntb,
                             OfficialTemplateError)

class GroupedSubsetError(ValueError):
    pass


def delete_grouped_pdf_pages(source, target, keep_pages):
    """Save a new NTB containing specified 1-based original pages (ascending).

    Constraints: a standalone PDF group with no annotations, no extras, and
    supported 595x842pt layout. Exactly 2..4 pages must remain. The source
    and existing destination are untouched even when validation fails.
    """
    src=Path(source).resolve(); dst=Path(target).resolve()
    if (src.suffix.lower()!='.ntb' or not src.is_file() or
            dst.suffix.lower()!='.ntb' or src==dst):
        raise GroupedSubsetError('请指定已有的 .ntb 源文件和另一个 .ntb 目标文件')
    if dst.exists():
        raise GroupedSubsetError('目标文件已存在：为避免覆盖，请使用新的文件名')
    if (not isinstance(keep_pages,(list,tuple)) or
            not all(type(x) is int for x in keep_pages) or
            list(keep_pages)!=sorted(set(keep_pages)) or
            not 2<=len(keep_pages)<=4):
        raise GroupedSubsetError('保留页码必须严格递增、无重复，且保留 2～4 页')
    try:
        with ZipFile(src) as z:
            names=z.namelist()
            if len(names)!=len(set(names)) or z.testzip() is not None:
                raise GroupedSubsetError('源 NTB ZIP 校验失败或存在重复成员')
            bdata=z.read('noteBundle')
            b,r,events,payload,bg,pdf,asset=_record_tables(bdata)
            kinds=tuple(bdata[e.field(4)] for _,e in events)
            if kinds not in ((1,3,1,22),(1,3,1,22,25,25,25)):
                raise GroupedSubsetError('文件含手写笔迹、图片或尚未识别的事件；禁止删除以保护数据')
            count=ntb.document_page_count(events,b)
            if count not in (2,3,4) or ntb.field_u32(b,pdf,3)!=count:
                raise GroupedSubsetError('分组页数不属于当前支持的官方结构')
            if any(not 1<=x<=count for x in keep_pages) or len(keep_pages)>=count:
                raise GroupedSubsetError('保留页面必须在源页数范围内，且至少删除一页')
            hpos=asset.field(0)
            if not hpos or not b.valid(hpos,64) or not asset.field(3):
                raise GroupedSubsetError('PDF 资源引用字段缺失')
            rawhash=bdata[hpos:hpos+64]
            digest=rawhash.hex()
            assetname=f'assets/{digest}.pdf'
            expected={'version','noteBundle',assetname,'manifest.json',
                      'index/ios/PDFIndex.fb','thumbnail.png'}
            if set(names)!=expected:
                raise GroupedSubsetError('存在额外资源或未知文件，不执行可能丢失数据的页面删除')
            pdfdata=z.read(assetname)
            if sha512(pdfdata).digest()!=rawhash or b.u32(asset.field(3))!=len(pdfdata):
                raise GroupedSubsetError('PDF 素材 SHA-512 或尺寸校验失败')
            idx=z.read('index/ios/PDFIndex.fb')
            if idx.count(rawhash)!=1:
                raise GroupedSubsetError('PDFIndex 索引中的资源哈希异常')
        with fitz.open(stream=pdfdata,filetype='pdf') as original:
            if original.needs_pass or original.page_count!=count:
                raise GroupedSubsetError('原始 PDF 页面数量与 NTB 事件不一致')
            if any(abs(p.rect.width-595)>0.08 or abs(p.rect.height-842)>0.08
                   for p in original):
                raise GroupedSubsetError('当前仅支持全部页面为 595×842 pt 的 PDF')
            with TemporaryDirectory(prefix='nv-group-subset-') as work:
                reduced=Path(work)/'selected.pdf'
                with fitz.open() as output_pdf:
                    for number in keep_pages:
                        output_pdf.insert_pdf(original,from_page=number-1,to_page=number-1)
                    output_pdf.save(str(reduced),garbage=4,deflate=True)
                # No handwritten or independent images exist in the validated
                # source, so rebuilding the PDF group cannot discard those.
                pdf_to_official_template_ntb(reduced,dst)
    except (KeyError, OSError, fitz.FileDataError, OfficialTemplateError) as exc:
        raise GroupedSubsetError(str(exc)) from exc
    return dst


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description='Save selected original PDF pages as a NEW official-structure NTB; no ink supported')
    parser.add_argument('source',help='unchanged source NTB')
    parser.add_argument('target',help='new NTB; must not exist')
    parser.add_argument('pages',nargs='+',type=int,help='1-based original pages to KEEP, in ascending order')
    args=parser.parse_args()
    print(delete_grouped_pdf_pages(args.source,args.target,args.pages))
