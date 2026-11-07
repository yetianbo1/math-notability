"""Render the source SVG to a multi-resolution Windows ICO and PNG preview."""
import os,struct
from pathlib import Path
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QImage,QPainter
from PyQt6.QtCore import QByteArray,QBuffer,QIODevice,Qt
from PyQt6.QtSvg import QSvgRenderer

def main():
    app=QApplication([])
    assets=Path(__file__).resolve().parent.parent/'assets'
    svg=QSvgRenderer(str(assets/'notability-viewer.svg'))
    if not svg.isValid():raise ValueError('Invalid icon SVG')
    frames=[]
    for size in (16,24,32,48,64,128,256):
        image=QImage(size,size,QImage.Format.Format_ARGB32);image.fill(Qt.GlobalColor.transparent)
        painter=QPainter(image);painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        svg.render(painter);painter.end()
        data=QByteArray();buffer=QBuffer(data);buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        if not image.save(buffer,'PNG'):raise ValueError('PNG encoder failed')
        buffer.close();frames.append((size,bytes(data)))
        if size==256:image.save(str(assets/'notability-viewer.png'))
    header=struct.pack('<HHH',0,1,len(frames));offset=6+16*len(frames);entries=[]
    for size,data in frames:
        entries.append(struct.pack('<BBBBHHII',size%256,size%256,0,0,1,32,len(data),offset));offset+=len(data)
    (assets/'notability-viewer.ico').write_bytes(header+b''.join(entries)+b''.join(data for _,data in frames))

if __name__=='__main__':main()
