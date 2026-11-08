"""A full, mirrored editing toolbar for question cards.

The card is normally full-screen, so its own toolbar must expose the same
tools and settings as the main window.  Every button here either reuses an
existing QAction owned by the main window, or writes a value back into a main
window control.  Nothing here rebuilds ``owner.tools`` / ``owner.pen_size``:
constructing a second EditorPanel would silently overwrite the main window's
state, which is exactly what we must avoid.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QAction, QActionGroup
from PyQt6.QtWidgets import (QFrame, QWidget, QHBoxLayout, QVBoxLayout,
                             QToolButton, QLabel, QDoubleSpinBox, QSpinBox,
                             QComboBox, QStackedWidget)

from editing.ne_edits import COLORS, HIGHLIGHT_COLORS
from editing.ne_shapes import KINDS, LABELS
from ui.ne_toolbar import tool_icon

TOOL_KEYS = ('draw', 'shape', 'highlight', 'tape', 'erase', 'select', 'image', 'read')
TOOL_LABELS = {'draw': '画笔', 'shape': '图形', 'highlight': '荧光笔',
               'tape': '遮字胶带', 'erase': '橡皮擦', 'select': '选择',
               'image': '图片', 'read': '阅读'}


def _colored_chip(row, action, color, name):
    chip = QToolButton()
    chip.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
    chip.setFixedSize(24, 24)
    chip.setToolTip(name + ' ' + color)
    chip.setAccessibleName('颜色 ' + name)
    chip.setStyleSheet(
        f'QToolButton {{ background:{color}; border:2px solid #d1d5db; border-radius:12px; }}'
        'QToolButton:checked { border:3px solid #1d4ed8; }'
        'QToolButton:hover { border-color:#93c5fd; }')
    if action is not None:
        chip.setDefaultAction(action)
    else:
        chip.setEnabled(False)
    row.addWidget(chip)
    return chip


def _separator(row):
    line = QFrame()
    line.setFixedSize(1, 20)
    line.setStyleSheet('background:#e2e8f0;border:0;')
    row.addWidget(line)


class CardToolBar(QFrame):
    """Two rows: eight shared tool actions + undo/redo, then per-tool settings."""

    def __init__(self, owner, window, extras=None):
        super().__init__()
        self.owner = owner
        self.card = window
        self.setObjectName('cardToolBar')
        self.setStyleSheet('''
            QFrame#cardToolBar { background:#ffffff; border:0; border-bottom:1px solid #e2e8f0; }
            QToolButton { color:#475569; background:transparent; border:1px solid transparent;
                          border-radius:7px; padding:3px 8px; font-size:12px; }
            QToolButton:hover { background:#edf4ff; color:#1d4ed8; }
            QToolButton:checked { background:#dbeafe; border:1px solid #93c5fd;
                                  color:#1d4ed8; font-weight:600; }
            QToolButton:disabled { color:#a4afbe; }
            QLabel { color:#64748b; background:transparent; border:0; font-size:12px; }
            QSpinBox,QDoubleSpinBox,QComboBox { background:#ffffff; color:#334155;
                border:1px solid #dce3ed; border-radius:6px; padding:3px; min-height:20px; }
            QWidget#cardToolSettings { background:#f8fafc; border:1px solid #e2e8f0; border-radius:9px; }
        ''')
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 5, 10, 6)
        layout.setSpacing(5)
        self.tool_row = QHBoxLayout()
        self.tool_row.setSpacing(4)
        layout.addLayout(self.tool_row)

        tools = getattr(owner, 'tools', {}) or {}
        self.tool_buttons = {}
        for key in TOOL_KEYS:
            action = tools.get(key)
            button = QToolButton()
            if action is not None:
                button.setDefaultAction(action)
            else:
                button.setText(TOOL_LABELS[key])
                button.setEnabled(False)
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            button.setIconSize(QSize(18, 18))
            button.setFixedHeight(32)
            button.setToolTip(TOOL_LABELS[key])
            self.tool_row.addWidget(button)
            self.tool_buttons[key] = button
        _separator(self.tool_row)

        # Undo/redo must act on the CARD canvas, not the note canvas behind it,
        # so these two are local actions rather than owner.undo_action/redo_action.
        self.undo_action = QAction('撤销', self)
        self.undo_action.setIcon(tool_icon('undo'))
        self.undo_action.setToolTip('撤销卡片上的笔迹（Ctrl+Z）')
        self.undo_action.triggered.connect(lambda: window.canvas.undoEdit())
        self.redo_action = QAction('回退', self)
        self.redo_action.setIcon(tool_icon('redo'))
        self.redo_action.setToolTip('重做卡片上的笔迹（Ctrl+Y）')
        self.redo_action.triggered.connect(lambda: window.canvas.redoEdit())
        for action in (self.undo_action, self.redo_action):
            button = QToolButton()
            button.setDefaultAction(action)
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
            button.setIconSize(QSize(18, 18))
            button.setFixedSize(32, 32)
            self.tool_row.addWidget(button)
        self.tool_row.addStretch(1)
        for widget in (extras or ()):
            self.tool_row.addWidget(widget)
        try:
            window.canvas.editsChanged.connect(self.sync_undo_state)
        except (AttributeError, RuntimeError):
            pass

        self.settings = QStackedWidget()
        self.settings.setFixedHeight(40)
        layout.addWidget(self.settings)
        self.pages = {}
        for tool in TOOL_KEYS:
            page = QWidget()
            page.setObjectName('cardToolSettings')
            row = QHBoxLayout(page)
            row.setContentsMargins(9, 2, 9, 2)
            row.setSpacing(6)
            self.pages[tool] = (page, row)
            self.settings.addWidget(page)

        self._bound_spins = []
        self._build_pages()
        self.set_tool(self._current_tool())
        self.sync_controls()
        self._bind_owner_controls()

    # --------------------------------------------------------------- helpers
    def _current_tool(self):
        for key in TOOL_KEYS:
            action = (getattr(self.owner, 'tools', {}) or {}).get(key)
            if action is not None and action.isChecked():
                return key
        return getattr(self.card.canvas, 'edit_tool', 'draw') or 'draw'

    @staticmethod
    def _coerce(spin, value):
        return int(round(float(value))) if isinstance(spin, QSpinBox) else float(value)

    def _spin(self, row, label, owner_attr, minimum, maximum, step=0.1,
              decimals=1, suffix='', integer=False):
        row.addWidget(QLabel(label))
        spin = QSpinBox() if integer else QDoubleSpinBox()
        if not integer:
            spin.setDecimals(decimals)
        spin.setRange(minimum, maximum)
        spin.setSingleStep(step)
        if suffix:
            spin.setSuffix(suffix)
        spin.setFixedWidth(76)
        spin.setAccessibleName(label)
        control = getattr(self.owner, owner_attr, None)
        if hasattr(control, 'value'):
            spin.setValue(self._coerce(spin, control.value()))

        def push(value):
            if hasattr(control, 'setValue'):
                if abs(float(control.value()) - float(value)) > 1e-9:
                    control.setValue(value)
            elif hasattr(self.owner, 'update_edit_settings'):
                self.owner.update_edit_settings()
        spin.valueChanged.connect(push)
        self._bound_spins.append((spin, owner_attr))
        row.addWidget(spin)
        return spin

    def _width_shortcuts(self, row, owner_attr):
        control = getattr(self.owner, owner_attr, None)
        for value in (1., 2., 4.):
            button = QToolButton()
            button.setText('%g' % value)
            button.setCheckable(True)
            button.setFixedSize(28, 26)
            button.setToolTip('笔宽 %g' % value)
            button.clicked.connect(
                lambda checked=False, v=value, c=control: c.setValue(v)
                if hasattr(c, 'setValue') else None)
            row.addWidget(button)

    def _color_row(self, row, actions, palette, custom_action=None,
                   custom_title='自定义'):
        row.addWidget(QLabel('颜色'))
        for name, color in palette.items():
            _colored_chip(row, actions.get(color) if actions else None, color, name)
        if custom_action is not None:
            button = QToolButton()
            button.setDefaultAction(custom_action)
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
            button.setFixedHeight(26)
            button.setToolTip(custom_title)
            row.addWidget(button)
        else:
            button = QToolButton()
            button.setText(custom_title)
            button.setFixedHeight(26)
            button.setEnabled(False)
            row.addWidget(button)

    # ----------------------------------------------------------------- pages
    def _build_pages(self):
        owner = self.owner
        panel = getattr(owner, 'editor_panel', None)
        color_actions = getattr(owner, 'color_actions', {}) or {}
        highlight_actions = getattr(owner, 'highlight_color_actions', {}) or {}

        row = self.pages['draw'][1]
        self._color_row(row, color_actions, COLORS,
                        getattr(owner, 'custom_pen_action', None), '自定义画笔颜色')
        _separator(row)
        row.addWidget(QLabel('笔宽'))
        self._width_shortcuts(row, 'pen_size')
        self._spin(row, '', 'pen_size', 0.1, 128.0)
        row.addStretch(1)

        row = self.pages['shape'][1]
        shape_actions = getattr(panel, 'shape_actions', {}) or {}
        for kind in KINDS:
            action = shape_actions.get(kind)
            if action is None:
                action = QAction(LABELS[kind], self)
                action.setCheckable(True)
                action.setEnabled(False)
            action.setIcon(tool_icon('shape_' + kind))
            button = QToolButton()
            button.setDefaultAction(action)
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            button.setIconSize(QSize(16, 16))
            button.setFixedHeight(28)
            row.addWidget(button)
        _separator(row)
        shape_color_actions = getattr(panel, 'shape_color_actions', {}) or {}
        self._color_row(row, shape_color_actions, COLORS,
                        getattr(panel, 'shape_custom_action', None), '自定义图形颜色')
        _separator(row)
        row.addWidget(QLabel('笔宽'))
        self._width_shortcuts(row, 'pen_size')
        self._spin(row, '', 'pen_size', 0.1, 128.0)
        row.addStretch(1)

        row = self.pages['highlight'][1]
        self._color_row(row, highlight_actions, HIGHLIGHT_COLORS,
                        getattr(owner, 'custom_highlight_action', None), '自定义荧光色')
        _separator(row)
        row.addWidget(QLabel('笔宽'))
        self._spin(row, '', 'highlight_size', 0.1, 256.0)
        row.addWidget(QLabel('透明度'))
        self._spin(row, '', 'highlight_opacity', 10, 80, step=5,
                   suffix='%', integer=True)
        row.addStretch(1)

        row = self.pages['tape'][1]
        row.addWidget(QLabel('颜色'))
        tape_group = QActionGroup(self)
        tape_group.setExclusive(True)
        from ui.ne_tape_controls import PASTELS
        for color in PASTELS:
            action = QAction(color, self)
            action.setCheckable(True)
            tape_group.addAction(action)
            action.triggered.connect(
                lambda checked=False, c=color: self._choose_tape_color(c))
            _colored_chip(row, action, color, '胶带')
        _separator(row)
        row.addWidget(QLabel('笔宽'))
        self._spin(row, '', 'tape_size', 1.0, 128.0)
        row.addStretch(1)

        row = self.pages['erase'][1]
        row.addWidget(QLabel('擦除方式'))
        mode = QComboBox()
        mode.addItems(['局部擦除', '整笔擦除'])
        mode.setFixedWidth(110)
        owner_mode = getattr(owner, 'erase_mode', None)
        if hasattr(owner_mode, 'currentIndex'):
            mode.setCurrentIndex(owner_mode.currentIndex())
        if hasattr(owner_mode, 'setCurrentIndex'):
            mode.currentIndexChanged.connect(owner_mode.setCurrentIndex)
        row.addWidget(mode)
        _separator(row)
        row.addWidget(QLabel('大小'))
        self._spin(row, '', 'erase_size', 1, 200, step=1, integer=True)
        row.addStretch(1)

        row = self.pages['select'][1]
        row.addWidget(QLabel('框选碰到即选整笔 · 拖四角缩放 · Ctrl+Z 撤销'))
        delete_button = QToolButton()
        delete_button.setText('删除')
        delete_button.setFixedHeight(26)
        delete_button.clicked.connect(lambda: self.card.canvas.deleteSelection())
        row.addWidget(delete_button)
        clear_button = QToolButton()
        clear_button.setText('取消选择')
        clear_button.setFixedHeight(26)
        clear_button.clicked.connect(lambda: self.card.canvas.clearSelection())
        row.addWidget(clear_button)
        row.addStretch(1)

        row = self.pages['image'][1]
        row.addWidget(QLabel('图片请回主窗口插入；卡片保存题目截图与笔迹'))
        row.addStretch(1)
        row = self.pages['read'][1]
        row.addWidget(QLabel('阅读模式：按住页面拖动可平移 · B 画笔  G 图形  H 荧光笔  E 橡皮擦  S 选择'))
        row.addStretch(1)

    def _choose_tape_color(self, color):
        self.owner.current_tape_color = color
        remember = getattr(self.owner, 'remember_tool_setting', None)
        if remember is not None:
            remember('tape/color', color)
        update = getattr(self.owner, 'update_edit_settings', None)
        if update is not None:
            update()

    # ------------------------------------------------------------------ sync
    def _bind_owner_controls(self):
        for attr in ('pen_size', 'highlight_size', 'highlight_opacity',
                     'tape_size', 'erase_size'):
            control = getattr(self.owner, attr, None)
            if hasattr(control, 'valueChanged'):
                try:
                    control.valueChanged.connect(self.sync_controls)
                except (AttributeError, RuntimeError):
                    pass

    def set_tool(self, tool):
        page = self.pages.get(tool)
        if page is not None:
            self.settings.setCurrentWidget(page[0])

    def sync_undo_state(self, *args):
        canvas = self.card.canvas
        self.undo_action.setEnabled(bool(canvas.edits.can_undo))
        self.redo_action.setEnabled(bool(canvas.edits.can_redo))

    def sync_controls(self, *args):
        """Mirror main-window values back into the card without signal loops."""
        for spin, attr in self._bound_spins:
            control = getattr(self.owner, attr, None)
            if not hasattr(control, 'value'):
                continue
            value = self._coerce(spin, control.value())
            if abs(float(spin.value()) - float(value)) < 1e-9:
                continue
            spin.blockSignals(True)
            try:
                spin.setValue(value)
            finally:
                spin.blockSignals(False)
        self.set_tool(self._current_tool())
        self.sync_undo_state()
