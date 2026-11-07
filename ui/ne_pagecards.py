"""Thumbnail cards with page labels and per-page action affordances."""
from PyQt6.QtCore import Qt,QRectF,QSize,QPointF
from PyQt6.QtGui import QColor,QPen,QFont
from PyQt6.QtWidgets import QStyledItemDelegate,QStyle


class PageCardDelegate(QStyledItemDelegate):
    def __init__(self,sidebar):
        super().__init__(sidebar.list);self.sidebar=sidebar
    def sizeHint(self,option,index):
        width=max(170,self.sidebar.list.viewport().width())
        ratio=1.414;c=self.sidebar.canvas
        if c and index.row()<c.page_count:
            w,h=c.sizes[index.row()];ratio=max(.55,min(1.65,h/w))
        return QSize(width,round((width-32)*ratio)+48)
    def cardRect(self,rect):return QRectF(rect).adjusted(14,8,-14,-34)
    def moreRect(self,rect):
        card=self.cardRect(rect)
        return QRectF(card.left()+10,card.bottom()-38,28,28)
    def paint(self,painter,option,index):
        painter.save();painter.setRenderHint(painter.RenderHint.Antialiasing)
        card=self.cardRect(option.rect);selected=bool(option.state & QStyle.StateFlag.State_Selected)
        painter.setPen(Qt.PenStyle.NoPen);painter.setBrush(QColor(30,45,70,15));painter.drawRoundedRect(card.translated(0,2),9,9)
        painter.setBrush(QColor('#ffffff'));painter.setPen(QPen(QColor('#4b7bec') if selected else QColor('#d6dbe4'),2.5 if selected else 1))
        painter.drawRoundedRect(card,9,9)
        icon=index.data(Qt.ItemDataRole.DecorationRole);paper=card.adjusted(10,10,-10,-10)
        if icon and not icon.isNull():
            pix=icon.pixmap(QSize(round(paper.width()*2),round(paper.height()*2)))
            if not pix.isNull():
                size=pix.size().scaled(paper.size().toSize(),Qt.AspectRatioMode.KeepAspectRatio)
                target=QRectF(paper.center().x()-size.width()/2,paper.center().y()-size.height()/2,size.width(),size.height())
                painter.setRenderHint(painter.RenderHint.SmoothPixmapTransform);painter.drawPixmap(target,pix,QRectF(pix.rect()))
        else:
            painter.setPen(QColor('#a5adba'));painter.drawText(paper,Qt.AlignmentFlag.AlignCenter,'预览加载中')
        if self.sidebar.batch_mode:
            # Explicit multi-page selection indicator instead of the normal
            # per-page "more" button. The entire card toggles selection.
            marker=QRectF(card.right()-38,card.top()+10,27,27)
            painter.setPen(QPen(QColor('#3769d8') if selected else QColor('#98a7bd'),1.7))
            painter.setBrush(QColor('#3769d8') if selected else QColor('#ffffff'))
            painter.drawRoundedRect(marker,6,6)
            if selected:
                tick=QFont(option.font);tick.setPixelSize(19);tick.setBold(True)
                painter.setFont(tick);painter.setPen(QColor('#ffffff'))
                painter.drawText(marker,Qt.AlignmentFlag.AlignCenter,'✓')
        else:
            button=self.moreRect(option.rect)
            painter.setPen(QPen(QColor('#d4dae5'),1));painter.setBrush(QColor('#ffffff'));painter.drawEllipse(button)
            painter.setPen(Qt.PenStyle.NoPen);painter.setBrush(QColor('#596273'))
            for dx in (-5,0,5):painter.drawEllipse(QPointF(button.center().x()+dx,button.center().y()),1.3,1.3)
        font=QFont(option.font);font.setPixelSize(13);painter.setFont(font)
        painter.setPen(QColor('#3769d8') if selected else QColor('#7e8795'))
        label=QRectF(option.rect.left(),card.bottom()+7,option.rect.width(),24)
        painter.drawText(label,Qt.AlignmentFlag.AlignCenter,str(index.row()+1))
        painter.restore()
