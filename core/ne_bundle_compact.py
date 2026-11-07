"""Remove unreachable noteBundle bytes using an explicit official schema.

Only pointer words and vtable displacements change. Inline structs, scalar
values, strings and encoded ink paths remain byte-for-byte intact. Shared
objects remain shared. Unknown nonempty fields/types abort instead of guessing.
This collects *unreachable* objects after a page rewrite, not history still
referenced by the event vector. ZIP asset/PDF cleanup is a separate operation.
"""
import bisect
import hashlib
import struct

from core.ne_bundle_schema import SCHEMA, PAYLOADS


class BundleCompactError(ValueError):
    pass


from core.ne_save_work import checkpoint


class _Graph:
    def __init__(self, data):
        self.data = data
        self.ranges = []
        self.pointers = {}
        self.tables = {}
        self.nodes = set()

    def require(self, offset, size):
        if offset < 0 or size < 0 or offset + size > len(self.data):
            raise BundleCompactError('noteBundle 对象越界，已停止物理清理')

    def span(self, offset, size):
        self.require(offset, size)
        self.ranges.append((offset, offset + size))

    def uint(self, offset, fmt='<I'):
        self.require(offset, struct.calcsize(fmt))
        return struct.unpack_from(fmt, self.data, offset)[0]

    def pointer(self, offset):
        value = self.uint(offset)
        if value < 4:
            raise BundleCompactError('noteBundle 引用无效，已停止物理清理')
        target = offset + value
        self.require(target, 4)
        self.pointers[offset] = target
        return target

    def visit(self, offset, kind):
        key = (offset, kind)
        if key in self.nodes:
            return
        self.nodes.add(key)
        if len(self.nodes) % 512 == 0:
            checkpoint()
        typ = kind[0]
        if typ in ('string', 'vector', 'tables'):
            count = self.uint(offset)
            width = kind[1] if typ == 'vector' else 4 if typ == 'tables' else 1
            size = 4 + count * width + (typ == 'string')
            self.span(offset, size)
            if typ == 'string' and self.data[offset + size - 1] != 0:
                raise BundleCompactError('字符串缺少结束标记，已停止物理清理')
            if typ == 'tables':
                for index in range(count):
                    self.visit(self.pointer(offset + 4 + index * 4), ('table', kind[1]))
            return
        if typ != 'table' or kind[1] not in SCHEMA:
            raise BundleCompactError('未知 noteBundle 对象类型，已停止物理清理')
        name = kind[1]
        schema = SCHEMA[name]
        vtable = offset - self.uint(offset, '<i')
        vsize = self.uint(vtable, '<H')
        # Official iOS bundles encode empty nullable-value tables with a
        # two-byte vtable. Generated __offset reads no fields when length=2;
        # the following bytes are a different object, not an object-size word.
        if vsize == 2:
            self.span(vtable,2)
            self.span(offset,4)
            self.tables[offset]=vtable
            return
        osize = self.uint(vtable + 2, '<H')
        if vsize < 4 or vsize % 2 or osize < 4:
            raise BundleCompactError(f'noteBundle {name}@{offset} 表结构无效 ({vsize}, {osize})，已停止物理清理')
        self.span(vtable, vsize)
        self.span(offset, osize)
        if offset in self.tables and self.tables[offset] != vtable:
            raise BundleCompactError('noteBundle 表别名冲突')
        self.tables[offset] = vtable
        fields = {}
        for slot in range((vsize - 4) // 2):
            relative = self.uint(vtable + 4 + slot * 2, '<H')
            if not relative:
                continue
            if slot >= len(schema):
                raise BundleCompactError(f'{name} 含未知字段 {slot}，不能安全释放原始数据')
            field_kind = schema[slot]
            width = field_kind[1] if field_kind[0] == 'scalar' else 4
            if ((name, slot) in {('Op', 0), ('AssetMetadata', 0), ('AssetCloudPersisted', 0)}
                    and relative == osize and field_kind[0] == 'scalar'):
                # JS export's prebuilt inline struct is outside endObject's
                # recorded size. Keep its exact bytes and original vtable.
                self.span(offset + relative, width)
                fields[slot] = offset + relative
                continue
            if relative < 4 or relative + width > osize:
                raise BundleCompactError(f'{name} 字段超出表边界')
            fields[slot] = offset + relative
        for slot, field in fields.items():
            field_kind = schema[slot]
            if field_kind[0] == 'scalar':
                continue
            if field_kind[0] == 'union':
                payload = self.uint(fields[4], '<B') if 4 in fields else 0
                if payload not in PAYLOADS:
                    raise BundleCompactError(f'未知事件类型 {payload}，不能安全释放原始数据')
                field_kind = ('table', PAYLOADS[payload])
            if field_kind[0] == 'shape_union':
                payload=self.uint(fields[4],'<B') if 4 in fields else 0
                shape={1:'Line',2:'Polygon',3:'NormalShape'}.get(payload)
                if shape is None:raise BundleCompactError('未知形状类型，不能安全清理')
                field_kind=('table',shape)
            self.visit(self.pointer(field), field_kind)

    def read(self):
        self.span(0, 4)
        self.visit(self.pointer(0), ('table', 'NoteBundle'))
        return self


def validate_bundle_schema(data):
    """Reject unknown populated fields without rebuilding a disposable copy."""
    _Graph(data).read()


def compact_bundle(data):
    """Return a smaller semantically identical buffer; raise on unknown layout.

    Call after all page/ink/PDF-reference rewrites, before staging the final ZIP.
    Failure must abort saving, never silently claim physical deletion succeeded.
    """
    graph = _Graph(data).read()
    spans = []
    for begin, end in sorted(graph.ranges):
        if spans and begin <= spans[-1][1]:
            spans[-1] = (spans[-1][0], max(end, spans[-1][1]))
        else:
            spans.append((begin, end))
    output = bytearray()
    starts = []
    shifts = []
    for begin, end in spans:
        # Preserve each object's alignment modulo 8, including struct vectors.
        output.extend(bytes((begin - len(output)) % 8))
        starts.append(begin)
        shifts.append(len(output) - begin)
        output.extend(data[begin:end])

    def relocated(offset):
        index = bisect.bisect_right(starts, offset) - 1
        if index < 0 or offset >= spans[index][1]:
            raise BundleCompactError('物理清理内部引用不完整')
        return offset + shifts[index]

    for field, target in graph.pointers.items():
        struct.pack_into('<I', output, relocated(field), relocated(target) - relocated(field))
    for table, vtable in graph.tables.items():
        struct.pack_into('<i', output, relocated(table), relocated(table) - relocated(vtable))
    result = bytes(output)
    # Reparse the rebuilt graph and compare every preserved byte except the
    # explicitly relocated pointer words. This includes opaque path vectors.
    rebuilt = _Graph(result).read()
    if len(rebuilt.nodes) != len(graph.nodes):
        raise BundleCompactError('物理清理对象数校验失败')
    check = bytearray(result)
    for field in graph.pointers:
        check[relocated(field):relocated(field) + 4] = data[field:field + 4]
    for table in graph.tables:
        check[relocated(table):relocated(table) + 4] = data[table:table + 4]
    for begin, end in spans:
        at = relocated(begin)
        if check[at:at + end - begin] != data[begin:end]:
            raise BundleCompactError('物理清理原始内容校验失败')
    return result


def reachable_digest(data):
    """Layout-independent byte digest for verification of a known bundle."""
    return hashlib.sha256(compact_bundle(data)).hexdigest()
