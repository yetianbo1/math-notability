"""Validate root namespaces and repair the older generated paper-note header.

The official iOS 16.13.x exports and bundled PDF templates contain both root
strings (slots 3 and 5). The earlier native-paper generator omitted them.
Keep every original event and opaque byte when adding the missing header.
"""
import struct
import json
import math


def repair_generated_paper(data, archive):
    """Clear the conflicting legacy image only in this app's compact notes.

    Official notes can intentionally combine legacy paper and RGBA. Require
    our creator marker and the verified compact layout before changing bytes.
    """
    try:
        manifest = json.loads(archive.read('manifest.json'))
    except (KeyError, ValueError, UnicodeError):
        return data
    if not isinstance(manifest, dict) or manifest.get('appVersion') != 'NotabilityViewer':
        return data
    from formats.ne_native import read_structure
    from formats.ne_ntb import _compact_supported_paper_type, _paper_styles
    buf, root, events = read_structure(data)
    if (not root.field(7) or buf.u16(root.field(7)) != 20
            or _compact_supported_paper_type(events, buf) is None):
        return data
    positions = []
    for _, _, _, style in _paper_styles(events, buf):
        rgba, marker = style.field(4), style.field(5)
        if (not rgba or not buf.valid(rgba, 4) or buf.data[rgba+3] != 255
                or not marker or not buf.valid(marker, 1)
                or buf.data[marker] not in (0, 13)):
            return data
        if buf.data[marker] == 13:
            positions.append(marker)
    if not positions:
        return data
    result = bytearray(data)
    for pos in positions:
        result[pos] = 0
    return bytes(result)


def repair_generated_paper_pattern(data, archive):
    """Remove the old paper selector from verified generated modern papers.

    Clone only the scalar Paper tables, preserving RGBA and all explicit
    pattern settings. Parent pointers change; all events and assets survive.
    A null legacy selector avoids mixing legacy paper defaults with modern
    flair/spacing. This also handles v6's explicit zero selector.
    """
    try:
        manifest=json.loads(archive.read('manifest.json'))
    except (KeyError,ValueError,UnicodeError):return data
    if not isinstance(manifest,dict) or manifest.get('appVersion')!='NotabilityViewer':return data
    from formats.ne_native import Builder,read_structure
    from formats.ne_ntb import _compact_supported_paper_type,_paper_styles
    buf,root,events=read_structure(data)
    if not root.field(7) or buf.u16(root.field(7))!=20:return data
    styles=list(_paper_styles(events,buf))
    if _compact_supported_paper_type(events,buf) is None:
        # Old page saves move the settings ahead of all independent pages.
        # The compact prefix is gone; require the checked page writer and the
        # exact generated two-setting geometry/style history instead.
        try:
            from formats.ne_pagewrite import validate_page_structure
            validate_page_structure(data)
        except ValueError:return data
        if len(styles)!=2 or abs(styles[0][0]-612.)>.02 or abs(styles[0][1]-792.)>.02:return data
        for index,(width,height,paper,style) in enumerate(styles):
            if not style or not paper.field(4) or not buf.valid(paper.field(4),16):return data
            flag,pitch=style.field(0),style.field(1)
            if index==0 and (flag or pitch):return data
            if bool(flag)!=bool(pitch):return data
            if flag and (not buf.valid(flag,1) or buf.data[flag] not in (0,1)
                         or not buf.valid(pitch,4) or not math.isfinite(buf.f32(pitch))
                         or abs(buf.f32(pitch)-36.)>.02):return data
            expected=(0.,0.,36.,36.) if flag else (36.,)*4
            margins=struct.unpack_from('<4f',data,paper.field(4))
            if any(not math.isfinite(actual) or abs(actual-wanted)>.02
                   for actual,wanted in zip(margins,expected)):return data
    changes=[]
    for _,_,paper,style in styles:
        if not style or style.slots>6:return data
        for slot in (2,3):
            field=style.field(slot)
            if field and (not buf.valid(field,1) or data[field] not in (0,1)):return data
        rgba,marker=style.field(4),style.field(5)
        if (not rgba or not buf.valid(rgba,4) or buf.data[rgba+3]!=255
                or (marker and (not buf.valid(marker,1) or buf.data[marker] not in (0,13)))):
            return data
        if marker:changes.append((paper.field(0),style))
    if not changes:return data
    builder=Builder();builder.data=bytearray(data)
    for parent,style in changes:
        values={}
        for slot,width in enumerate((1,4,1,1,4)):
            field=style.field(slot)
            if field:
                if not buf.valid(field,width):return data
                values[slot]=(data[field:field+width],4 if width==4 else 1)
        if style.field(0) and style.field(1):
            values.setdefault(2,(b'\0',1));values.setdefault(3,(b'\0',1))
        replacement,_=builder.table(5,values)
        builder.uoffset(parent,replacement)
    return bytes(builder.data)


