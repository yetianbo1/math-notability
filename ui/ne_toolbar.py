"""Unified light reading controls and grouped editing tools."""
from pathlib import Path
import math
from PyQt6.QtCore import Qt,QSize,QByteArray,QPointF
from PyQt6.QtGui import QActionGroup,QColor,QIcon,QPainter,QPen,QPixmap
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtWidgets import (QFrame,QWidget,QHBoxLayout,QVBoxLayout,QToolButton,QMenu,
                             QLabel,QDoubleSpinBox,QSpinBox,QComboBox,QStackedWidget,QSizePolicy,QAbstractSpinBox,QLayout)
from ui.ne_style import style_menu
from editing.ne_edits import COLORS,HIGHLIGHT_COLORS
from ui.ne_colors import ColorPalette

ICONS={
 'draw':'<path d="m5 19 1-5L17 3l4 4-11 11-5 1Zm2-6 4 4M15 5l4 4M4 22h13"/>',
 'tape':'<path d="M3 6h18v12H3zM6 6l2 3-2 3 2 3-2 3M16 6l2 3-2 3 2 3-2 3"/>',
 'highlight':'<path d="m4 18 9-14 7 5-9 13H4v-4Zm3-2 7 5M12 7l7 5M3 23h18"/>',
 'image':'<rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="8" cy="9" r="2"/><path d="m4 18 6-6 3 3 3-4 5 7"/>',
 'lock':'<rect x="5" y="10" width="14" height="11" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3"/>',
 'unlock':'<rect x="5" y="10" width="14" height="11" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0"/>',
 'layer_up':'<path d="M5 16h14M5 20h14M12 12V3m-4 4 4-4 4 4"/>',
 'layer_down':'<path d="M5 4h14M5 8h14M12 12v9m-4-4 4 4 4-4"/>',
 'layer_front':'<path d="M3 4h18M12 19V8m-5 5 5-5 5 5M5 22h14"/>',
 'layer_back':'<path d="M3 20h18M12 5v11m-5-5 5 5 5-5M5 2h14"/>',
 'erase':'<path d="m3 14 10-11a2 2 0 0 1 3 0l5 5a2 2 0 0 1 0 3L11 21H8l-5-4a2 2 0 0 1 0-3ZM8 9l9 8M11 21h10"/>',
 'select':'<rect x="4" y="4" width="16" height="16" rx="1" stroke-dasharray="3 3"/><path d="M2 6V2h4M18 2h4v4M22 18v4h-4M6 22H2v-4"/>',
 'read':'<path d="M8 12V5a2 2 0 0 1 4 0v7-8a2 2 0 0 1 4 0v8-6a2 2 0 0 1 4 0v9c0 5-3 7-7 7-3 0-5-2-7-5l-3-4a2 2 0 0 1 3-3l2 2Z"/>',
 'undo':'<path d="m9 5-6 5 6 5M4 10h11a6 6 0 0 1 0 12" transform="translate(0 -2)"/>',
 'redo':'<path d="m15 5 6 5-6 5M20 10H9a6 6 0 0 0 0 12" transform="translate(0 -2)"/>',
 'save':'<path d="M5 3h12l4 4v14H3V3h2ZM7 3v6h10V3M7 21v-8h10v8"/>',
 'save_as':'<path d="M13 3H3v18h18V11M7 3v6h6M7 21v-7h5M16 4h6v6M22 4l-8 8"/>',
 'delete':'<path d="M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7M14 10v7"/>',
 'open':'<path d="M3 7V4h6l2 3h10v3M3 7v14h16l3-11H7L3 21"/>',
 'new_note':'<path d="M14 3H5v18h14V8l-5-5v5h5M8 14h8M12 10v8"/>',
 'import':'<path d="M12 3v12m-5-5 5 5 5-5M5 17v4h14v-4" transform="rotate(180 12 12)"/>',
 'export':'<path d="M12 3v12m-5-5 5 5 5-5M5 17v4h14v-4"/>',
 'previous':'<path d="m15 5-7 7 7 7"/>',
 'next':'<path d="m9 5 7 7-7 7"/>',
 'minus':'<path d="M5 12h14"/>',
 'plus':'<path d="M5 12h14M12 5v14"/>',
 'eyedropper':'<path d="m14 4 6 6M12 6l6 6M14 8 4-4a2 2 0 0 1 3 3l-4 4M13 9l-9 9-1 4 4-1 9-9"/>',
 'fit':'<path d="M3 5v14M21 5v14M8 8l-4 4 4 4M16 8l4 4-4 4M4 12h16"/>',
 'jump':'<path d="M4 5v8h14m-5-5 5 5-5 5M21 5v14"/>',
 'pages':'<rect x="4" y="6" width="13" height="16" rx="2"/><path d="M8 6V2h13v16h-4"/>',
 'page_add':'<path d="M13 3H4v18h16V9l-7-6v6h7M8 15h8M12 11v8"/>',
 'copy':'<rect x="8" y="8" width="13" height="13" rx="2"/><path d="M16 8V3H3v13h5"/>',
 'cut':'<circle cx="6" cy="6" r="3"/><circle cx="6" cy="18" r="3"/><path d="m8 8 12 12M8 16 20 4"/>',
 'paste':'<path d="M8 5H4v16h16V5h-4M8 3h8v4H8zM8 12h8M8 16h5"/>',
 'expand':'<path d="M9 3H3v6M15 3h6v6M3 15v6h6M21 15v6h-6M3 3l6 6M21 3l-6 6M3 21l6-6M21 21l-6-6"/>',
 'collapse':'<path d="M3 9h6V3M21 9h-6V3M3 15h6v6M21 15h-6v6"/>',
 'close':'<path d="m6 6 12 12M18 6 6 18"/>',
 'search':'<circle cx="10" cy="10" r="6"/><path d="m15 15 6 6"/>',
}

