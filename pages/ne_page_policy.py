"""Page-level safety policy and native-paper preset descriptions.

Known = originally validated by an available page writer, or a new empty
page made by this application. An unknown source page must never be cut,
replaced or physically deleted merely because another page is safe.
"""
from __future__ import annotations

PAPER_KINDS = ('blank', 'ruled', 'dotted')
PAPER_LABELS = {'blank': '纯空白', 'ruled': '横线', 'dotted': '点状'}
DEFAULT_PITCH_PT = 36.0  # Notability default UI spacing level 4 in supplied samples


def resolve_paper_kind(selected, source_kind):
    if selected in (None, 'follow'):
        return source_kind if source_kind in PAPER_KINDS else 'blank'
    if selected not in PAPER_KINDS:
        raise ValueError('仅支持空白、横线、点状三种已知底页')
    return selected


def validate_native_paper_choice(writer_kind, source_kind, selected):
    """Native grouped paper stores one document-wide paper template.

    A different paper style on just one page is *not* independently encoded
    by its page range. Never pretend to have saved a mixed official template.
    Grouped PDF notes can instead rasterize the chosen paper into a new page.
    """
    choice = resolve_paper_kind(selected, source_kind)
    if writer_kind in ('official_grouped_paper', 'native_per_page') and choice != source_kind:
        raise ValueError('当前 NTB 的原生纸张样式应用于整个笔记，尚不能安全混用不同底页。'
                         '请选择与原笔记一致的底页，或在支持混合背景的 PDF 分组笔记中添加。')
    return choice


def native_pattern_spec(kind, width, *, pitch=DEFAULT_PITCH_PT):
    if kind not in PAPER_KINDS:
        raise ValueError('未知纸张底页')
    if kind == 'blank':
        return {'kind': 'blank'}
    if kind == 'ruled':
        return {'kind': 'ruled', 'pitch': pitch, 'margin': 36.0, 'color': '#a3b7d3'}
    import math
    return {'kind': 'dotted', 'pitch': pitch,
            'x_origin': (width - math.floor(width / pitch) * pitch) / 2,
            'radius': 1.0, 'color': '#a3b7d3'}


def is_known(spec, known_original=False):
    """Copied unknown pages stay unknown; a newly added empty sheet is known."""
    if spec.get('known_page') is True:
        return True
    if spec.get('known_page') is False:
        return False
    return bool(known_original and spec.get('source') is not None)