class BundleIdentityError(ValueError):
    pass


def bundle_namespaces(data):
    from formats.ne_native import read_structure
    buf,root,_=read_structure(data)
    values=[]
    for slot in (3,5):
        field=root.field(slot)
        if not field or not buf.valid(field,4):
            raise BundleIdentityError('笔记根表缺少命名空间字符串')
        offset=field+buf.u32(field)
        if not buf.valid(offset,4):raise BundleIdentityError('笔记命名空间引用越界')
        length=buf.u32(offset)
        if not length or not buf.valid(offset+4,length+1) or data[offset+4+length]!=0:
            raise BundleIdentityError('笔记命名空间字符串不完整')
        try:values.append(data[offset+4:offset+4+length].decode('utf-8'))
        except UnicodeDecodeError as exc:raise BundleIdentityError('笔记命名空间编码无效') from exc
    return tuple(values)


def repair_generated_namespaces(data):
    """Add namespaces only to the recognized header made by the old generator.

    An existing namespace or an unfamiliar layout leaves the input untouched.
    A deterministic name keeps repeated saves of the same old baseline stable.
    """
    from formats.ne_native import Builder,read_structure
    from formats.ne_ntb import payload_table
    buf,root,events=read_structure(data)
    populated={slot for slot in range(root.slots) if root.field(slot)}
    if populated!={0,4,6,7} or buf.u16(root.field(7))!=20 or len(events)<4:return data
    if [data[event.field(4)] for _,event in events[:4]]!=[1,3,1,1]:return data
    for index,(_,event) in enumerate(events[:4]):
        ref=event.field(0)
        if not ref or not buf.valid(ref,8) or struct.unpack_from('<2I',data,ref)!=(0,index):return data
    page=payload_table(buf,events[1][1])
    if not page or page.field(0) or page.field(1) or not page.field(2) or buf.u32(page.field(2))!=2:return data
    if not buf.valid(root.field(0),16) or not buf.valid(root.field(4),8):return data
    namespace=('_'+data[root.field(0):root.field(0)+16].hex()).encode('ascii')
    builder=Builder();builder.data=bytearray(4)
    header,fields=builder.table(8,{
        0:(data[root.field(0):root.field(0)+16],8),
        3:(bytes(4),4),4:(data[root.field(4):root.field(4)+8],8),
        5:(bytes(4),4),6:(bytes(4),4),7:(data[root.field(7):root.field(7)+2],2),
    })
    struct.pack_into('<I',builder.data,0,header)
    string=builder.vector(namespace+b'\0',len(namespace))
    for slot in (3,5):builder.uoffset(fields[slot],string)
    builder.align(8);shift=len(builder.data);builder.add(data)
    old_vector=root.field(6)+buf.u32(root.field(6))
    builder.uoffset(fields[6],old_vector+shift)
    result=bytes(builder.data)
    if bundle_namespaces(result)!=(namespace.decode(),)*2 or result[shift:]!=data:
        raise BundleIdentityError('笔记命名空间补全校验失败')
    return result
