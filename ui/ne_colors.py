"""Persistent per-tool palettes and a screen picker using logical screen coordinates."""
import json
import re
from PyQt6.QtCore import Qt, QPoint, QRect, QTimer, QObject, pyqtSignal, QEvent, QEventLoop
from PyQt6.QtGui import QColor, QPainter, QPen, QLinearGradient, QCursor, QGuiApplication
from PyQt6.QtWidgets import (QWidget, QDialog, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QPushButton, QToolButton, QLineEdit, QSlider,
    QScrollArea, QMenu, QStackedWidget, QApplication)

BUILTIN_COLORS = ('#ffff00','#ff9900','#f03026','#ff00cc','#99ff00','#60b644','#aa00cc','#70309a',
                  '#008556','#007aff','#2555b6','#000099','#ffffff','#999999','#000000','#996633')


class ColorChip(QToolButton):
    def __init__(self,color,current=lambda:None,remove=None,edit=None,parent=None):
        super().__init__(parent)
        self.color,self.current,self.remove,self.edit=color,current,remove,edit
        self.setFixedSize(38,38)
        self.setAccessibleName('颜色 '+color)
        self.setToolTip(color+(' · 右键删除' if remove else ''))

    def paintEvent(self,event):
        painter=QPainter(self);painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor('#d1d5db'),1));painter.setBrush(QColor(self.color))
        painter.drawEllipse(4,4,30,30)
        if self.color==self.current():
            color=QColor(self.color)
            painter.setPen(QPen(QColor('#111827') if color.lightnessF()>.72 else QColor('white'),2.4))
            painter.drawLine(12,19,17,24);painter.drawLine(17,24,26,14)

    def contextMenuEvent(self,event):
        if not self.remove:return
        menu=QMenu(self)
        menu.setStyleSheet('QMenu{background:white;border:1px solid #e2e8f0;border-radius:12px;padding:8px;}'
                           'QMenu::item{color:#ef4444;padding:10px 34px;}QMenu::item:selected{background:#fff1f2;}')
        edit_action=menu.addAction('更改颜色') if self.edit else None
        action=menu.addAction('删除')
        chosen=menu.exec(event.globalPos())
        if chosen==action:self.remove(self.color)
        elif edit_action is not None and chosen==edit_action:self.edit(self.color)


def rgb(value):
    """Only accept explicit RGB hex, never QColor's surprising named-color aliases."""
    if not isinstance(value, str) or not re.fullmatch(r'#[0-9a-fA-F]{6}', value):
        return None
    return value.lower()


class ColorPalette(QObject):
    changed = pyqtSignal()

    def __init__(self, settings, tool, parent=None):
        super().__init__(parent)
        if tool not in ('draw', 'highlight'):
            raise ValueError('Unknown color tool')
        from editing.ne_edits import COLORS,HIGHLIGHT_COLORS
        defaults=list((COLORS if tool=='draw' else HIGHLIGHT_COLORS).values())
        self.settings, self.key = settings, tool + '/palette_colors'
        first_use=not settings.contains(self.key)
        try:
            values = json.loads(str(settings.value(tool+'/favorite_colors' if first_use else self.key, '[]')))
        except (ValueError, TypeError):
            values = defaults
        if not isinstance(values,list):values=defaults
        if first_use:values=defaults+values
        self.colors = []
        if isinstance(values, list):
            for value in values:
                color = rgb(value)
                if color and color not in self.colors:
                    self.colors.append(color)
        if first_use:self._save()

    def add(self, color):
        color = rgb(color)
        if not color or color in self.colors:
            return False
        self.colors.append(color)
        self._save()
        return True

    def remove(self, color):
        color = rgb(color)
        if color not in self.colors:
            return False
        self.colors.remove(color)
        self._save()
        return True

    def replace(self,old,new):
        old,new=rgb(old),rgb(new)
        if old not in self.colors or not new:return False
        if old==new:return True
        index=self.colors.index(old)
        if new in self.colors:self.colors.remove(old)
        else:self.colors[index]=new
        self._save()
        return True

    def _save(self):
        self.settings.setValue(self.key, json.dumps(self.colors))
        self.settings.sync()
        self.changed.emit()


