"""Study tape palette, thickness, native pattern and reveal controls."""
from PyQt6.QtCore import Qt, QSize, QRectF
from PyQt6.QtGui import QColor, QIcon, QPixmap, QPainter, QPainterPath
from PyQt6.QtWidgets import QColorDialog, QDoubleSpinBox, QLabel, QToolButton, QMenu, QWidget, QWidgetAction, QGridLayout
from formats.ne_tape import PATTERNS
from rendering.ne_tape_patterns import paint_tape

PASTELS = ('#e5f5ff','#f5bdb6','#9fd2d3','#ffd1a0','#ddd7eb')
COLORS = ('#ffff00','#ffaa00','#f23b28','#ff00dd','#aaff00','#66bb44','#aa00cc','#663399',
          '#008855','#007aff','#204ac8','#321088','#ffffff','#999999','#000000','#996633')


def pattern_icon(pattern, color):
    pix = QPixmap(96,28); pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix); painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    path = QPainterPath(); path.addRoundedRect(QRectF(1,2,94,24),10,10)
    paint_tape(painter,path,color,28,pattern); painter.end()
    return QIcon(pix)


def build_controls(owner, panel):
    from ui.ne_toolbar import restore_color, restore_number, Swatch
    row = panel.pages['tape'][1]; row.addWidget(QLabel('学习'))
    owner.current_tape_color = restore_color(owner.color_settings.value('tape/color',PASTELS[0]),PASTELS[0])
    owner.current_tape_pattern = int(restore_number(owner.color_settings.value('tape/pattern',0),0,0,8))
    swatches = []; pattern_actions = []
    def refresh():
        for button in swatches: button.setChecked(button.color == owner.current_tape_color)
        for value, action in pattern_actions:
            action.setChecked(value == owner.current_tape_pattern)
            action.setIcon(pattern_icon(value,owner.current_tape_color))
        pattern_button.setIcon(pattern_icon(owner.current_tape_pattern,owner.current_tape_color))
        pattern_button.setText(dict(PATTERNS)[owner.current_tape_pattern])
    def choose(rgb):
        owner.current_tape_color = rgb
        owner.remember_tool_setting('tape/color',rgb)
        refresh(); owner.update_edit_settings()
    for color in PASTELS:
        button = Swatch(color); button.setCheckable(True); button.setAccessibleName('胶带颜色 '+color)
        button.clicked.connect(lambda checked=False,c=color:choose(c))
        swatches.append(button); row.addWidget(button)
    more = QToolButton(); more.setText('+'); more.setAccessibleName('更多胶带颜色')
    menu = QMenu(more); panel.tape_color_menu = menu
    grid_widget = QWidget(); grid = QGridLayout(grid_widget)
    for i,color in enumerate(COLORS):
        button = Swatch(color); button.setAccessibleName('胶带颜色 '+color)
        button.clicked.connect(lambda checked=False,c=color:(choose(c),menu.close()))
        grid.addWidget(button,i//8,i%8)
    widget_action = QWidgetAction(menu); widget_action.setDefaultWidget(grid_widget); menu.addAction(widget_action)
    def custom():
        color = QColorDialog.getColor(QColor(owner.current_tape_color),owner,'遮字胶带颜色')
        if color.isValid(): choose(color.name())
    menu.addAction('自定义颜色…',custom)
    more.setMenu(menu); more.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup); row.addWidget(more)
    row.addWidget(QLabel('  粗细'))
    sizes = []
    for width,radius in ((14.,3),(28.7,7),(43.,11)):
        pix = QPixmap(28,28); pix.fill(Qt.GlobalColor.transparent)
        p = QPainter(pix); p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen); p.setBrush(QColor('#64748b')); p.drawEllipse(QRectF(14-radius,14-radius,radius*2,radius*2)); p.end()
        button = QToolButton(); button.setIcon(QIcon(pix)); button.setIconSize(QSize(28,28)); button.setCheckable(True)
        button.setToolTip(str(width)); button.setAccessibleName('胶带粗细 '+str(width))
        button.clicked.connect(lambda checked=False,w=width:owner.tape_size.setValue(w))
        sizes.append((width,button)); row.addWidget(button)
    owner.tape_size = QDoubleSpinBox(); owner.tape_size.setRange(2,256); owner.tape_size.setDecimals(1)
    owner.tape_size.setValue(restore_number(owner.color_settings.value('tape/width',28.7),28.7,2,256))
    owner.tape_size.setFixedWidth(70); owner.tape_size.setAccessibleName('遮字胶带宽度'); row.addWidget(owner.tape_size)
    def size_changed(value):
        for width,button in sizes: button.setChecked(abs(width-value)<.05)
        owner.remember_tool_setting('tape/width',value); owner.update_edit_settings()
    owner.tape_size.valueChanged.connect(size_changed)
    for width,button in sizes: button.setChecked(abs(width-owner.tape_size.value())<.05)
    row.addWidget(QLabel('  样式'))
    pattern_button = QToolButton(); pattern_button.setIconSize(QSize(72,24)); pattern_button.setAccessibleName('胶带样式')
    pattern_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
    patterns = QMenu(pattern_button); panel.tape_pattern_menu = patterns
    def set_pattern(value):
        owner.current_tape_pattern = value; owner.remember_tool_setting('tape/pattern',value)
        refresh(); owner.update_edit_settings()
    for value,label in PATTERNS:
        action = patterns.addAction(label); action.setCheckable(True)
        action.triggered.connect(lambda checked=False,v=value:set_pattern(v)); pattern_actions.append((value,action))
    pattern_button.setMenu(patterns); pattern_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
    row.addWidget(pattern_button); refresh()
    owner.tape_reveal_action = owner.action(panel,'临时揭开全部胶带',lambda:None,'Ctrl+Shift+T')
    owner.tape_reveal_action.setCheckable(True)
    owner.tape_reveal_action.setToolTip('再次点击恢复遮盖；保存和导出保留遮盖（Ctrl+Shift+T）')
    owner.tape_reveal_action.toggled.connect(lambda checked:owner.canvas.setTapeRevealed(checked) if hasattr(owner.canvas,'setTapeRevealed') else None)
    button = QToolButton(); button.setDefaultAction(owner.tape_reveal_action); row.addWidget(button); row.addStretch()
