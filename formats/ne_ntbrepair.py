"""Experimental compatibility repair for NTBs created by earlier PDF importer.

Rebuild the PDF/page metadata, retaining original vector-ink event bytes.
Notability compatibility is not proven without an official import test.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED, ZIP_STORED

import formats.ne_ntb
from formats.ne_native import Builder, NativeWriteError
from formats.ne_pagewrite import validate_page_structure
from import_export.ne_pdfimport import pdf_to_ntb
from core.ne_titles import effective_title

class RepairNotPossible(ValueError):
    pass


def repair_generated_ntb(input_ntb, output_ntb):
    """Repair known PDF-importer structures, without changing existing pen data.

    Supports only 1:1 PDF pages and pure type-15/25 ink overlays. Reject other
    events, reordered pages or files that need a richer conversion.
    """
    source, output = Path(input_ntb).resolve(), Path(output_ntb).resolve()
    if source == output:
        raise RepairNotPossible('请另存为新文件，不覆盖原 NTB。')
    with ZipFile(source) as z:
        names = z.namelist()
        pdfs = [name for name in names if name.startswith('assets/') and name.endswith('.pdf')]
        if len(pdfs)!=1:
            raise RepairNotPossible('当前只支持恰好包含一份 PDF 的导入型 NTB。')
        old = z.read('noteBundle')
        archive_pdf = pdfs[0]
        pdf_bytes = z.read(archive_pdf)
        manifest = z.read('manifest.json')
        thumbnail = z.read('thumbnail.png')
    import hashlib
    if archive_pdf != 'assets/'+hashlib.sha512(pdf_bytes).hexdigest()+'.pdf':
        raise RepairNotPossible('PDF 素材哈希不匹配，停止转换。')
    try:
        buf, root, old_events, page_ids = validate_page_structure(old)
    except NativeWriteError as exc:
        raise RepairNotPossible('此文件不适合旧版修复工具；官方分组 PDF 请使用正常的笔迹保存功能：' + str(exc)) from exc
    from formats.ne_ntb import page_records
    pages = page_records(old_events, buf)
    if any(p['id'] != i+2 or p['asset'] != hashlib.sha512(pdf_bytes).hexdigest()
           or p['pdf'] != i for i,p in enumerate(pages)):
        raise RepairNotPossible('页面已经重排或引用关系不同，不适合无损自动修复。')
    old_ink = []
    for off, event in old_events:
        typ=old[event.field(4)]
        if typ in (15,25):
            old_ink.append((off,event))
        elif typ not in (1,3):
            raise RepairNotPossible(f'文件包含暂不支持无损迁移的事件类型 {typ}。')
    with tempfile.TemporaryDirectory(prefix='nv-ntb-repair-') as d:
        work=Path(d)
        pdf=work/'source.pdf'
        pdf.write_bytes(pdf_bytes)
        from fitz import open as fitzopen
        with fitzopen(pdf) as document:
            if document.page_count != len(pages):
                raise RepairNotPossible('PDF 底图页数和笔记页面数不一致。')
        base=pdf_to_ntb(pdf,work/(output.stem+'.ntb'))
        with ZipFile(base) as archive:
            new=archive.read('noteBundle')
        newbuf,newroot,new_events,_=validate_page_structure(new)
        if len(new_events)!=len(pages)+3:
            raise RepairNotPossible('更新后的生成器未写入完整初始化事件。')
        # Build new root and forward event vector before any target event objects.
        builder=Builder()
        builder.data=bytearray(new[:newroot.offset+newroot.osize])
        total=len(new_events)+len(old_ink)
        vector=builder.vector(b'\x00'*(4*total),total)
        builder.uoffset(newroot.field(6),vector)
        builder.align(8)
        new_shift=len(builder.data)
        builder.add(new)
        builder.align(8)
        old_shift=len(builder.data)
        builder.add(old)
        for slot in (3,5):
            f=newroot.field(slot)
            if f:builder.uoffset(f,f+newbuf.u32(f)+new_shift)
        for i,(off,_) in enumerate(new_events):
            builder.uoffset(vector+4+4*i,off+new_shift)
        for i,(off,_) in enumerate(old_ink,len(new_events)):
            builder.uoffset(vector+4+4*i,off+old_shift)
        rebuilt=bytes(builder.data)
        verify,vroot,events,ids=validate_page_structure(rebuilt)
        counts=lambda b,ev: sum(b.data[e.field(4)]==15 for _,e in ev)
        if len(ids)!=len(pages) or counts(verify,events)!=counts(buf,old_events):
            raise RepairNotPossible('修复结果的页面数或笔迹事件数不同。')
        if effective_title(rebuilt)!=output.stem:
            raise RepairNotPossible('文件内部标题与输出文件名不同。')
        output.parent.mkdir(parents=True,exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix='.ntb-fix-',suffix='.ntb',dir=output.parent)
        os.close(fd)
        try:
            with ZipFile(tmp,'w',compression=ZIP_DEFLATED,allowZip64=True) as z:
                z.writestr('version',b'1')
                z.writestr('noteBundle',rebuilt)
                z.writestr('manifest.json',manifest)
                z.writestr('thumbnail.png',thumbnail)
                z.writestr(archive_pdf,pdf_bytes,compress_type=ZIP_STORED)
            with ZipFile(tmp) as z:
                if z.testzip() is not None or z.read('noteBundle')!=rebuilt:
                    raise RepairNotPossible('修复文件 ZIP 完整性失败。')
            os.replace(tmp,output)
        finally:
            if os.path.exists(tmp):os.unlink(tmp)
    return output


def main():
    """Optional Windows GUI for repairing older PDF-importer-created NTB notes."""
    from tkinter import Tk, filedialog, messagebox
    win=Tk();win.withdraw()
    try:
        source=filedialog.askopenfilename(title='Select NTB created by NotabilityEditor',filetypes=[('NTB notes','*.ntb')])
        if not source:return
        source=Path(source)
        target=filedialog.asksaveasfilename(title='Save experimental compatibility repair',
            defaultextension='.ntb', initialfile=source.stem+'-repaired.ntb',
            filetypes=[('NTB notes','*.ntb')])
        if not target:return
        if Path(target).resolve()==source.resolve():
            messagebox.showerror('Repair', 'Do not overwrite the original NTB.');return
        repair_generated_ntb(source,target)
        messagebox.showinfo('Repair', 'A repaired NTB copy was created.\nOfficial Notability compatibility has NOT been verified.\nKeep the original file.')
    except Exception as exc:
        messagebox.showerror('Repair failed', str(exc))
    finally:
        win.destroy()


if __name__=='__main__':
    main()