class PaletteWidget(QWidget):
    selected = pyqtSignal(str)
    editRequested = pyqtSignal(str)

    def __init__(self, palette, parent=None):
        super().__init__(parent)
        self.palette = palette
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        self.empty = QLabel('暂无常用颜色，点右上角色轮自定义。')
        layout.addWidget(self.empty)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setMinimumWidth(350)
        scroll.setFixedHeight(112)
        self.content = QWidget()
        self.grid = QGridLayout(self.content)
        self.grid.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.grid.setContentsMargins(2, 2, 2, 2)
        self.grid.setSpacing(5)
        scroll.setWidget(self.content)
        layout.addWidget(scroll)
        self.palette.changed.connect(self.rebuild)
        self.rebuild()

    def rebuild(self):
        while self.grid.count():
            widget = self.grid.takeAt(0).widget()
            if widget:
                widget.hide()
                widget.deleteLater()
        self.empty.setVisible(not self.palette.colors)
        for index, color in enumerate(self.palette.colors):
            button = ColorChip(color,remove=self.palette.remove,edit=self.editRequested.emit)
            button.setAccessibleName('使用常用颜色 ' + color)
            button.clicked.connect(lambda checked=False, c=color: self.selected.emit(c))
            self.grid.addWidget(button, index // 8, index % 8)


def sample_screen(image, geometry, position):
    """Map Qt global logical coordinates to captured physical pixels (including negative origins)."""
    if image.isNull() or not geometry.contains(position) or geometry.isEmpty():
        return None
    x = min(image.width() - 1, int((position.x() - geometry.x()) * image.width() / geometry.width()))
    y = min(image.height() - 1, int((position.y() - geometry.y()) * image.height() / geometry.height()))
    return image.pixelColor(x, y)


class ScreenOverlay(QWidget):
    picked = pyqtSignal(QColor)
    cancelled = pyqtSignal()

    def __init__(self, geometry, pixmap, parent=None):
        super().__init__(parent, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint |
                         Qt.WindowType.WindowStaysOnTopHint)
        self.screen_geometry, self.pixmap = QRect(geometry), pixmap
        self.image = pixmap.toImage()
        self.position = QCursor.pos()
        self.setGeometry(geometry)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def color_at(self, position):
        return sample_screen(self.image, self.screen_geometry, position)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.drawPixmap(self.rect(), self.pixmap)
        color = self.color_at(self.position)
        if color is None:
            return
        point = self.position - self.screen_geometry.topLeft()
        x = max(0, min(point.x() + 20, self.width() - 238))
        y = max(0, min(point.y() + 22, self.height() - 104))
        painter.fillRect(x, y, 236, 100, QColor('#ffffff'))
        # Magnify the captured pixels, without interpolating their colors.
        px = int(point.x() * self.image.width() / self.width())
        py = int(point.y() * self.image.height() / self.height())
        painter.drawImage(QRect(x + 5, y + 5, 70, 70), self.image,
                          QRect(px - 5, py - 5, 11, 11))
        painter.setPen(QPen(QColor('#111827'), 1))
        painter.drawRect(x + 37, y + 37, 6, 6)
        painter.fillRect(x + 84, y + 8, 28, 28, color)
        painter.drawText(x + 119, y + 27, color.name().upper())
        painter.drawText(x + 84, y + 56, '点击取色')
        painter.drawText(x + 8, y + 90, 'Esc / 右键取消')

    def mouseMoveEvent(self, event):
        self.position = event.globalPosition().toPoint()
        self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            color = self.color_at(event.globalPosition().toPoint())
            if color is not None:
                self.picked.emit(color)
        elif event.button() == Qt.MouseButton.RightButton:
            self.cancelled.emit()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.cancelled.emit()
        else:
            super().keyPressEvent(event)


class SaturationValue(QWidget):
    selected = pyqtSignal(float, float)

    def __init__(self):
        super().__init__()
        self.hue, self.saturation, self.value = 0., 1., 1.
        self.setMinimumSize(300, 180)
        self.setAccessibleName('颜色饱和度和亮度')

    def paintEvent(self, event):
        painter = QPainter(self)
        gradient = QLinearGradient(0, 0, self.width(), 0)
        gradient.setColorAt(0, QColor('white'))
        gradient.setColorAt(1, QColor.fromHsvF(self.hue, 1, 1))
        painter.fillRect(self.rect(), gradient)
        shade = QLinearGradient(0, 0, 0, self.height())
        shade.setColorAt(0, QColor(0, 0, 0, 0))
        shade.setColorAt(1, QColor('black'))
        painter.fillRect(self.rect(), shade)
        x, y = self.saturation * (self.width() - 1), (1 - self.value) * (self.height() - 1)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor('black'), 3))
        painter.drawEllipse(QPoint(round(x), round(y)), 7, 7)
        painter.setPen(QPen(QColor('white'), 2))
        painter.drawEllipse(QPoint(round(x), round(y)), 7, 7)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._select(event)

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.MouseButton.LeftButton:
            self._select(event)

    def _select(self, event):
        s = max(0., min(1., event.position().x() / max(1, self.width() - 1)))
        v = max(0., min(1., 1 - event.position().y() / max(1, self.height() - 1)))
        self.selected.emit(s, v)


