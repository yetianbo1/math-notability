"""UI-owned bitmap LRU; a separate document is owned solely by its worker."""
from collections import OrderedDict
from threading import Condition
from PyQt6.QtCore import QObject,QThread,QSize,pyqtSignal
from PyQt6.QtGui import QImage
from PyQt6.QtPdf import QPdfDocument
from PyQt6 import sip
import math
from PyQt6.QtCore import Qt


def render_pdf_page(document,page,size,*,supersample=False):
    """Avoid PDFium's oversized glyph strokes at small NOTE preview sizes."""
    if not supersample:
        return document.render(page,size)
    factor=min(3.,math.sqrt(4_000_000/max(1,size.width()*size.height())))
    if factor<=1:
        return document.render(page,size)
    larger=QSize(max(1,int(size.width()*factor)),max(1,int(size.height()*factor)))
    image=document.render(page,larger)
    return image.scaled(size,Qt.AspectRatioMode.IgnoreAspectRatio,Qt.TransformationMode.SmoothTransformation)


class PdfWorker(QThread):
    rendered=pyqtSignal(object,QImage,str)

    def __init__(self,path,parent=None,*,supersample=False):
        super().__init__(parent)
        self.path=path;self.condition=Condition();self.jobs=[];self.current=None;self.stopping=False
        self.supersample=supersample

    def replace_jobs(self,jobs):
        with self.condition:
            self.jobs=[key for key in jobs if key!=self.current][:8]
            self.condition.notify()

    def run(self):
        document=QPdfDocument(None)
        try:
            error=document.load(self.path)
            message='' if error==QPdfDocument.Error.None_ else f'PDF 加载失败：{error.name}'
            while True:
                with self.condition:
                    while not self.stopping and not self.jobs:self.condition.wait()
                    if self.stopping:break
                    key=self.jobs.pop(0);self.current=key
                image=QImage();failure=message
                if not failure:
                    try:
                        image=render_pdf_page(document,key[0],QSize(key[1],key[2]),supersample=self.supersample)
                        if image.isNull():failure=f'第 {key[0]+1} 页 PDF 渲染失败'
                    except Exception as exc:failure=str(exc)
                self.rendered.emit(key,image,failure)
                with self.condition:self.current=None
        finally:
            document.close();sip.delete(document)

    def stop(self):
        with self.condition:
            self.stopping=True;self.jobs=[];self.condition.notify()
        # The source PDF's temporary directory may be removed immediately next.
        # Join before that cleanup, including when replacing an open notebook.
        self.wait()


class PdfRasterCache(QObject):
    ready=pyqtSignal(object)
    BUDGET=32*1024*1024

    def __init__(self,path,parent=None,*,supersample=False):
        super().__init__(parent)
        self.cache=OrderedDict();self.errors={};self.bytes=0;self.closed=False
        self.worker=PdfWorker(str(path),self,supersample=supersample)
        self.worker.rendered.connect(self._received)

    def get(self,key):
        image=self.cache.get(key)
        if image is not None:self.cache.move_to_end(key)
        if key in self.errors:raise ValueError(self.errors[key])
        return image

    def request(self,keys):
        if self.closed:return
        missing=[key for key in dict.fromkeys(keys) if key not in self.cache and key not in self.errors]
        self.worker.replace_jobs(missing)
        if missing and not self.worker.isRunning():self.worker.start()

    def request_thumbnail(self,key):
        """Append idle work without displacing visible-page render requests."""
        if self.closed or key in self.cache or key in self.errors:return
        with self.worker.condition:
            if key!=self.worker.current and key not in self.worker.jobs and len(self.worker.jobs)<8:
                self.worker.jobs.append(key);self.worker.condition.notify()
        if not self.worker.isRunning():self.worker.start()

    def _received(self,key,image,error):
        if self.closed:return
        if error:
            self.errors[key]=error
            while len(self.errors)>64:self.errors.pop(next(iter(self.errors)))
        else:
            previous=self.cache.pop(key,None)
            if previous is not None:self.bytes-=previous.sizeInBytes()
            self.cache[key]=image;self.bytes+=image.sizeInBytes()
            while self.bytes>self.BUDGET and len(self.cache)>1:
                _,old=self.cache.popitem(last=False);self.bytes-=old.sizeInBytes()
        self.ready.emit(key)

    def close(self,*_):
        if self.closed:return
        self.closed=True;self.worker.stop();self.cache.clear();self.bytes=0
