"""Optional OpenGL viewport, sharing the canvas model and CPU export path.

Only the visible viewport gets a framebuffer. Never make the entire continuous
notebook an OpenGL widget: a long document would allocate an enormous FBO.
"""
import math
from PyQt6 import sip
from PyQt6.QtCore import QObject,QEvent,QPoint,QRect,Qt,QTimer,pyqtSignal
from PyQt6.QtGui import QPainter,QSurfaceFormat,QColor
from PyQt6.QtWidgets import QApplication
from infrastructure.ne_diagnostics import exception as diagnostic_exception,stage as diagnostic_stage


def hardware_renderer(name):
    """Feature-based support, without a GeForce generation/model whitelist."""
    name=str(name or '').lower()
    return bool(name) and not any(s in name for s in (
        'llvmpipe','softpipe','swiftshader','software','gdi generic',
        'microsoft basic render','mesa offscreen'))


def platform_supported():
    return QApplication.platformName().lower() not in ('offscreen','minimal','vnc')


class GpuViewport(QObject):
    changed=pyqtSignal(bool,str)
    MAX_PIXELS=16_000_000

    def __init__(self,scroll,enabled=True,parent=None):
        super().__init__(parent)
        self.scroll=scroll;self.enabled=bool(enabled);self.canvas=None;self.surface=None
        self.active=False;self.description='软件绘制';self.failed=False
        self._generation=0
        scroll.viewport().installEventFilter(self)
        scroll.verticalScrollBar().valueChanged.connect(self.refresh)
        scroll.horizontalScrollBar().valueChanged.connect(self.refresh)

    def attach(self,canvas):
        self.detach();self.canvas=canvas
        if not hasattr(canvas,'paintVisible'):return
        canvas._gpu_view=self;canvas.installEventFilter(self)
        if self.enabled:self._create()

    def detach(self):
        self._generation+=1
        self.active=False
        if self.canvas is not None and not sip.isdeleted(self.canvas):
            self.canvas.removeEventFilter(self);self.canvas._gpu_view=None;self.canvas.update()
        self.canvas=None
        if self.surface is not None:
            self.surface.hide();self.surface.deleteLater();self.surface=None
        self.description='软件绘制'

    def setEnabled(self,enabled):
        self.enabled=bool(enabled);canvas=self.canvas
        if self.enabled:self.failed=False
        if canvas is not None:self.attach(canvas)

    def _within_budget(self):
        viewport=self.scroll.viewport();dpr=viewport.devicePixelRatioF()
        return math.ceil(viewport.width()*dpr)*math.ceil(viewport.height()*dpr)<=self.MAX_PIXELS

    def _create(self):
        if self.failed or not platform_supported() or not self._within_budget():return
        try:
            from PyQt6.QtOpenGLWidgets import QOpenGLWidget
            # Qt's OpenGL painter needs no RTX/CUDA-specific API. A 2.0
            # compatibility request also works with older GeForce drivers.
            owner=self
            class Surface(QOpenGLWidget):
                def initializeGL(self):
                    if owner.surface is not self:return
                    try:
                        from PyQt6.QtOpenGL import QOpenGLVersionProfile,QOpenGLVersionFunctionsFactory
                        profile=QOpenGLVersionProfile();profile.setVersion(2,0)
                        functions=QOpenGLVersionFunctionsFactory.get(profile,self.context())
                        if functions is None:raise RuntimeError('OpenGL 2.0 绘制接口不可用')
                        functions.initializeOpenGLFunctions()
                        renderer=functions.glGetString(0x1F01)
                        if not hardware_renderer(renderer):raise RuntimeError('当前驱动仅提供软件 OpenGL')
                        owner.active=True;owner.description=renderer
                        diagnostic_stage('GPU viewport: '+renderer)
                        owner.changed.emit(True,renderer)
                    except Exception as exc:owner._fail(str(exc))

                def paintGL(self):
                    painter=QPainter(self)
                    try:
                        if not painter.isActive():raise RuntimeError('GPU 绘制上下文已失效')
                        painter.fillRect(self.rect(),QColor('#42464d'))
                        canvas=owner.canvas
                        if not owner.active or canvas is None or sip.isdeleted(canvas):return
                        origin=canvas.mapFrom(owner.scroll.viewport(),QPoint(0,0))
                        painter.translate(-origin.x(),-origin.y())
                        canvas.paintVisible(painter,QRect(origin,self.size()),self.devicePixelRatioF())
                    except Exception as exc:
                        diagnostic_exception('GPU viewport paint',exc);owner._fail(str(exc))
                    finally:painter.end()
            surface=Surface(self.scroll.viewport())
            fmt=QSurfaceFormat();fmt.setVersion(2,0);fmt.setProfile(QSurfaceFormat.OpenGLContextProfile.NoProfile)
            fmt.setStencilBufferSize(8);fmt.setDepthBufferSize(0)
            fmt.setSamples(4);fmt.setSwapInterval(1);surface.setFormat(fmt)
            surface.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents,True)
            surface.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self.surface=surface;surface.setGeometry(self.scroll.viewport().rect());surface.show();surface.raise_()
            QTimer.singleShot(350,lambda:self._verify(surface))
        except Exception as exc:self._fail(str(exc))

    def _verify(self,surface):
        if self.surface is surface and not sip.isdeleted(surface) and not surface.isValid():
            self._fail('OpenGL 上下文不可用')

    def _fail(self,reason):
        from infrastructure.ne_diagnostics import warning
        if not self.failed:warning('GPU fallback: '+reason)
        self.active=False;self.failed=True;self.description='软件绘制：'+reason
        # Avoid destroying the OpenGL widget from inside initializeGL/paintGL.
        generation=self._generation
        QTimer.singleShot(0,lambda:self._fallback(generation))

    def _fallback(self,generation):
        if generation!=self._generation:return
        if self.surface is not None:self.surface.hide();self.surface.deleteLater();self.surface=None
        if self.canvas is not None and not sip.isdeleted(self.canvas):self.canvas.update()
        self.changed.emit(False,self.description)

    def refresh(self,*_):
        if self.surface is not None and self.active:
            if not self.surface.isValid():self._fail('OpenGL 上下文已失效')
            else:self.surface.update()

    def eventFilter(self,obj,event):
        if event.type() in (QEvent.Type.Resize,QEvent.Type.Move,QEvent.Type.Show):
            if self.surface is not None:
                if not self._within_budget():self._fail('窗口绘制面积超过加速上限')
                else:
                    self.surface.setGeometry(self.scroll.viewport().rect());self.surface.raise_();self.refresh()
        return False