class ColorModeButton(QToolButton):
    def __init__(self,parent=None):
        super().__init__(parent);self.setCheckable(True);self.setFixedSize(34,34)
        self.setAccessibleName('切换预设和自定义颜色')
        self.setToolTip('切换预设和自定义颜色')

    def paintEvent(self,event):
        painter=QPainter(self);painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        if self.isChecked():
            for x,y,color in ((10,10,'#ffd54f'),(24,10,'#f50057'),(10,24,'#8bc34a'),(24,24,'#29a0e3')):
                painter.setBrush(QColor(color));painter.drawEllipse(QPoint(x,y),5,5)
        else:
            for index in range(8):
                painter.setBrush(QColor.fromHsv(index*45,210,235))
                painter.drawPie(3,3,28,28,index*45*16,45*16)


class CustomColorDialog(QDialog):
    def __init__(self, color, palette, title, parent=None,edit_color=None):
        super().__init__(parent,Qt.WindowType.Tool|Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowTitle(title)
        self.setMinimumWidth(460)
        self.color, self.palette = QColor(color), palette
        self.edit_color=edit_color
        self.overlays = []
        self._picking = False
        layout = QVBoxLayout(self);layout.setContentsMargins(24,18,24,18)
        layout.setSpacing(14)
        header=QHBoxLayout()
        self.heading=QLabel('颜色');self.heading.setStyleSheet('font-size:20px;font-weight:600;color:#171717;')
        header.addWidget(self.heading);header.addStretch()
        self.mode_button=ColorModeButton(self);header.addWidget(self.mode_button)
        layout.addLayout(header)
        self.stack=QStackedWidget();layout.addWidget(self.stack)
        standard=QWidget();standard_layout=QVBoxLayout(standard);standard_layout.setContentsMargins(0,0,0,0)
        self.palette_pages=QStackedWidget();standard_layout.addWidget(self.palette_pages)
        builtins=QWidget();grid=QGridLayout(builtins);grid.setContentsMargins(0,5,0,5);grid.setSpacing(12)
        self.builtin_buttons=[]
        for index,c in enumerate(BUILTIN_COLORS):
            button=ColorChip(c,lambda:self.color.name())
            button.clicked.connect(lambda checked=False,color=c:self.choose_palette_color(color))
            grid.addWidget(button,index//8,index%8);self.builtin_buttons.append(button)
        self.palette_pages.addWidget(builtins)
        self.favorites=PaletteWidget(palette)
        self.favorites.selected.connect(self.choose_palette_color)
        self.favorites.editRequested.connect(self.begin_edit)
        self.palette_pages.addWidget(self.favorites)
        self.palette_pages.setFixedHeight(142)
        dots=QHBoxLayout();dots.addStretch();self.page_buttons=[]
        for index,label in enumerate(('预设颜色','常用颜色')):
            button=QToolButton();button.setText('●' if index==0 else '○');button.setFixedSize(24,24)
            button.setAutoRaise(True)
            button.setStyleSheet('QToolButton{border:0;background:transparent;color:#334155;}')
            button.setToolTip(label);button.setAccessibleName(label)
            button.clicked.connect(lambda checked=False,i=index:self.set_palette_page(i))
            dots.addWidget(button);self.page_buttons.append(button)
        dots.addStretch();standard_layout.addLayout(dots)
        self.stack.addWidget(standard)
        custom=QWidget();custom_layout=QVBoxLayout(custom);custom_layout.setContentsMargins(0,0,0,0);custom_layout.setSpacing(14)
        self.plane = SaturationValue()
        custom_layout.addWidget(self.plane)
        hue_row = QHBoxLayout()
        self.hue = QSlider(Qt.Orientation.Horizontal)
        self.hue.setRange(0, 359)
        self.hue.setAccessibleName('色相')
        self.hue.setStyleSheet('QSlider::groove:horizontal {height:12px;border-radius:4px;'
            'background:qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 red,stop:0.166 yellow,'
            'stop:0.333 lime,stop:0.5 cyan,stop:0.666 blue,stop:0.833 magenta,stop:1 red);}')
        hue_row.addWidget(self.hue)
        custom_layout.addLayout(hue_row)
        values = QHBoxLayout()
        self.hex = QLineEdit()
        self.hex.setMaxLength(7)
        self.hex.setPlaceholderText('#RRGGBB')
        self.hex.setAccessibleName('十六进制颜色值')
        values.addWidget(self.hex)
        self.pick_button = QToolButton()
        from ui.ne_toolbar import tool_icon
        self.pick_button.setIcon(tool_icon('eyedropper'))
        self.pick_button.setFixedSize(38,38)
        self.pick_button.setStyleSheet('QToolButton{border:0;background:transparent;}QToolButton:hover{background:#f1f5f9;border-radius:8px;}')
        self.pick_button.setToolTip('吸取屏幕颜色；Esc 或右键取消')
        self.pick_button.setAccessibleName('吸取屏幕颜色')
        self.pick_button.clicked.connect(self.start_picker)
        values.addWidget(self.pick_button)
        self.add_button = QPushButton('＋')
        self.add_button.setFixedSize(110,42)
        self.add_button.setAccessibleName('添加到常用颜色')
        self.add_button.setToolTip('添加到常用颜色，并显示到工具栏')
        self.add_button.clicked.connect(self.add_favorite)
        values.addWidget(self.add_button)
        custom_layout.addLayout(values)
        self.stack.addWidget(custom)
        self.message = QLabel()
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        self.message.hide()
        # Enter edits a value; it must not accidentally begin screen capture or add a color.
        for button in self.findChildren(QPushButton):
            button.setAutoDefault(False)
        self.hex.textEdited.connect(self._hex_changed)
        self.hue.valueChanged.connect(self._hue_changed)
        self.plane.selected.connect(self._plane_changed)
        self.mode_button.toggled.connect(self.switch_mode)
        self.set_color(self.color)
        self.switch_mode(False)
        if edit_color:self.begin_edit(edit_color)

    def exec(self):
        # Native popup mouse grabs prevent selecting colors outside the popup.
        # Use a tool window with popup-style dismissal instead, including while
        # an owned screen overlay is active. Outside clicks never reach the canvas.
        loop=QEventLoop(self)
        self.setResult(self.DialogCode.Rejected)
        self.finished.connect(loop.quit)
        app=QApplication.instance()
        app.installEventFilter(self)
        app.applicationStateChanged.connect(self._application_state_changed)
        self.show();self.raise_();self.activateWindow()
        try:
            loop.exec()
            return self.result()
        finally:
            app.removeEventFilter(self)
            app.applicationStateChanged.disconnect(self._application_state_changed)
            self.finished.disconnect(loop.quit)

    def _application_state_changed(self,state):
        if not self._picking and state==Qt.ApplicationState.ApplicationInactive:self.reject()

    def eventFilter(self,watched,event):
        if (not self._picking and event.type()==QEvent.Type.MouseButtonPress and
                isinstance(watched,QWidget) and watched is not self and not self.isAncestorOf(watched)):
            self.reject()
            return True
        return super().eventFilter(watched,event)

    def begin_edit(self,color):
        self.edit_color=color
        self.set_color(QColor(color))
        self.mode_button.setChecked(True)
        self.add_button.setText('保存')
        self.add_button.setAccessibleName('保存更改的颜色')

    def paintEvent(self,event):
        painter=QPainter(self);painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor('#e2e8f0'),1));painter.setBrush(QColor('white'))
        painter.drawRoundedRect(self.rect().adjusted(1,1,-1,-1),22,22)

    def switch_mode(self,custom):
        self.heading.setText(('更改颜色' if self.edit_color else '自定义颜色') if custom else '颜色')
        self.stack.setCurrentIndex(1 if custom else 0)
        self.stack.setFixedHeight(272 if custom else 180)
        self.adjustSize()

    def set_palette_page(self,index):
        self.palette_pages.setCurrentIndex(index)
        for i,button in enumerate(self.page_buttons):button.setText('●' if i==index else '○')

    def choose_palette_color(self,color):
        self.set_color(QColor(color))
        if self.edit_color:self.palette.replace(self.edit_color,color)
        else:self.palette.add(color)
        self.accept()

    def set_color(self, color, keep_hex=False):
        if not color.isValid():
            return
        self.color = QColor(color.name())
        hue, saturation, value, _ = self.color.getHsvF()
        if hue >= 0:
            self.plane.hue = hue
        self.plane.saturation, self.plane.value = saturation, value
        self.plane.update()
        self.hue.blockSignals(True)
        self.hue.setValue(round(self.plane.hue * 359))
        self.hue.blockSignals(False)
        if not keep_hex:
            self.hex.setText(self.color.name())
        foreground='#111827' if self.color.lightnessF()>.72 else '#ffffff'
        self.add_button.setStyleSheet('QPushButton{background:'+self.color.name()+';color:'+foreground+
            ';font-size:28px;border:1px solid #d1d5db;border-radius:6px;padding:0;min-width:0;}')
        self.add_button.setFixedWidth(110)
        for button in self.builtin_buttons:button.update()
        self._valid(True)

    def _valid(self, valid):
        self.hex.setStyleSheet('' if valid else 'border:1px solid #dc2626;')
        self.hex.setToolTip('' if valid else '请输入完整的六位十六进制颜色，例如 #2979ff')
        self.add_button.setEnabled(valid)

    def _hex_changed(self, value):
        color = rgb(value)
        if color:
            self.set_color(QColor(color), keep_hex=True)
        else:
            self._valid(False)

    def _hue_changed(self, value):
        self.plane.hue = value / 360
        self._plane_changed(self.plane.saturation, self.plane.value)

    def _plane_changed(self, saturation, value):
        self.set_color(QColor.fromHsvF(self.plane.hue, saturation, value))

    def add_favorite(self):
        if not rgb(self.hex.text()):return
        if self.edit_color:self.palette.replace(self.edit_color,self.color.name())
        else:self.palette.add(self.color.name())
        self.accept()

    def start_picker(self):
        if self._picking:
            return
        self._picking = True
        self.message.hide()
        # QDialog.hide() terminates exec(), so keep its modal loop alive while
        # making the dialog transparent for the desktop capture.
        self.setWindowOpacity(0)
        # Let the compositor remove this dialog before capturing the underlying screen.
        QTimer.singleShot(180, self._capture_screens)

    def _capture_screens(self):
        if not self._picking:
            return
        try:
            captures = [(screen, screen.grabWindow(0)) for screen in QGuiApplication.screens()]
            if not captures or any(pixmap.isNull() for screen, pixmap in captures):
                raise RuntimeError('Screen capture unavailable')
            # Capture all monitors before showing any overlay, so the preview never samples itself.
            for screen, pixmap in captures:
                overlay = ScreenOverlay(screen.geometry(), pixmap, self)
                overlay.setScreen(screen)
                overlay.setGeometry(screen.geometry())
                overlay.picked.connect(self.finish_picker)
                overlay.cancelled.connect(lambda: self.finish_picker())
                self.overlays.append(overlay)
            for overlay in self.overlays:
                overlay.show()
            active = next((o for o in self.overlays if o.screen_geometry.contains(QCursor.pos())), self.overlays[0])
            active.raise_()
            active.activateWindow()
            active.setFocus()
        except Exception:
            self.finish_picker()
            self.message.setText('无法读取屏幕颜色，请使用色板或输入颜色值。')
            self.message.show()

    def finish_picker(self, color=None):
        self._picking = False
        for overlay in self.overlays:
            overlay.close()
            overlay.deleteLater()
        self.overlays.clear()
        if color is not None:
            self.set_color(color)
        self.setWindowOpacity(1)
        self.show()
        self.raise_()
        self.activateWindow()

    def done(self, result):
        self._picking = False
        for overlay in self.overlays:
            overlay.close()
            overlay.deleteLater()
        self.overlays.clear()
        self.setWindowOpacity(1)
        super().done(result)


