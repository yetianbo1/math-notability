"""User-editable shortcuts: identifiers, defaults and contextual groups.

This module deliberately has no Qt dependency so the registry can be audited
and regression-tested on non-Windows build hosts.
"""

# Identifier -> (category, display name, portable Qt shortcut, context)
SPECS = {
    'fullscreen': ('视图', '全屏编辑', 'F11', 'global'),
    'open': ('文件', '打开文件', 'Ctrl+O', 'global'),
    'pdf_note': ('文件', '导入 PDF', 'Ctrl+Shift+N', 'global'),
    'save': ('文件', '保存', 'Ctrl+S', 'global'),
    'save_as': ('文件', '另存为', 'Ctrl+Shift+S', 'global'),
    'export_pdf': ('文件', '导出 PDF', 'Ctrl+Shift+E', 'global'),
    'export_note': ('文件', '导出 NOTE', '', 'global'),
    'undo': ('编辑', '撤销', 'Ctrl+Z', 'global'),
    'redo': ('编辑', '回退', 'Ctrl+Y;Ctrl+Shift+Z', 'global'),
    'draw': ('工具', '画笔', 'B', 'global'),
    'highlight': ('工具', '荧光笔', 'H', 'global'),
    'erase': ('工具', '橡皮擦', 'E', 'global'),
    'select': ('工具', '选择笔迹', 'S', 'global'),
    'image': ('工具', '图片管理', 'I', 'global'),
    'read': ('工具', '阅读模式', 'V', 'global'),
    'previous': ('视图', '上一页', 'Alt+Left', 'global'),
    'next': ('视图', '下一页', 'Alt+Right', 'global'),
    'zoom_in': ('视图', '放大', 'Ctrl+=', 'global'),
    'zoom_out': ('视图', '缩小', 'Ctrl+-', 'global'),
    'fit': ('视图', '适应宽度', 'Ctrl+0', 'global'),
    'goto': ('视图', '跳转页码', 'Ctrl+L', 'global'),
    'sidebar': ('页面', '显示/隐藏页面侧栏', 'Ctrl+Shift+P', 'global'),
    'help': ('帮助', '操作与快捷键', 'F1', 'global'),
    'ink_select_all': ('编辑', '全选当前页笔迹（编辑区）', 'Ctrl+A', 'ink'),
    'ink_delete': ('编辑', '删除选中笔迹（编辑区）', 'Delete', 'ink'),
    'page_copy': ('页面', '复制当前页（页面侧栏）', 'Ctrl+C', 'page_normal'),
    'page_cut': ('页面', '剪切当前页（页面侧栏）', 'Ctrl+X', 'page_normal'),
    'page_paste': ('页面', '粘贴到当前页后（页面侧栏）', 'Ctrl+V', 'page_normal'),
    'page_delete': ('页面', '删除当前页（页面侧栏）', 'Delete', 'page_normal'),
    'page_all': ('页面', '批量全选（批量模式）', 'Ctrl+A', 'page_batch'),
    'page_invert': ('页面', '批量反选（批量模式）', 'Ctrl+I', 'page_batch'),
    'page_delete_batch': ('页面', '批量删除所选（批量模式）', 'Delete', 'page_batch'),
}


def default_bindings():
    return {key: spec[2] for key, spec in SPECS.items()}


def conflicting(left, right):
    """Global shortcuts compete with everything; disjoint editor areas do not."""
    a, b = SPECS[left][3], SPECS[right][3]
    return a == 'global' or b == 'global' or a == b
