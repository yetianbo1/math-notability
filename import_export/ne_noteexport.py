"""Legacy .note export: lossless copy of original NOTE inputs, experimental
NTB conversion carrying embedded PDF backgrounds and separate editable ink.

.notability NOTE is NOT a renamed NTB: NSKeyedArchiver with a Session.plist.
The archive emitted from NTB is intentionally marked EXPERIMENTAL. Its session
hierarchy is a minimal best-effort writer and is not officially import-tested.
Backgrounds (including pictures) are rendered into a multipage PDF; images are
not individually editable. Audio and typed objects are not preserved.
Compatibility with the official Notability importer is not verified.
"""
import os
import plistlib
import struct
import tempfile
import uuid
import shutil
import json
from pathlib import Path
from zipfile import ZipFile,ZIP_DEFLATED
from datetime import datetime,timezone
from xml.etree import ElementTree as ET


def note_ink_components(pages):
    """pages = [(width, height, iterable[Stroke]), ...]; convert to one scroll."""
    counts=[];points=[];widths=[];colors=[];styles=[];height=0.;max_width=0.
    for width,page_height,strokes in pages:
        if width <= 0 or page_height <= 0:raise ValueError('无效页面尺寸')
        max_width=max(max_width,float(width))
        for stroke in strokes:
            pts=list(stroke.points)
            if not pts:continue
            counts.append(len(pts));widths.append(float(stroke.width))
            rgb=str(stroke.color).lstrip('#')
            if len(rgb)!=6:raise ValueError('NOTE 不支持此笔迹颜色格式')
            rgba=bytes.fromhex(rgb)+bytes((max(0,min(255,round(float(stroke.opacity)*255))),))
            colors.append(rgba)
            styles.append(4 if stroke.opacity < .99 else 3)
            points.extend((float(x),float(y)+height) for x,y in pts)
        height+=float(page_height)
    return dict(numcurves=len(counts),numpoints=len(points),
                curvesnumpoints=struct.pack('<%di'%len(counts),*counts),
                curvespoints=b''.join(struct.pack('<ff',*p) for p in points),
                curveswidth=struct.pack('<%df'%len(widths),*widths),
                curvescolors=b''.join(colors),
                curvesstyles=struct.pack('<%di'%len(styles),*styles),
                numfractionalwidths=0,curvesfractionalwidths=b'',
                document_width=max_width,document_height=height)


def make_keyed_session(title, ink, background_name=None, sizes=None):
    """Best-effort keyed object graph. PDF references follow observed v9 NOTE layout.

    No claim of official Notability support: archive validation only proves
    internal consistency, not that proprietary importers accept this layout.
    """
    uid=plistlib.UID
    objs=['$null']
    def item(obj):
        objs.append(obj)
        return uid(len(objs)-1)
    def cls(name):
        return item({'$classname':name,'$classes':[name,'NSObject']})
    session_class=cls('NoteTakingSession')
    rich_class=cls('FormattedString')
    hand_class=cls('HandwritingObject')
    hash_class=cls('InkedSpatialHash')
    array_class=cls('NSArray')
    dict_class=cls('NSDictionary')
    paper_class=cls('Notability.NoteDocumentPaperLayoutModel')
    pdf_class=cls('PDFFile')
    hash_fields={key:value for key,value in ink.items() if key not in ('document_width','document_height')}
    hash_fields['$class']=hash_class
    spatial=item(hash_fields)
    handwriting=item({'$class':hand_class,'SpatialHash':spatial})
    rich_data={'$class':rich_class,'Handwriting Overlay':handwriting,
               'pageWidthInDocumentCoordsKey':ink['document_width'],
               'pageHeightInDocumentCoordsKey':ink['document_height'],
               'formatVersion':4}
    top_data={'$class':session_class,'name':title,'sessionFormatVersion':9,
              'subject':'unsortedNotesKey'}
    if background_name:
        if not sizes:
            raise ValueError('嵌入背景时需要页尺寸')
        pdf=item({'$class':pdf_class,'pdfFileName':background_name,
                  'highlights':[], 'pageNumbers':uid(0),'version':2,
                  'type':0,'contentBoxVersion':1})
        pdf_files=item({'$class':array_class,'NS.objects':[pdf]})
        layout=[]
        for i, _size in enumerate(sizes,1):
            names=['kPageLayoutDocumentPageNumberKey',
                   'kPageLayoutPDFPageNumberKey','kPageLayoutPDFFileNameKey',
                   'kPageLayoutPDFIsOriginalPageKey','kPageLayoutPageIsBookmarkedKey']
            values=[i,i,background_name,True,False]
            layout.append(item({'$class':dict_class,'NS.keys':names,'NS.objects':values}))
        page_array=item({'$class':array_class,'NS.objects':layout})
        paper=item({'$class':paper_class,'pageLayoutArray':page_array})
        rich_data['pdfFiles']=pdf_files
        rich_data['pageLayoutArray']=page_array
        top_data['NBNoteTakingSessionDocumentPaperLayoutModelKey']=paper
    else:
        rich_data['pageLayoutArray']=[]
    rich=item(rich_data)
    top_data['richText']=rich
    top=item(top_data)
    return {'$archiver':'GLKeyedArchiver','$version':100000,
            '$objects':objs,'$top':{'root':top}}


