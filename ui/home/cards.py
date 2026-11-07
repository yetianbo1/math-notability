"""Notebook covers read only the bounded thumbnail resource, never full pages."""
from functools import lru_cache
from pathlib import Path
from zipfile import ZipFile, BadZipFile
import zlib

from PyQt6.QtCore import Qt, QRectF, QSize, QBuffer, QByteArray, QIODevice, QEvent, QPoint, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPen, QImageReader, QPixmap, QFont, QPainterPath
from PyQt6.QtWidgets import QStyledItemDelegate, QStyle, QStyleOptionButton, QListView, QStyleOptionFocusRect
from ui.ne_toolbar import tool_icon


@lru_cache(maxsize=128)
def cover(path, modified):
    try:
        with ZipFile(path) as archive:
            for name in ('thumbnail.png', 'Thumbnail.png', 'thumbnail.jpg', 'preview.png'):
                try:
                    info = archive.getinfo(name)
                except KeyError:
                    continue
                if info.file_size > 2 * 1024 * 1024:
                    continue
                data = archive.read(info)
                buffer = QBuffer()
                buffer.setData(QByteArray(data))
                buffer.open(QIODevice.OpenModeFlag.ReadOnly)
                reader = QImageReader(buffer)
                size = reader.size()
                if not size.isValid() or max(size.width(), size.height()) > 4096:
                    continue
                # Blank one-pixel thumbnails from new notes use the paper placeholder.
                if size.width() < 8 or size.height() < 8:
                    continue
                reader.setScaledSize(size.scaled(QSize(360, 440), Qt.AspectRatioMode.KeepAspectRatio))
                image = reader.read()
                if not image.isNull():
                    return QPixmap.fromImage(image)
    except (OSError, ValueError, BadZipFile, RuntimeError, zlib.error):
        pass
    return QPixmap()