def tool_icon(name,color='#475569'):
    pixmap=QPixmap(64,64);pixmap.fill(Qt.GlobalColor.transparent)
    svg=QSvgRenderer(QByteArray(('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><g fill="none" stroke="'+color+'" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">'+ICONS[name]+'</g></svg>').encode()))
    painter=QPainter(pixmap);svg.render(painter);painter.end();return QIcon(pixmap)

def restore_color(value,default):
    """Read a QColor-compatible saved RGB value; never trust corrupt settings."""
    color=QColor(str(value))
    return color.name(QColor.NameFormat.HexRgb) if color.isValid() else default


def restore_preset(value,palette):
    color=restore_color(value,next(iter(palette.values())))
    return color if color in palette.values() else next(iter(palette.values()))


class Swatch(QToolButton):
    def __init__(self,color,parent=None):
        super().__init__(parent);self.color=color;self.setFixedSize(32,32)
    def paintEvent(self,event):
        painter=QPainter(self);painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if not self.isEnabled():painter.setOpacity(.3)
        if self.isChecked():
            painter.setPen(QPen(QColor('#2563eb'),2));painter.setBrush(Qt.BrushStyle.NoBrush);painter.drawEllipse(2,2,28,28)
        painter.setPen(QPen(QColor('#cbd5e1'),.6));painter.setBrush(QColor(self.color));painter.drawEllipse(6,6,20,20)


def restore_number(value, default, minimum, maximum):
    """Ignore corrupt/out-of-range saved tool preferences."""
    try:
        number = float(value)
        if not isinstance(value, bool) and math.isfinite(number) and minimum <= number <= maximum:
            return number
    except (TypeError, ValueError, OverflowError):
        pass
    return default