def _validate_note(path,expected_strokes=None,expect_background=False):
    with ZipFile(path) as z:
        if z.testzip():raise ValueError('NOTE ZIP 校验失败')
        sessions=[name for name in z.namelist() if name.endswith('Session.plist')]
        if len(sessions)!=1:raise ValueError('NOTE 缺少唯一 Session.plist')
        document=plistlib.loads(z.read(sessions[0]))
        if not isinstance(document,dict) or document.get('$archiver') not in ('GLKeyedArchiver','NSKeyedArchiver'):
            raise ValueError('NOTE Session.plist 格式错误')
        if expect_background:
            metadata=[x for x in z.namelist() if x.endswith('/nv-export-layout.json')]
            if len(metadata)!=1:raise ValueError('NOTE 缺少背景页面映射')
            layout=json.loads(z.read(metadata[0]))
            name=layout['background']
            expected=metadata[0].rsplit('/',1)[0]+'/PDFs/'+name
            if expected not in z.namelist() or not z.read(expected).startswith(b'%PDF-'):
                raise ValueError('NOTE 内嵌 PDF 丢失或无效')
            if len(layout['sizes'])<1:raise ValueError('NOTE 背景页面为空')
            names=[d.get('pdfFileName') for d in document.get('$objects',[])
                   if isinstance(d,dict) and 'pdfFileName' in d]
            if name not in names:raise ValueError('Session.plist 缺少 PDF 引用')
        if expected_strokes is not None:
            records=[item for item in document.get('$objects',[]) if isinstance(item,dict) and 'curvesnumpoints' in item]
            if len(records)!=1 or records[0]['numcurves']!=expected_strokes:
                raise ValueError('NOTE 笔迹数量校验失败')
    return True


def export_note(source,target,pages=None,background_pdf=None):
    """Write an independent .note file; never mutate the active note source."""
    source=Path(source).resolve();target=Path(target).resolve()
    if source==target:raise ValueError('导出文件不能覆盖正在打开的原文件')
    target.parent.mkdir(parents=True,exist_ok=True)
    if source.suffix.lower() not in ('.note','.ntb'):
        raise ValueError('只能从 NOTE 或 NTB 导出 NOTE')
    if source.suffix.lower()=='.ntb' and pages is None:raise ValueError('请提供笔记页面数据')
    if background_pdf is not None and source.suffix.lower()!='.ntb':
        raise ValueError('原 NOTE 请使用无损副本导出')
    fd,tmp=tempfile.mkstemp(prefix='.nv-note-',suffix='.tmp',dir=target.parent)
    os.close(fd)
    try:
        if source.suffix.lower()=='.note':
            _validate_note(source)
            shutil.copyfile(source,tmp)
            _validate_note(tmp)
        else:
            ink=note_ink_components(pages)
            title=source.stem
            stem=str(uuid.uuid4())
            sizes=[(float(width),float(height)) for width,height,_strokes in pages]
            pdf_name=str(uuid.uuid4()).upper()+'.pdf' if background_pdf else None
            session=plistlib.dumps(make_keyed_session(title,ink,pdf_name,sizes),fmt=plistlib.FMT_BINARY)
            now=datetime.now(timezone.utc).isoformat()
            metadata=plistlib.dumps({'noteName':title,'uuidKey':stem,'documentVersion':9,
                                     'noteSubject':'unsortedNotesKey','exportedAt':now},fmt=plistlib.FMT_BINARY)
            with ZipFile(tmp,'w',compression=ZIP_DEFLATED) as z:
                z.writestr(f'{stem}/Session.plist',session)
                z.writestr(f'{stem}/metadata.plist',metadata)
                if background_pdf:
                    background_path=Path(background_pdf)
                    if not background_path.is_file() or background_path.stat().st_size < 50:
                        raise ValueError('合成背景 PDF 不存在或为空')
                    z.write(background_path,f'{stem}/PDFs/{pdf_name}')
                    # Private metadata lets this viewer faithfully reconstruct
                    # page sizes. It is not a Notability-defined field.
                    z.writestr(f'{stem}/nv-export-layout.json',json.dumps({
                        'version':1,'background':pdf_name,'sizes':sizes},
                        ensure_ascii=True).encode('utf-8'))
            _validate_note(tmp,ink['numcurves'],expect_background=bool(background_pdf))
        os.replace(tmp,target)
    finally:
        if os.path.exists(tmp):os.remove(tmp)
    return target