class NotebookCard(QStyledItemDelegate):
    menuRequested = pyqtSignal(QPoint)

    def check_rect(self, rect):
        top = rect.center().y()-18 if self.parent().viewMode() == QListView.ViewMode.ListMode else rect.top()+6
        return QRectF(rect.left()+6, top, 36, 36)

    def draw_check(self, painter, option):
        view = self.parent()
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        if view.viewMode() == QListView.ViewMode.ListMode or selected or view.batch or option.state & QStyle.StateFlag.State_MouseOver:
            if view.viewMode() == QListView.ViewMode.ListMode:
                box = self.check_rect(option.rect).adjusted(10,10,-10,-10)
                painter.save()
                painter.setPen(QPen(QColor('#4d86ee' if selected else '#c3cee0'),1))
                painter.setBrush(QColor('#4d86ee' if selected else '#ffffff'))
                painter.drawRoundedRect(box,4,4)
                if selected:
                    painter.setPen(QPen(QColor('#ffffff'),1.8,Qt.PenStyle.SolidLine,Qt.PenCapStyle.RoundCap,Qt.PenJoinStyle.RoundJoin))
                    tick = QPainterPath();tick.moveTo(box.left()+4,box.top()+8)
                    tick.lineTo(box.left()+7,box.top()+11);tick.lineTo(box.left()+12,box.top()+5)
                    painter.drawPath(tick)
                painter.restore()
            else:
                check = QStyleOptionButton()
                check.rect = self.check_rect(option.rect).adjusted(8,8,-8,-8).toRect()
                check.state = QStyle.StateFlag.State_Enabled | (QStyle.StateFlag.State_On if selected else QStyle.StateFlag.State_Off)
                view.style().drawControl(QStyle.ControlElement.CE_CheckBox, check, painter, view)
        if option.state & QStyle.StateFlag.State_HasFocus:
            focus = QStyleOptionFocusRect()
            focus.rect = option.rect.adjusted(4,4,-4,-4)
            focus.state = QStyle.StateFlag.State_KeyboardFocusChange | QStyle.StateFlag.State_Item
            view.style().drawPrimitive(QStyle.PrimitiveElement.PE_FrameFocusRect,focus,painter,view)

    def menu_rect(self, rect):
        top = rect.center().y()-15 if self.parent().viewMode() == QListView.ViewMode.ListMode else rect.top()+9
        return QRectF(rect.right() - 38, top, 30, 30)

    def paint_list(self, painter, option, index, entry):
        rect = QRectF(option.rect).adjusted(2,2,-2,-2)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        painter.setPen(QPen(QColor('#c8dcff'),1) if selected else QPen(Qt.PenStyle.NoPen))
        painter.setBrush(QColor('#eaf2ff' if selected else '#f3f7fd' if hovered else '#ffffff'))
        painter.drawRoundedRect(rect,7,7)
        if selected:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor('#4d86ee'))
            painter.drawRoundedRect(QRectF(rect.left(),rect.center().y()-14,3,28),1.5,1.5)
        path = Path(entry['path'])
        thumb = QRectF(rect.left()+46,rect.center().y()-23,36,46)
        painter.setPen(QPen(QColor('#dce5f2'),1))
        painter.setBrush(QColor('#f0f5fd'))
        painter.drawRoundedRect(thumb,4,4)
        try:
            pixmap = cover(str(path),path.stat().st_mtime_ns)
        except OSError:
            pixmap = QPixmap()
        if pixmap.isNull():
            painter.drawPixmap(round(thumb.center().x()-11),round(thumb.center().y()-11),
                               tool_icon('new_note','#7d9cc9').pixmap(22,22))
        else:
            scaled = pixmap.scaled(thumb.adjusted(3,3,-3,-3).size().toSize(),
                Qt.AspectRatioMode.KeepAspectRatio,Qt.TransformationMode.SmoothTransformation)
            painter.save()
            clip = QPainterPath();clip.addRoundedRect(thumb,4,4);painter.setClipPath(clip)
            painter.drawPixmap(round(thumb.center().x()-scaled.width()/2),
                               round(thumb.center().y()-scaled.height()/2),scaled)
            painter.restore()
        wide = rect.width() >= 650
        left = rect.left()+96
        right = rect.right()-(300 if wide else 44)
        title = QRectF(left,rect.top()+10,max(1,right-left),22)
        font = QFont(option.font);font.setPixelSize(14);font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font);painter.setPen(QColor('#203b61'))
        painter.drawText(title,Qt.AlignmentFlag.AlignVCenter,
            painter.fontMetrics().elidedText(path.stem,Qt.TextElideMode.ElideRight,round(title.width())))
        font.setPixelSize(11);font.setWeight(QFont.Weight.Normal);painter.setFont(font)
        painter.setPen(QColor('#8191a8'))
        detail = path.suffix[1:].upper()+' · '+('笔记库' if entry.get('managed') else '外部文件')
        if not wide:
            detail += ' · '+entry.get('category_name','未分类')+' · '+entry.get('when','历史记录')
        secondary = QRectF(left,rect.top()+35,max(1,right-left),18)
        painter.drawText(secondary,Qt.AlignmentFlag.AlignVCenter,
            painter.fontMetrics().elidedText(detail,Qt.TextElideMode.ElideRight,round(secondary.width())))
        if wide:
            badge = QRectF(rect.right()-286,rect.center().y()-12,110,24)
            painter.setPen(Qt.PenStyle.NoPen);painter.setBrush(QColor('#f0f4fa' if not selected else '#dfeaff'))
            painter.drawRoundedRect(badge,6,6)
            font.setPixelSize(12);painter.setFont(font);painter.setPen(QColor('#607794'))
            painter.drawText(badge.adjusted(9,0,-9,0),Qt.AlignmentFlag.AlignCenter,
                painter.fontMetrics().elidedText(entry.get('category_name','未分类'),Qt.TextElideMode.ElideRight,92))
            painter.setPen(QColor('#8090a7'))
            painter.drawText(QRectF(rect.right()-158,rect.top(),110,rect.height()),
                Qt.AlignmentFlag.AlignVCenter|Qt.AlignmentFlag.AlignRight,entry.get('when','历史记录'))
        if not selected and not hovered and index.row()<index.model().rowCount()-1:
            painter.setPen(QPen(QColor('#edf1f7'),1))
            painter.drawLine(round(left),round(rect.bottom()+3),round(rect.right()-10),round(rect.bottom()+3))
        self.draw_check(painter,option)
        if not self.parent().batch:
            button = self.menu_rect(option.rect)
            painter.setPen(Qt.PenStyle.NoPen)
            if hovered:
                painter.setBrush(QColor('#e4edfa'));painter.drawRoundedRect(button,6,6)
            painter.setBrush(QColor('#6e85a5'))
            for offset in (-5,0,5):
                painter.drawEllipse(QRectF(button.center().x()+offset-1.5,button.center().y()-1.5,3,3))

    def editorEvent(self, event, model, option, index):
        entry = index.data(int(Qt.ItemDataRole.UserRole) + 1) or {}
        if entry.get('group'):
            return False
        if event.type() in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease,
                            QEvent.Type.MouseButtonDblClick):
            if event.button() == Qt.MouseButton.LeftButton and self.menu_rect(option.rect).contains(event.position()):
                if event.type() == QEvent.Type.MouseButtonRelease:
                    self.menuRequested.emit(event.position().toPoint())
                return True
        return super().editorEvent(event, model, option, index)

    def paint(self, painter, option, index):
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        entry = index.data(int(Qt.ItemDataRole.UserRole) + 1)
        if not entry:
            painter.restore()
            return
        if entry.get('group'):
            rect = QRectF(option.rect).adjusted(4, 6, -4, -2)
            background = index.data(Qt.ItemDataRole.BackgroundRole)
            if background:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(background)
                painter.drawRoundedRect(rect, 6, 6)
            font = QFont(option.font)
            font.setPixelSize(13 if entry.get('depth') else 15)
            font.setBold(True)
            painter.setFont(font)
            painter.setPen(QColor('#607794' if entry.get('depth') else '#284d83'))
            label = ('    ' if entry.get('depth') else '') + entry['title'] + '  ·  ' + str(entry['count']) + ' 本'
            painter.drawText(rect.adjusted(8, 0, -8, 0), Qt.AlignmentFlag.AlignVCenter,
                             painter.fontMetrics().elidedText(label, Qt.TextElideMode.ElideRight, round(rect.width()-16)))
            painter.restore()
            return
        if self.parent().viewMode() == QListView.ViewMode.ListMode:
            self.paint_list(painter,option,index,entry)
            painter.restore()
            return
        rect = QRectF(option.rect).adjusted(2, 2, -2, -2)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        painter.setPen(QPen(QColor('#4d86ee' if selected else '#d6e1f2'), 2 if selected else 1))
        painter.setBrush(QColor('#edf4ff' if selected else '#ffffff'))
        painter.drawRoundedRect(rect, 8, 8)
        paper = rect.adjusted(1, 1, -1, -77)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor('#f7f8fa'))
        painter.drawRoundedRect(paper, 8, 8)
        painter.drawRect(QRectF(paper.left(), paper.bottom() - 14, paper.width(), 14))
        path = Path(entry['path'])
        try:
            pixmap = cover(str(path), path.stat().st_mtime_ns)
        except OSError:
            pixmap = QPixmap()
        if not pixmap.isNull():
            scaled = pixmap.scaled(paper.size().toSize(), Qt.AspectRatioMode.KeepAspectRatio,
                                   Qt.TransformationMode.SmoothTransformation)
            painter.drawPixmap(round(paper.center().x() - scaled.width() / 2),
                               round(paper.center().y() - scaled.height() / 2), scaled)
        else:
            icon = tool_icon('new_note', '#9caec6').pixmap(48, 48)
            painter.drawPixmap(round(paper.center().x() - 24), round(paper.center().y() - 34), icon)
            painter.setPen(QColor('#8795a8'))
            painter.drawText(paper.adjusted(0, 35, 0, 0), Qt.AlignmentFlag.AlignCenter, path.suffix[1:].upper())
        font = QFont(option.font)
        font.setBold(True)
        font.setPointSize(11)
        painter.setFont(font)
        painter.setPen(QColor('#263c5c'))
        title = rect.adjusted(13, rect.height() - 66, -13, -38)
        painter.drawText(title, Qt.AlignmentFlag.AlignVCenter,
            painter.fontMetrics().elidedText(path.stem, Qt.TextElideMode.ElideRight, round(title.width())))
        font.setBold(False)
        font.setPointSize(9)
        painter.setFont(font)
        painter.setPen(QColor('#7b8ca6' if path.is_file() else '#bc5656'))
        detail = entry['detail'] if path.is_file() else '文件已移动或不存在'
        footer = rect.adjusted(13, rect.height() - 34, -13, -10)
        painter.drawText(footer, Qt.AlignmentFlag.AlignVCenter,
            painter.fontMetrics().elidedText(detail, Qt.TextElideMode.ElideRight, round(footer.width())))
        self.draw_check(painter,option)
        if self.parent().batch:
            painter.restore()
            return
        button = self.menu_rect(option.rect)
        painter.setPen(QPen(QColor('#d6e1f2'), 1))
        painter.setBrush(QColor('#ffffff'))
        painter.drawEllipse(button)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor('#526988'))
        for offset in (-6, 0, 6):
            painter.drawEllipse(QRectF(button.center().x() + offset - 1.5,
                                      button.center().y() - 1.5, 3, 3))
        painter.restore()