class FavoriteSwatches(QWidget):
    """Show saved colors beside the defaults, yielding room to the width controls."""
    def __init__(self,palette,current,select,parent=None,edit=None):
        super().__init__(parent)
        self.palette,self.current,self.select=palette,current,select
        self.edit=edit
        self.buttons=[]
        self.row=QHBoxLayout(self);self.row.setContentsMargins(0,0,0,0);self.row.setSpacing(8)
        self.row.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        self.setFixedHeight(34)
        self.setSizePolicy(QSizePolicy.Policy.Preferred,QSizePolicy.Policy.Fixed)
        self.palette.changed.connect(self.rebuild)
        self.rebuild()

    def sizeHint(self):
        return QSize(max(0,min(len(self.palette.colors),8)*40-8),34)

    def minimumSizeHint(self):
        return QSize(0,34)

    def rebuild(self):
        while self.row.count():
            widget=self.row.takeAt(0).widget();widget.hide();widget.deleteLater()
        self.buttons=[]
        self.shown_colors=self.display_colors()
        for color in self.shown_colors:
            button=Swatch(color,self);button.setCheckable(True)
            button.setAccessibleName('常用颜色 '+color)
            button.setToolTip(color+' · 右键更改或删除')
            button.clicked.connect(lambda checked=False,c=color:self.select(c))
            button.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            button.customContextMenuRequested.connect(lambda point,b=button,c=color:self.delete_menu(b,c,point))
            self.buttons.append(button);self.row.addWidget(button)
        self.setMaximumWidth(self.sizeHint().width())
        self.setVisible(bool(self.buttons))
        self.updateGeometry()
        self.sync_selection()

    def display_colors(self):
        current=self.current()
        capacity=max(1,min(8,(self.width()+8)//40))
        index=self.palette.colors.index(current) if current in self.palette.colors else 0
        start=max(0,index-capacity+1)
        return self.palette.colors[start:start+8]

    def delete_menu(self,button,color,point):
        menu=QMenu(button);style_menu(menu)
        menu.setStyleSheet(menu.styleSheet()+'QMenu::item{color:#ef4444;}QMenu::item:selected{background:#fff1f2;}')
        edit_action=menu.addAction('更改颜色') if self.edit else None
        action=menu.addAction('删除')
        action.setIcon(tool_icon('delete','#ef4444'))
        chosen=menu.exec(button.mapToGlobal(point))
        if chosen==action:self.palette.remove(color)
        elif edit_action is not None and chosen==edit_action:self.edit(color)

    def sync_selection(self):
        if self.shown_colors!=self.display_colors():
            self.rebuild();return
        for index,button in enumerate(self.buttons):
            button.setChecked(button.color==self.current())
            button.setVisible(index*40+32<=self.width())

    def resizeEvent(self,event):
        super().resizeEvent(event)
        self.sync_selection()

class CustomColorButton(QToolButton):
    """Always-visible custom-color button with a preview of the remembered color."""
    def __init__(self,color,parent=None):
        super().__init__(parent)
        self.color=color
        self.setFixedSize(34,34)
        self.setToolTip('添加颜色：预设色板、自定义色板和屏幕吸色；右键色块可更改或删除')
        self.setAccessibleName('自定义颜色')

    def setColor(self,color):
        self.color=color
        self.update()

    def paintEvent(self,event):
        painter=QPainter(self);painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if not self.isEnabled():painter.setOpacity(.3)
        painter.setPen(Qt.PenStyle.NoPen);painter.setBrush(QColor('#edf0f3'))
        painter.drawEllipse(3,3,28,28)
        painter.setPen(QPen(QColor('#475569'),1.6))
        painter.drawLine(11,17,23,17);painter.drawLine(17,11,17,23)


class WidthButton(QToolButton):
    def __init__(self,width,parent=None):
        super().__init__(parent);self.value=width;self.setCheckable(True);self.setFixedSize(34,34)
        self.setToolTip(f'笔宽 {width:g}');self.setAccessibleName(f'笔宽 {width:g}')
    def paintEvent(self,event):
        painter=QPainter(self);painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if not self.isEnabled():painter.setOpacity(.3)
        painter.setPen(QPen(QColor('#2563eb') if self.isChecked() else QColor('#e2e8f0'),1.5))
        painter.setBrush(QColor('#eaf1ff') if self.isChecked() else QColor('#ffffff'));painter.drawRoundedRect(2,2,30,30,8,8)
        radius=1.5+self.value;painter.setPen(Qt.PenStyle.NoPen);painter.setBrush(QColor('#475569'))
        painter.drawEllipse(QPointF(self.rect().center()),radius,radius)


class ReadingPanel(QFrame):
    def __init__(self,owner):
        super().__init__();self.setObjectName('readingPanel');self.setMinimumWidth(680)
        self.setSizePolicy(QSizePolicy.Policy.Expanding,QSizePolicy.Policy.Preferred)
        self.setStyleSheet('''
            QFrame#readingPanel { background:#ffffff; border:0; border-bottom:1px solid #e2e8f0; }
            QFrame#controlGroup { background:#f7f9fd; border:1px solid #e2e8f0; border-radius:10px; }
            QToolButton { color:#475569; background:transparent; border:0; border-radius:6px; padding:4px; font-size:12px; }
            QToolButton:hover { background:#e9f1ff; color:#1d4ed8; }
            QToolButton:pressed { background:#dbeafe; }
            QToolButton:focus { border:1px solid #93c5fd; }
            QToolButton:disabled { color:#a4afbe; }
            QToolButton#openFile { background:#ffffff; border:1px solid #cbd5e1; font-weight:600; border-radius:9px; }
            QToolButton#openFile:hover { background:#f1f5f9; border-color:#94a3b8; }
            QToolButton#newNote { background:#ffffff; border:1px solid #cbd5e1; font-weight:600; border-radius:9px; }
            QToolButton#newNote:hover { background:#f1f5f9; border-color:#94a3b8; }
            QToolButton#newNote:disabled { background:#f5f7fb; color:#a4afbe; border-color:#e7ecf3; }
            QToolButton#createPdfNote { background:#eef4ff; border:1px solid #b7d0ff; color:#1d4ed8; border-radius:9px; font-weight:600; }
            QToolButton#createPdfNote:hover { background:#dbeafe; border-color:#93c5fd; }
            QToolButton#createPdfNote:disabled { background:#f5f7fb; color:#a4afbe; border-color:#e7ecf3; }
            QToolButton#exportFile { background:#2563eb; border:1px solid #2563eb; color:#ffffff; border-radius:9px; font-weight:600; }
            QToolButton#exportFile:hover { background:#1d4ed8; border-color:#1d4ed8; }
            QToolButton#exportFile:disabled { background:#f5f7fb; color:#a4afbe; border-color:#e7ecf3; }
            QLabel { color:#64748b; background:transparent; border:0; font-size:12px; }
            QLabel#zoomValue { color:#334155; font-weight:600; }
            QSpinBox { color:#334155; background:#ffffff; border:1px solid #dce3ed; border-radius:5px; padding:2px 4px; font-size:12px; }
            QSpinBox:focus { border-color:#93b4f5; }
        ''')
        row=QHBoxLayout(self);row.setContentsMargins(18,8,18,8);row.setSpacing(12)
        def action(label,callback,key,icon):
            result=owner.action(self,label,callback,key);result.setIcon(tool_icon(icon));result.setIconVisibleInMenu(False)
            result.setToolTip(label+'（'+key+'）');return result
        def button(layout,command,icon_only=False):
            b=QToolButton();b.setDefaultAction(command);b.setIconSize(QSize(18,18))
            b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly if icon_only else Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            b.setFixedHeight(30);b.setFixedWidth(30 if icon_only else 92);layout.addWidget(b);return b
        def group():
            frame=QFrame();frame.setObjectName('controlGroup');layout=QHBoxLayout(frame)
            layout.setContentsMargins(4,3,4,3);layout.setSpacing(2);row.addWidget(frame);return layout
        # The file menu creates the shared New Note action later in build_menus().
        self.new_note_button=QToolButton(self)
        self.new_note_button.setObjectName('newNote')
        self.new_note_button.setText('新建笔记')
        self.new_note_button.setIcon(tool_icon('new_note'))
        self.new_note_button.setIconSize(QSize(18,18))
        self.new_note_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.new_note_button.setFixedSize(102,38)
        self.new_note_button.setAccessibleName('新建笔记')
        row.addWidget(self.new_note_button)
        owner.open_action=action('打开文件',owner.choose_file,'Ctrl+O','open')
        open_button=button(row,owner.open_action);open_button.setObjectName('openFile');open_button.setFixedHeight(38)
        # Bind the PDF-to-NTB QAction after build_menus(), exactly like Export.
        self.pdf_note_button=QToolButton(self)
        self.pdf_note_button.setText('导入 PDF')
        self.pdf_note_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.pdf_note_button.setIcon(tool_icon('import'))
        self.pdf_note_button.setIconSize(QSize(18,18))
        self.pdf_note_button.setFixedHeight(38)
        row.addWidget(self.pdf_note_button)
        # The export actions are created later by build_menus(). Create the visible
        # toolbar control now; bind the SAME QActions after the menus exist.
        self.export_button=QToolButton(self)
        self.export_button.setObjectName('exportFile')
        self.export_button.setText('导出')
        self.export_button.setIcon(tool_icon('export','#ffffff'))
        self.export_button.setIconSize(QSize(18,18))
        self.export_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.export_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.export_button.setFixedSize(92,38)
        self.export_button.setToolTip('导出当前笔记：PDF 或 NOTE（NTB → NOTE 为实验性）')
        self.export_button.setAccessibleName('导出文件，展开 PDF 和 NOTE 选项')
        self.export_button.setEnabled(False)
        row.addWidget(self.export_button)
        row.addStretch()
        pager=group()
        owner.previous_action=action('上一页',lambda:owner.go_page(owner.page.value()-1),'Alt+Left','previous');button(pager,owner.previous_action,True)
        owner.page=QSpinBox();owner.page.setRange(1,1);owner.page.setFixedSize(48,28);owner.page.setKeyboardTracking(False)
        owner.page.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons);owner.page.setAlignment(Qt.AlignmentFlag.AlignCenter)
        owner.page.setAccessibleName('当前页码');owner.page.setToolTip('输入页码后按 Enter 跳转（Ctrl+L）');owner.page.setEnabled(False);pager.addWidget(owner.page)
        owner.count_label=QLabel(' / 0  ');owner.count_label.setMinimumWidth(42);pager.addWidget(owner.count_label)
        owner.page.lineEdit().returnPressed.connect(lambda:owner.go_page(owner.page.value()))
        jump=owner.action(self,'跳转',lambda:owner.go_page(owner.page.value()));jump.setIcon(tool_icon('jump'));jump.setToolTip('跳转到输入的页码')
        button(pager,jump,True)
        owner.next_action=action('下一页',lambda:owner.go_page(owner.page.value()+1),'Alt+Right','next');button(pager,owner.next_action,True)
        row.addStretch()
        zoom=group()
        owner.zoom_out_action=action('缩小',lambda:owner.change_zoom(1/1.2),'Ctrl+-','minus');button(zoom,owner.zoom_out_action,True)
        owner.zoom_label=QLabel('100%');owner.zoom_label.setObjectName('zoomValue');owner.zoom_label.setFixedWidth(48)
        owner.zoom_label.setAlignment(Qt.AlignmentFlag.AlignCenter);zoom.addWidget(owner.zoom_label)
        owner.zoom_in_action=action('放大',lambda:owner.change_zoom(1.2),'Ctrl+=','plus');button(zoom,owner.zoom_in_action,True)
        line=QFrame();line.setFixedSize(1,16);line.setStyleSheet('background:#dce3ed;border:0;');zoom.addWidget(line)
        owner.fit_action=action('适应宽度',owner.fit,'Ctrl+0','fit');button(zoom,owner.fit_action)

    def bind_export_actions(self,owner):
        """Share the file menu's actual export actions; do not create duplicate callbacks."""
        menu=QMenu(self.export_button)
        menu.addAction(owner.export_pdf_action)
        menu.addAction(owner.export_note_action)
        style_menu(menu)
        self.export_button.setMenu(menu)
        self.export_menu=menu  # retain QMenu for the life of the toolbar


class EditorPanel(QFrame):
    def __init__(self,owner):
        super().__init__();self.owner=owner;self.setObjectName('editorPanel');self.setMinimumWidth(680)
        self.setSizePolicy(QSizePolicy.Policy.Expanding,QSizePolicy.Policy.Preferred)
        self.setStyleSheet('''
            QFrame#editorPanel { background:#ffffff; border:0; border-bottom:1px solid #e2e8f0; }
            QToolButton { color:#475569; background:transparent; border:1px solid transparent; border-radius:8px; padding:4px; font-size:12px; }
            QToolButton:hover { background:#edf4ff; color:#1d4ed8; }
            QToolButton:checked { background:#dbeafe; border:1px solid #93c5fd; color:#1d4ed8; font-weight:600; }
            QToolButton:disabled { color:#a4afbe; }
            QToolButton#primarySave { background:#2563eb; color:white; border:1px solid #2563eb; font-weight:600; }
            QToolButton#primarySave:hover { background:#1d4ed8; }
            QToolButton#primarySave:disabled { background:#e2e8f0; border-color:#e2e8f0; color:#94a3b8; }
            QToolButton#secondarySave { border:1px solid #dce3ed; background:#ffffff; }
            QToolButton#secondarySave:hover { background:#f0f4fa; }
            QLabel { color:#64748b; background:transparent; border:0; font-size:12px; }
            QSpinBox,QDoubleSpinBox,QComboBox { background:#ffffff; color:#334155; border:1px solid #dce3ed; border-radius:7px; padding:4px; min-height:22px; }
            QSpinBox:focus,QDoubleSpinBox:focus,QComboBox:focus { border-color:#93b4f5; }
            QComboBox QAbstractItemView { background:#ffffff; color:#334155; selection-background-color:#eaf1ff; selection-color:#1d4ed8; }
            QComboBox::drop-down { width:22px; border:0; border-left:1px solid #edf2f7; }
            QStackedWidget { background:transparent; border:0; }
            QWidget#toolSettings { background:#f8fafc; border:1px solid #e2e8f0; border-radius:10px; }
        ''')
        assets=Path(__file__).resolve().parent.parent/'assets'
        self.setStyleSheet(self.styleSheet()+f'''
            QAbstractSpinBox::up-button,QAbstractSpinBox::down-button {{ width:16px; background:#edf2f7; border:0; }}
            QAbstractSpinBox::up-arrow {{ image:url("{(assets/'chevron-up.svg').as_posix()}"); width:10px; height:7px; }}
            QAbstractSpinBox::down-arrow,QComboBox::down-arrow {{ image:url("{(assets/'chevron-down.svg').as_posix()}"); width:10px; height:7px; }}
        ''')
        layout=QVBoxLayout(self);layout.setContentsMargins(18,8,18,10);layout.setSpacing(8)
        top=QHBoxLayout();top.setSpacing(6);layout.addLayout(top)
        group=QActionGroup(self);group.setExclusive(True);owner.tools={}
        for tool,label,key in [('draw','画笔','B'),('highlight','荧光笔','H'),('tape','遮字胶带','T'),('erase','橡皮擦','E'),('select','选择','S'),('image','图片','I'),('read','阅读','V')]:
            action=owner.action(self,label,lambda checked=False,t=tool:owner.select_tool(t),key)
            action.setCheckable(True);action.setToolTip(f'{label}（{key}）');action.setIcon(tool_icon(tool));group.addAction(action);owner.tools[tool]=action
            self.tool_button(top,action)
        owner.tools['read'].setChecked(True)
        self.separator(top)
        owner.undo_action=owner.action(self,'撤销',lambda:owner.canvas.undoEdit() if hasattr(owner.canvas,'undoEdit') else None,'Ctrl+Z')
        owner.redo_action=owner.action(self,'回退',lambda:owner.canvas.redoEdit() if hasattr(owner.canvas,'redoEdit') else None,'Ctrl+Y')
        owner.redo_action.setShortcuts(['Ctrl+Y','Ctrl+Shift+Z'])
        owner.save_action=owner.action(self,'保存',lambda:owner.save_ntb(),'Ctrl+S')
        owner.save_action.setToolTip('保存到当前文件（Ctrl+S）')
        owner.save_as_action=owner.action(self,'另存为',lambda:owner.save_ntb(save_as=True),'Ctrl+Shift+S')
        owner.save_as_action.setToolTip('选择新文件名保存（Ctrl+Shift+S）')
        for action,name in [(owner.undo_action,'undo'),(owner.redo_action,'redo'),(owner.save_action,'save'),(owner.save_as_action,'save_as')]:
            if name=='save':
                top.addStretch()
                self.page_button=QToolButton();self.page_button.setObjectName('pageManagerToggle')
                self.page_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
                self.page_button.setIconSize(QSize(20,20));self.page_button.setFixedSize(38,38)
                top.addWidget(self.page_button)
            action.setIcon(tool_icon(name,'#ffffff' if name=='save' else '#475569'))
            button=self.tool_button(top,action)
            if name in ('undo','redo'):
                button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly);button.setFixedWidth(36)
                button.setToolTip(action.text()+'（'+action.shortcut().toString()+'）')
            else:
                button.setFixedWidth(86 if name=='save_as' else 78)
                button.setObjectName('primarySave' if name=='save' else 'secondarySave')
        owner.undo_action.setEnabled(False);owner.redo_action.setEnabled(False)
        self.settings=QStackedWidget();self.settings.setFixedHeight(46);layout.addWidget(self.settings)
        self.pages={}
        for tool in ('draw','highlight','tape','erase','select','image','read'):
            page=QWidget();page.setObjectName('toolSettings');row=QHBoxLayout(page);row.setContentsMargins(12,5,12,5);row.setSpacing(8)
            self.pages[tool]=(page,row);self.settings.addWidget(page)
        row=self.pages['draw'][1];colors=QActionGroup(self);colors.setExclusive(True)
        owner.color_actions={}
        owner.custom_pen_color=restore_color(owner.color_settings.value('draw/custom_color',COLORS['红']),COLORS['红'])
        pen_preset=restore_preset(owner.color_settings.value('draw/preset_color',COLORS['红']),COLORS)
        owner.pen_color_is_custom=owner.color_settings.value('draw/use_custom',False,type=bool)
        owner.current_color=owner.custom_pen_color if owner.pen_color_is_custom else pen_preset
        row.addWidget(QLabel('颜色'))
        for name,color in COLORS.items():
            action=owner.action(self,name,lambda checked=False,c=color:owner.select_color(c))
            action.setCheckable(True);action.setChecked(not owner.pen_color_is_custom and pen_preset==color)
            colors.addAction(action);owner.color_actions[color]=action
        owner.pen_palette=ColorPalette(owner.color_settings,'draw',self)
        self.pen_favorites=FavoriteSwatches(owner.pen_palette,
            lambda:owner.current_color,
            lambda color:owner.apply_custom_color('draw',color),self,
            edit=lambda color:owner.choose_custom_color('draw',replace_color=color))
        row.addWidget(self.pen_favorites)
        owner.custom_pen_action=owner.action(self,'自定义画笔颜色',lambda:owner.choose_custom_color('draw'))
        owner.custom_pen_action.setCheckable(True);colors.addAction(owner.custom_pen_action)
        owner.custom_pen_action.setChecked(owner.pen_color_is_custom)
        self.custom_pen_button=CustomColorButton(owner.custom_pen_color)
        self.custom_pen_button.setDefaultAction(owner.custom_pen_action);row.addWidget(self.custom_pen_button)
        self.separator(row);row.addWidget(QLabel('笔宽'))
        self.width_buttons=[]
        for value in (1.,2.,4.):
            button=WidthButton(value);button.clicked.connect(lambda checked=False,v=value:owner.pen_size.setValue(v));self.width_buttons.append(button);row.addWidget(button)
        owner.pen_size=QDoubleSpinBox();owner.pen_size.setDecimals(1);owner.pen_size.setRange(.1,128);owner.pen_size.setSingleStep(.1);owner.pen_size.setValue(restore_number(owner.color_settings.value('draw/width',2),2,.1,128));owner.pen_size.setFixedWidth(72)
        owner.pen_size.setAccessibleName('画笔宽度');owner.pen_size.setToolTip('笔宽 0.1～128；将鼠标移到页面即可按实际比例预览笔尖大小');row.addWidget(owner.pen_size);row.addStretch()
        owner.pen_size.valueChanged.connect(owner.update_edit_settings);owner.pen_size.valueChanged.connect(self.sync_width);self.sync_width(owner.pen_size.value())
        row=self.pages['highlight'][1]
        highlight_group=QActionGroup(self);highlight_group.setExclusive(True)
        owner.highlight_color_actions={}
        owner.custom_highlight_color=restore_color(owner.color_settings.value('highlight/custom_color',HIGHLIGHT_COLORS['黄']),HIGHLIGHT_COLORS['黄'])
        highlight_preset=restore_preset(owner.color_settings.value('highlight/preset_color',HIGHLIGHT_COLORS['黄']),HIGHLIGHT_COLORS)
        owner.highlight_color_is_custom=owner.color_settings.value('highlight/use_custom',False,type=bool)
        owner.current_highlight_color=owner.custom_highlight_color if owner.highlight_color_is_custom else highlight_preset
        row.addWidget(QLabel('颜色'))
        for name,color in HIGHLIGHT_COLORS.items():
            action=owner.action(self,name,lambda checked=False,c=color:owner.select_highlight_color(c))
            action.setCheckable(True);action.setChecked(not owner.highlight_color_is_custom and highlight_preset==color)
            highlight_group.addAction(action);owner.highlight_color_actions[color]=action
        owner.highlight_palette=ColorPalette(owner.color_settings,'highlight',self)
        self.highlight_favorites=FavoriteSwatches(owner.highlight_palette,
            lambda:owner.current_highlight_color,
            lambda color:owner.apply_custom_color('highlight',color),self,
            edit=lambda color:owner.choose_custom_color('highlight',replace_color=color))
        row.addWidget(self.highlight_favorites)
        owner.custom_highlight_action=owner.action(self,'自定义荧光笔颜色',lambda:owner.choose_custom_color('highlight'))
        owner.custom_highlight_action.setCheckable(True);highlight_group.addAction(owner.custom_highlight_action)
        owner.custom_highlight_action.setChecked(owner.highlight_color_is_custom)
        self.custom_highlight_button=CustomColorButton(owner.custom_highlight_color)
        self.custom_highlight_button.setDefaultAction(owner.custom_highlight_action);row.addWidget(self.custom_highlight_button)
        self.separator(row);row.addWidget(QLabel('笔宽'))
        owner.highlight_size=QDoubleSpinBox();owner.highlight_size.setDecimals(1)
        owner.highlight_size.setRange(.1,256);owner.highlight_size.setSingleStep(.1)
        owner.highlight_size.setValue(restore_number(owner.color_settings.value('highlight/width',16),16,.1,256));owner.highlight_size.setFixedWidth(70)
        owner.highlight_size.setAccessibleName('荧光笔宽度');owner.highlight_size.setToolTip('笔宽 0.1～256；页面内显示与缩放同步的半透明笔尖预览');row.addWidget(owner.highlight_size)
        self.separator(row);row.addWidget(QLabel('透明度'))
        owner.highlight_opacity=QSpinBox();owner.highlight_opacity.setRange(10,80)
        owner.highlight_opacity.setSingleStep(5);owner.highlight_opacity.setValue(int(restore_number(owner.color_settings.value('highlight/opacity',42),42,10,80)))
        owner.highlight_opacity.setSuffix('%');owner.highlight_opacity.setFixedWidth(74)
        owner.highlight_opacity.setAccessibleName('荧光笔透明度');row.addWidget(owner.highlight_opacity)
        row.addStretch()
        owner.highlight_size.valueChanged.connect(owner.update_edit_settings)
        owner.highlight_opacity.valueChanged.connect(owner.update_edit_settings)
        from ui.ne_tape_controls import build_controls
        build_controls(owner,self)
        row=self.pages['erase'][1];row.addWidget(QLabel('擦除方式'))
        owner.erase_mode=QComboBox();owner.erase_mode.addItems(['局部擦除','整笔擦除']);owner.erase_mode.setFixedWidth(120)
        owner.erase_mode.setCurrentIndex(1 if owner.color_settings.value('erase/mode','partial')=='stroke' else 0)
        owner.erase_mode.setToolTip('局部擦除：减去接触区域，剩余片段可独立操作\n整笔擦除：删除接触的完整笔画；混合接触时优先擦荧光笔')
        row.addWidget(owner.erase_mode);self.separator(row);row.addWidget(QLabel('大小'))
        owner.erase_size=QSpinBox();owner.erase_size.setRange(1,200);owner.erase_size.setValue(int(restore_number(owner.color_settings.value('erase/width',18),18,1,200)));owner.erase_size.setFixedWidth(80);owner.erase_size.setAccessibleName('橡皮擦大小');owner.erase_size.setToolTip('擦除直径（页面单位）；圆圈随页面缩放');row.addWidget(owner.erase_size)
        row.addStretch();row.addWidget(QLabel('仅擦除笔迹 · Ctrl+Z 撤销 · Esc 取消'))
        owner.erase_mode.currentIndexChanged.connect(owner.update_edit_settings);owner.erase_size.valueChanged.connect(owner.update_edit_settings)
        # Restore before connecting persistence, so construction cannot overwrite
        # another preference with a control's temporary default.
        for control,key in ((owner.pen_size,'draw/width'),(owner.highlight_size,'highlight/width'),
                            (owner.highlight_opacity,'highlight/opacity'),(owner.erase_size,'erase/width')):
            control.valueChanged.connect(lambda value,k=key:owner.remember_tool_setting(k,value))
        owner.erase_mode.currentIndexChanged.connect(
            lambda index:owner.remember_tool_setting('erase/mode','stroke' if index==1 else 'partial'))
        row=self.pages['select'][1];self.selection_label=QLabel('框选碰到即选整笔');row.addWidget(self.selection_label);row.addStretch()
        self.selection_hint=QLabel('选框内拖动 · Shift 追加 · Esc 取消');row.addWidget(self.selection_hint)
        self.delete_action=owner.action(self,'删除',lambda:owner.canvas.deleteSelection() if hasattr(owner.canvas,'deleteSelection') else None)
        self.delete_action.setIcon(tool_icon('delete'));self.delete_action.setToolTip('删除选中笔迹（Delete）')
        button=QToolButton();button.setDefaultAction(self.delete_action);button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon);button.setIconSize(QSize(20,20));row.addWidget(button)
        self.clear_action=owner.action(self,'取消选择',lambda:owner.canvas.clearSelection() if hasattr(owner.canvas,'clearSelection') else None)
        button=QToolButton();button.setDefaultAction(self.clear_action);row.addWidget(button)
        # This is the *same* secondary settings row used by the eraser, pen,
        # and highlighter: images are no longer a separate QMainWindow toolbar.
        self.image_settings_row=self.pages['image'][1]
        self.image_settings_row.addWidget(QLabel('图片管理'))
        row=self.pages['read'][1];row.addWidget(QLabel('阅读模式'));row.addStretch();row.addWidget(QLabel('B 画笔    H 荧光笔    T 胶带    E 橡皮擦    S 选择    I 图片    Ctrl+Z 撤销'))
        self.selection_changed(0);self.set_tool('read')

    def tool_button(self,row,action):
        button=QToolButton();button.setDefaultAction(action);button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        button.setIconSize(QSize(20,20));button.setFixedSize(94 if action.text()=='遮字胶带' else 70,38);row.addWidget(button);return button

    def separator(self,row):
        line=QFrame();line.setFixedSize(1,22);line.setStyleSheet('background:#e2e8f0;border:0;');row.addWidget(line)

    def bind_selection_actions(self,owner):
        """Share the shape actions with Tools > Selection, as a two-button strip."""
        row=self.pages['select'][1];self.selection_shape_buttons={}
        for position,key in enumerate(('lasso','rectangle')):
            button=QToolButton(self);button.setDefaultAction(owner.selection_shape_actions[key])
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
            button.setFixedSize(108,34)
            button.setStyleSheet('QToolButton {border:1px solid transparent;border-radius:17px;background:#f0f2f5;color:#475569;}'
                'QToolButton:checked {border:1px solid #5486db;background:#edf3ff;color:#1d4ed8;}'
                'QToolButton:hover {background:#e4ecfa;}')
            row.insertWidget(position,button);self.selection_shape_buttons[key]=button

    def bind_image_actions(self,owner):
        """Populate the image settings row once shared menu QActions exist."""
        row=self.image_settings_row
        def image_button(action,width):
            button=QToolButton(self)
            button.setDefaultAction(action)
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            button.setIconSize(QSize(17,17))
            button.setFixedHeight(34)
            button.setMinimumWidth(width)
            button.setToolTip(action.toolTip())
            row.addWidget(button)
            return button
        self.image_buttons={}
        self.image_buttons['insert']=image_button(owner.insert_image_action,90)
        self.separator(row)
        for key,width in (('toggle_lock',92),('up',90),('down',90),
                          ('front',102),('back',130),('delete',85)):
            self.image_buttons[key]=image_button(owner.image_actions[key],width)
        self.image_status_label=QLabel('状态：未选择')
        self.image_status_label.setToolTip('显示选中图片的原生锁定状态。官方锁定图片需要先解锁才能移动或缩放。')
        row.addWidget(self.image_status_label)
        row.addStretch()

    def set_tool(self,tool):self.settings.setCurrentWidget(self.pages[tool][0])
    def sync_width(self,value):
        for button in self.width_buttons:button.setChecked(abs(button.value-value)<.001)
    def selection_changed(self,count):
        self.selection_label.setText(f'已选 {count} 段笔迹' if count else '圈选笔迹')
        self.delete_action.setEnabled(count>0);self.clear_action.setEnabled(count>0)
