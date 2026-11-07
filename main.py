"""NotabilityEditor 0.7.9-Beta - local note viewer and ink editor."""
# Release channel: relese. Same-base relese outranks every internal vN.
# Compatibility revision: 2026-10-29 (native paper ranges and shapes).
# Seamless pages: 2026-10-29, cross-page drawing and erasure.
from infrastructure import ne_ai_runtime  # Initialize CPU runtime before Qt DLLs.
import sys
import threading
import os
import tempfile
from pathlib import Path
from lxml import etree
from PyQt6 import sip
from PyQt6.QtCore import Qt, QEvent, QThread, pyqtSignal, QTimer, QSettings
from PyQt6.QtGui import QAction, QKeySequence, QActionGroup, QIcon, QPixmap, QColor, QImageReader, QImage
from PyQt6.QtPdf import QPdfDocument
from PyQt6.QtSvgWidgets import QSvgWidget
from PyQt6.QtWidgets import (QApplication, QMainWindow, QDialog, QFileDialog, QLabel, QScrollArea,
                             QMessageBox, QProgressDialog, QProgressBar, QSpinBox, QPushButton, QSlider, QDoubleSpinBox, QComboBox, QWidget, QSizePolicy, QVBoxLayout, QHBoxLayout, QMenu)
from formats.ne_loader import prepare_note, LoadCancelled
from infrastructure.ne_diagnostics import (install as install_diagnostics, stage as diagnostic_stage,
                            exception as diagnostic_exception, warning as diagnostic_warning,
                            log_directory)
from core.ne_filetypes import file_action
from import_export.ne_pdfimport import pdf_to_ntb, PdfImportCancelled
from import_export.ne_importdraft import ImportDraft
from rendering.ne_pdfview import PdfNoteCanvas
from editing.ne_edits import COLORS
from formats.ne_native import write_ntb
from platform_integration.ne_windows import icon_path,set_app_identity
from ui.ne_defaults import DefaultAppsDialog
from ui.ne_toolbar import EditorPanel,ReadingPanel,tool_icon
from ui.ne_colors import CustomColorDialog
from ui.ne_menus import build_menus,refresh_menu_state,refresh_recent_menu,apply_selection_preferences
from ui.ne_sidebar import PageSidebar
from pages.ne_page_access import grant_page_access
from import_export.ne_export import write_pdf, ExportCancelled
from import_export.ne_noteexport import export_note
from ui.ne_style import APP_STYLE,style_menu
from ui.ne_ui import prepare_progress
from ui.ne_save_notice import show_copyable_path_notice
from ui.ne_unsaved_dialog import (
    CHOICE_DISCARD, CHOICE_SAVE, show_unsaved_changes_dialog,
)
from images.ne_inserted_images import rewrite_archive
from ui.ne_shortcuts import ShortcutManager
from platform_integration.ne_userdata import migrate_user_data
from editing.ne_save_session import SaveSession, SnapshotWorker, capture as capture_save, file_stamp


class LoadWorker(QThread):
    progress = pyqtSignal(int,str)
    ready = pyqtSignal(object)
    page_access_ready = pyqtSignal(object)
    failed = pyqtSignal(str)
    cancelled = pyqtSignal()

    def __init__(self,path,parent=None):
        super().__init__(parent)
        self.path = path
        self.stop = threading.Event()
        self.page_access_gate = threading.Event()

    def run(self):
        try:
            result = prepare_note(self.path,self.progress.emit,self.stop.is_set)
            if not self.stop.is_set() and result.archive_path:
                self.progress.emit(98,'正在准备页面显示索引…')
                from rendering.ne_canvas_index import prepare_canvas_index
                try:result.render_index=prepare_canvas_index(result.svg,result.height,self.stop.is_set)
                except BaseException:
                    result.cleanup()
                    raise
            if self.stop.is_set():
                result.cleanup()
                self.cancelled.emit()
            else:
                index=result.render_index
                result.page_access_pending=bool(result.bundle_data and index and index.page_count is not None
                                                and result.path.lower().endswith('.ntb'))
                self.ready.emit(result)
                if result.page_access_pending and not self.stop.is_set():
                    # Let the first visible raster use CPU/GIL first. The
                    # bounded fallback also supports opens in a hidden window.
                    for _ in range(40):
                        if self.stop.is_set() or self.page_access_gate.wait(.05):break
                    if self.stop.is_set():return
                    from pages.ne_page_access import inspect_page_access,PageAccess
                    try:access=inspect_page_access(result.bundle_data,result.path,index.page_count)
                    except Exception as exc:
                        diagnostic_exception('background page capability check',exc)
                        access=PageAccess(reason='页面结构校验失败：'+str(exc))
                    result.page_access_result=(result.bundle_data,index.page_count,access)
                    result.page_access_pending=False
                    if not self.stop.is_set():self.page_access_ready.emit(result)
        except LoadCancelled:
            self.cancelled.emit()
        except Exception as exc:
            diagnostic_exception("background load worker", exc)
            self.failed.emit(str(exc))


class PdfImportWorker(QThread):
    progress = pyqtSignal(int)
    ready = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, source, target, parent=None):
        super().__init__(parent)
        self.source, self.target = source, target
        self.stop = threading.Event()
        self.succeeded = False

    def run(self):
        try:
            output = pdf_to_ntb(self.source, self.target, self.progress.emit, self.stop.is_set)
            self.succeeded = True
            self.ready.emit(str(output))
        except PdfImportCancelled:
            pass
        except Exception as exc:
            self.failed.emit(str(exc))


class SaveWorker(QThread):
    progress = pyqtSignal(int)
    ready = pyqtSignal(str,object)
    failed = pyqtSignal(str)
    baseline_ready = pyqtSignal(str)
    def __init__(self,source,target,hidden,added,thumbnail,base_bundle,expected_bundle,parent=None,page_plan=None,image_payload=None,native_image_changes=None,native_image_lock_changes=None,native_image_deletions=None,native_image_layer_changes=None,session=None,snapshot=None,expected_stamp=None):
        super().__init__(parent)
        self.source,self.target,self.hidden,self.added,self.thumbnail=source,target,hidden,added,thumbnail
        self.base_bundle,self.expected_bundle=base_bundle,expected_bundle
        self.stop=threading.Event();self.succeeded=False;self.page_plan=page_plan
        self.overwrite_error=None
        self.image_payload=image_payload
        self.native_image_changes=native_image_changes or {}
        self.native_image_lock_changes=native_image_lock_changes or {}
        self.native_image_deletions=native_image_deletions or set()
        self.native_image_layer_changes=native_image_layer_changes or {}
        self.session,self.snapshot=session,snapshot
        self.disk_source=source
        self.expected_stamp=expected_stamp
        self.target_stamp=file_stamp(target)
        self.committed_stamp=None
    def check_source(self):
        from zipfile import ZipFile
        if self.expected_stamp is not None and file_stamp(self.disk_source)!=self.expected_stamp:
            raise ValueError('文件已被外部修改，已停止替换；当前编辑仍保留。')
        if self.expected_bundle is not None:
            with ZipFile(self.disk_source) as current:
                if current.read('noteBundle')!=self.expected_bundle:
                    raise ValueError('保存期间文件被其他程序修改，已停止替换；当前编辑仍保留。')
    def _write(self,target):
        if self.session:
            from core.ne_save_work import session_validation
            with session_validation(self.session.validation_cache):
                return self._write_indexed(target)
        return self._write_indexed(target)

    def _write_indexed(self,target):
        self.check_source()
        source=self.session.ensure_baseline(self.stop.is_set) if self.session else self.source
        expected=self.base_bundle if self.session else self.expected_bundle
        if self.image_payload is None and not self.session:
            return write_ntb(self.source,target,self.hidden,self.added,self.progress.emit,
                             self.stop.is_set,self.thumbnail,base_bundle=self.base_bundle,
                             expected_bundle=self.expected_bundle,page_plan=self.page_plan,
                             native_image_changes=self.native_image_changes,
                             native_image_lock_changes=self.native_image_lock_changes,
                             native_image_deletions=self.native_image_deletions,native_image_layer_changes=self.native_image_layer_changes)
        # Two-phase atomic save. Rebuild native NTB in a private scratch file,
        # then pack viewer images before committing ONCE to the requested path.
        fd,stage=tempfile.mkstemp(prefix='.ne-native-',suffix='.ntb',dir=Path(target).parent)
        os.close(fd)
        try:
            bundle=write_ntb(source,stage,self.hidden,self.added,self.progress.emit,
                             self.stop.is_set,self.thumbnail,base_bundle=self.base_bundle,
                             expected_bundle=expected,page_plan=self.page_plan,
                             effective_target=target,effective_source=self.session.source if self.session else None,
                             native_image_changes=self.native_image_changes,
                             native_image_lock_changes=self.native_image_lock_changes,
                             native_image_deletions=self.native_image_deletions,native_image_layer_changes=self.native_image_layer_changes)
            if self.stop.is_set():raise LoadCancelled()
            pages,assets=self.image_payload or ([],{})
            def check_source():
                # Packing Viewer images can take time after the native writer
                # checked the source. Recheck immediately before final commit.
                self.check_source()
                # Save As must not overwrite a destination changed after the
                # user chose it. The recovery fallback always uses a new path.
                expected_target=self.target_stamp if Path(target)==Path(self.target) else None
                if file_stamp(target)!=expected_target:
                    raise ValueError('目标文件在保存期间发生变化，已停止替换；当前编辑仍保留。')
            from zipfile import ZipFile
            from images.ne_inserted_images import PREFIX,_validate_page
            for page in pages:_validate_page(page)
            with ZipFile(stage) as staged:
                needs_images=bool(assets or any(page['images'] for page in pages)
                                  or any(name.startswith(PREFIX) for name in staged.namelist()))
            if not needs_images:
                if self.stop.is_set():raise LoadCancelled()
                check_source()
                from core.ne_safecommit import replace_with_retry
                replace_with_retry(stage,target)
                return bundle
            bundle=rewrite_archive(stage,target,pages,assets,cancelled=self.stop.is_set,before_commit=check_source,native=True)
            return bundle
        finally:
            try:os.unlink(stage)
            except FileNotFoundError:pass
    def run(self):
        try:
            if self.snapshot is not None:
                from document_core import thaw
                values=thaw(self.snapshot.data)
                for name,value in values.items():setattr(self,name,value)
            if self.session and self.snapshot:
                self.baseline_ready.emit(self.session.ensure_baseline(self.stop.is_set))
                self.session.persist(self.snapshot,self.target,self.stop.is_set)
            bundle=self._write(self.target)
            self.committed_stamp=file_stamp(self.target)
            self.acknowledge_snapshot()
            self.succeeded=True;self.ready.emit(self.target,bundle)
        except LoadCancelled:
            pass
        except PermissionError as exc:
            self.overwrite_error=str(exc)
            # A locked/read-only destination cannot safely be forcibly replaced.
            # Preserve the edits in a NEW file alongside the original instead.
            # Restrict fallback to replacing the current source, not arbitrary Save As.
            if Path(self.source).resolve()!=Path(self.target).resolve() or self.stop.is_set():
                self.failed.emit(str(exc));return
            try:
                source=Path(self.source)
                fallback=None
                for index in range(1,1000):
                    candidate=source.with_name(f'{source.stem}-保存恢复{index}.ntb')
                    if not candidate.exists():
                        fallback=candidate;break
                if fallback is None:
                    raise RuntimeError('无法分配新的恢复文件名')
                bundle=self._write(str(fallback))
                self.target=str(fallback)
                self.committed_stamp=file_stamp(self.target)
                self.acknowledge_snapshot()
                self.succeeded=True
                self.ready.emit(self.target,bundle)
            except LoadCancelled:
                pass
            except Exception as recovery_error:
                self.failed.emit('原文件被 Windows 拒绝覆盖；安全副本保存也失败。\n原文件未修改，编辑仍保留。\n'+str(recovery_error))
        except Exception as exc:
            self.failed.emit(str(exc))

    def acknowledge_snapshot(self):
        # NTB replacement is already committed. Failure to remove an obsolete
        # recovery file must not be reported as a failed NTB save or trigger a
        # second write to a fallback destination.
        if self.session and self.snapshot:
            try:self.session.saved(self.snapshot.revision)
            except OSError as exc:diagnostic_exception('acknowledge saved recovery snapshot',exc)


class Viewer(QMainWindow):
    def __init__(self):
        super().__init__()
        self.document = self.prepared = self.canvas = self.worker = None
        self.saver = None
        self.pdf_importer = None
        self.pdf_import_ready_path = None
        self._pending_pdf_draft = None
        self._active_pdf_draft = None
        self._pending_new_note_draft = None
        self._active_new_note_draft = None
        self.disk_bundle = None
        self.after_save = None
        self._save_queued = False
        self.save_session = None
        self.snapshot_worker = None
        self._edit_revision = 0
        self.disk_stamp = None
        self._snapshot_timer = QTimer(self)
        self._snapshot_timer.setSingleShot(True)
        self._snapshot_timer.setInterval(2000)
        self._snapshot_timer.timeout.connect(self.persist_recovery_snapshot)
        self._save_notice = None
        self._image_compat_notice_shown=False
        self._page_history_path = None
        self._page_history_ready = False
        self._restoring_page = False
        self.zoom = 1.0
        self.fit_width = False
        self.setWindowTitle('NotabilityEditor 0.7.9-Beta')
        self.setWindowIcon(QIcon(str(icon_path())))
        self.resize(1100,800)
        self.setMinimumSize(720,400)
        self.setAcceptDrops(True)
        self.scroll = QScrollArea()
        self.scroll.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.scroll.setStyleSheet('QScrollArea { background: #42464d; border: 0; }')
        self.setCentralWidget(self.scroll)
        from rendering.ne_gpuview import GpuViewport
        self.gpu_settings=QSettings('NotabilityEditor','Display')
        gpu_enabled=self.gpu_settings.value('gpu_acceleration',True,type=bool)
        self.gpu=GpuViewport(self.scroll,gpu_enabled,self)
        self.gpu.changed.connect(self.gpu_state_changed)
        self._install_drop_target(self.scroll)
        self._install_drop_target(self.scroll.viewport())
        self.read_toolbar=self.addToolBar('阅读工具');self.read_toolbar.setMovable(False)
        self.read_toolbar.setStyleSheet('QToolBar { background:#ffffff; border:0; spacing:0; padding:0; }')
        self.reading_panel=ReadingPanel(self);self.read_toolbar.addWidget(self.reading_panel)
        self.enhance = self.action(self,'增强页面',self.toggle_enhance)
        self.enhance.setCheckable(True)
        self.strength = QSlider(Qt.Orientation.Horizontal)
        self.strength.setRange(0,100)
        self.strength.setValue(60)
        self.strength.setFixedWidth(90)
        self.strength.setEnabled(False)
        self.strength.setToolTip('0% 保留原图；数值越高，黑白反差越柔和。彩色图表和手写笔迹保持原色。')
        self.strength_label = QLabel('60% ')
        self.strength_timer = QTimer(self)
        self.strength_timer.setSingleShot(True)
        self.strength_timer.setInterval(150)
        self.strength_timer.timeout.connect(self.apply_strength)
        self.strength.valueChanged.connect(self.schedule_strength)
        self.ink = self.action(self,'显示笔迹',self.toggle_ink)
        self.ink.setCheckable(True)
        self.ink.setChecked(True)
        self.scroll.verticalScrollBar().valueChanged.connect(self.sync_page)
        self.color_settings=QSettings('NotabilityEditor','NotabilityEditor')
        self._page_history_timer=QTimer(self)
        self._page_history_timer.setSingleShot(True)
        self._page_history_timer.setInterval(650)
        self._page_history_timer.timeout.connect(self._remember_current_page)
        self.scroll.horizontalScrollBar().valueChanged.connect(self._schedule_page_history)
        self._reading_page_sync_timer=QTimer(self)
        self._reading_page_sync_timer.setSingleShot(True)
        self._reading_page_sync_timer.setInterval(32)
        self._reading_page_sync_timer.timeout.connect(self._flush_reading_page_sync)
        self.sampling_precision_action=self.action(self,'采样精度…',self.configure_sampling_precision)
        self.sampling_precision_action.setToolTip('设置新笔迹的最小采样间距；数值越小，保留的细节越多')
        self.smart_interpolation_action=self.action(self,'智能插点',self.toggle_smart_interpolation)
        self.smart_interpolation_action.setCheckable(True)
        self.smart_interpolation_action.setChecked(
            self.color_settings.value('draw/smart_interpolation',True,type=bool))
        self.smart_interpolation_action.setToolTip('开启时对新笔迹使用向心三次曲线；关闭时使用原有曲线，旧笔迹保持不变')
        self.build_edit_toolbar()
        build_menus(self)
        self.reading_panel.new_note_button.setDefaultAction(self.new_note_action)
        self.reading_panel.new_note_button.setText('新建笔记')
        self.reading_panel.bind_export_actions(self)
        self.reading_panel.pdf_note_button.setDefaultAction(self.pdf_note_action)
        self.reading_panel.pdf_note_button.setText('导入 PDF')
        self.reading_panel.pdf_note_button.setIcon(tool_icon('import'))
        self.reading_panel.pdf_note_button.setObjectName('createPdfNote')
        self.reading_panel.pdf_note_button.setFixedSize(102,38)
        self.reading_panel.pdf_note_button.setToolTip('导入 1～5000 页 PDF 为可编辑 NTB；支持横向、自定义和混合尺寸（Ctrl+Shift+N）')
        self.page_sidebar=PageSidebar(self);self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea,self.page_sidebar)
        self._install_drop_target(self.page_sidebar)
        self._install_drop_target(self.page_sidebar.list.viewport())
        self.resizeDocks([self.page_sidebar],[280],Qt.Orientation.Horizontal)
        self.menus['页面']=QMenu('页面',self);style_menu(self.menus['页面'])
        self.seamless_pages_action=self.action(self,'页面无缝衔接（支持跨页绘制）',self.toggle_seamless_pages)
        self.seamless_pages_action.setCheckable(True)
        self.seamless_pages_action.setChecked(self.color_settings.value('view/seamless_pages',False,type=bool))
        self.seamless_pages_action.setToolTip('去除页间空隙；画笔、荧光笔和橡皮擦可连续跨页，一次操作可一次撤销')
        self.menus['页面'].addAction(self.seamless_pages_action)
        self.menus['页面'].addSeparator()
        self.menus['页面'].addActions(self.page_sidebar.menu.actions())
        self.menus['页面'].aboutToShow.connect(self.page_sidebar.refresh)
        self.seamless_pages_action.setEnabled(False)
        self.menuBar().insertMenu(self.menus['视图'].menuAction(),self.menus['页面'])
        self.page_toggle_action=self.page_sidebar.toggleViewAction()
        self.page_toggle_action.triggered.connect(self.page_sidebar.set_user_visible)
        self.page_toggle_action.setText('页面管理');self.page_toggle_action.setIcon(tool_icon('pages'))
        self.page_toggle_action.setIconVisibleInMenu(False);self.page_toggle_action.setShortcut(QKeySequence('Ctrl+Shift+P'))
        self.editor_panel.page_button.setDefaultAction(self.page_toggle_action)
        self.addAction(self.page_toggle_action)
        from ui.ne_editor_view import EditorView
        self.editor_view=EditorView(self)
        self.shortcut_manager=ShortcutManager(self)
        self.shortcut_manager.apply()
        self.page_sidebar.visibilityChanged.connect(self.page_sidebar_visibility_changed)
        self.page_sidebar.setVisible(self.color_settings.value('view/page_sidebar_visible',True,type=bool))
        self.page_sidebar_visibility_changed(not self.page_sidebar.isHidden())
        self.menus['视图'].addSeparator();self.menus['视图'].addAction(self.page_toggle_action)
        refresh_menu_state(self)
        from ui.ne_workspace import DesktopWorkspace
        self.workspace = DesktopWorkspace(self)
        recovery_action=self.action(self,'恢复未保存的笔记…',self.recover_unsaved_note)
        self.menus['文件'].addAction(recovery_action)
        refresh_menu_state(self)
        QTimer.singleShot(0,self.offer_recovery)

    def show_default_settings(self):
        DefaultAppsDialog(self).exec()

    def page_sidebar_visibility_changed(self,visible):
        label='收起页面管理' if visible else '展开页面管理'
        self.page_toggle_action.setToolTip(label+'（Ctrl+Shift+P）')
        self.editor_panel.page_button.setAccessibleName(label)
        if self.fit_width:QTimer.singleShot(0,self.fit)

    def build_edit_toolbar(self):
        self.addToolBarBreak()
        self.edit_toolbar=self.addToolBar('笔迹编辑');self.edit_toolbar.setMovable(False)
        self.edit_toolbar.setStyleSheet('QToolBar { background:#ffffff; border:0; spacing:0; padding:0; }')
        self.editor_panel=EditorPanel(self);self.edit_toolbar.addWidget(self.editor_panel)
        self.edit_toolbar.setEnabled(False)

    def update_edit_settings(self,*args):
        if hasattr(self.canvas,'edits'):
            self.canvas.pen_color=self.current_color;self.canvas.pen_width=self.pen_size.value()
            self.canvas.highlight_color=self.current_highlight_color
            self.canvas.highlight_width=self.highlight_size.value()
            self.canvas.highlight_opacity=self.highlight_opacity.value()/100.0
            self.canvas.tape_color=self.current_tape_color
            self.canvas.tape_width=self.tape_size.value()
            self.canvas.tape_pattern=self.current_tape_pattern
            self.canvas.eraser_width=self.erase_size.value()
            self.canvas.eraser_mode='partial' if self.erase_mode.currentIndex()==0 else 'stroke'
            self.canvas._syncBrushCursor()
            self.canvas.update()

    def remember_tool_setting(self,key,value):
        self.color_settings.setValue(key,value)
        self.color_settings.sync()

    def _page_history_target(self,path,*,allow_draft=False):
        from pathlib import Path
        from settings.ne_recent_files import canonical_path
        if not path:
            return None
        if not allow_draft and self._is_unsaved_draft():
            return None
        value=Path(path)
        if value.suffix.lower() not in ('.ntb','.note') or not value.is_file():
            return None
        return canonical_path(value)

    def _remember_current_page(self):
        timer=getattr(self,'_page_history_timer',None)
        if timer is not None:timer.stop()
        if (not self._page_history_ready or not self._page_history_path
                or self._restoring_page or not self.canvas):
            return False
        try:
            from settings.ne_page_history import remember_last_position
            vertical=0.0;horizontal=0.0
            if hasattr(self,'scroll'):
                vbar=self.scroll.verticalScrollBar()
                hbar=self.scroll.horizontalScrollBar()
                if hasattr(self.canvas,'pageAt') and vbar.maximum()>0:
                    page=self.canvas.pageAt(vbar.value()+12)
                    top=self.canvas.pageTop(page);height=self.canvas.draw_sizes[page][1]
                    if height>0:
                        vertical=min(1.0,max(0.0,(vbar.value()-top)/height))
                if hbar.maximum()>0:
                    horizontal=hbar.value()/hbar.maximum()
            return remember_last_position(
                self.color_settings,self._page_history_path,int(self.page.value()),
                zoom=self.zoom,vertical=vertical,horizontal=horizontal,
                fit_width=self.fit_width)
        except (OSError,TypeError,ValueError) as exc:
            diagnostic_exception("record page history",exc)
            return False

    def _schedule_page_history(self):
        if self._page_history_ready and self._page_history_path and not self._restoring_page:
            self._page_history_timer.start()

    def _apply_history_view(self,expected_path,page,vertical,horizontal):
        if expected_path!=self._page_history_path or not self.canvas:
            return
        self._restoring_page=True
        try:
            page=max(1,min(self.canvas.page_count,int(page)))
            if hasattr(self.canvas,'pageTop'):
                top=self.canvas.pageTop(page-1)
                height=self.canvas.draw_sizes[page-1][1]
                fraction=min(1.0,max(0.0,float(vertical)))
                self.scroll.verticalScrollBar().setValue(
                    round(top+fraction*height))
                self.page.setValue(page)
                if hasattr(self,'page_sidebar'):self.page_sidebar.sync(page-1)
            hbar=self.scroll.horizontalScrollBar()
            fraction=min(1.0,max(0.0,float(horizontal)))
            hbar.setValue(round(hbar.maximum()*fraction))
        finally:self._restoring_page=False

    def _restore_page_history(self,path,page_count):
        self._page_history_ready=False
        target=self._page_history_target(path)
        if target is None:return None,None
        try:
            from settings.ne_page_history import last_position_for
            position=last_position_for(self.color_settings,target)
        except (OSError,TypeError,ValueError) as exc:
            diagnostic_exception("read page history",exc)
            position=None
        if position is None:
            self._page_history_path=target
            self._page_history_ready=True
            return None,None
        restored=max(1,min(page_count,position.page))
        self._restoring_page=True
        try:
            if position.zoom is not None:
                if position.fit_width:
                    self.fit()
                else:
                    self.fit_width=False
                    self.apply_zoom(position.zoom)
            self.go_page(restored)
        finally:self._restoring_page=False
        self._page_history_path=target
        self._page_history_ready=True
        QTimer.singleShot(
            0,lambda p=target,page=restored,v=position.vertical,
                    h=position.horizontal:self._apply_history_view(p,page,v,h))
        return position,restored

    def _sync_color_selection(self,tool):
        """Restore exclusive swatch selection, also when a color dialog is cancelled."""
        if tool=='draw':
            action=(self.custom_pen_action if self.pen_color_is_custom else self.color_actions[self.current_color])
        else:
            action=(self.custom_highlight_action if self.highlight_color_is_custom
                    else self.highlight_color_actions[self.current_highlight_color])
        action.setChecked(True)
        favorites=(self.editor_panel.pen_favorites if tool=='draw' else self.editor_panel.highlight_favorites)
        favorites.sync_selection()

    def select_color(self,color):
        self.current_color=color;self.pen_color_is_custom=False
        self.color_settings.setValue('draw/preset_color',color)
        self.color_settings.setValue('draw/use_custom',False)
        self.color_settings.sync()
        self._sync_color_selection('draw')
        self.tools['draw'].setChecked(True);self.select_tool('draw')

    def select_highlight_color(self,color):
        self.current_highlight_color=color;self.highlight_color_is_custom=False
        self.color_settings.setValue('highlight/preset_color',color)
        self.color_settings.setValue('highlight/use_custom',False)
        self.color_settings.sync()
        self._sync_color_selection('highlight')
        self.tools['highlight'].setChecked(True);self.select_tool('highlight')

    def choose_custom_color(self,tool,replace_color=None):
        """Pick arbitrary RGB; highlight alpha remains controlled by its opacity setting."""
        if tool=='draw':
            initial=self.current_color
            title='自定义画笔颜色'
        elif tool=='highlight':
            initial=self.current_highlight_color
            title='自定义荧光笔颜色'
        else:
            raise ValueError('Unknown color tool')
        palette=self.pen_palette if tool=='draw' else self.highlight_palette
        dialog=CustomColorDialog(replace_color or initial,palette,title,self,edit_color=replace_color)
        dialog.pick_button.setIcon(tool_icon('eyedropper'))
        button=(self.editor_panel.custom_pen_button if tool=='draw' else self.editor_panel.custom_highlight_button)
        screen=button.screen().availableGeometry()
        anchor=button.mapToGlobal(button.rect().bottomLeft())
        dialog.move(max(screen.left(),min(anchor.x(),screen.right()-dialog.width())),
                    max(screen.top(),min(anchor.y()+8,screen.bottom()-dialog.height())))
        try:
            if dialog.exec()==CustomColorDialog.DialogCode.Accepted:
                self.apply_custom_color(tool,dialog.color.name(QColor.NameFormat.HexRgb))
            else:
                self._sync_color_selection(tool)
        finally:
            dialog.deleteLater()

    def apply_custom_color(self,tool,rgb):
        """A favorite is a remembered custom RGB; highlight opacity stays independent."""
        if tool=='draw':
            self.custom_pen_color=self.current_color=rgb
            self.pen_color_is_custom=True
            self.editor_panel.custom_pen_button.setColor(rgb)
        elif tool=='highlight':
            self.custom_highlight_color=self.current_highlight_color=rgb
            self.highlight_color_is_custom=True
            self.editor_panel.custom_highlight_button.setColor(rgb)
        else:
            raise ValueError('Unknown color tool')
        self.color_settings.setValue(tool+'/custom_color',rgb)
        self.color_settings.setValue(tool+'/use_custom',True)
        self.color_settings.sync()
        self._sync_color_selection(tool)
        self.tools[tool].setChecked(True)
        self.select_tool(tool)

    def restore_edit_tool(self):
        tool = self.color_settings.value('editor/last_tool', 'read')
        if not isinstance(tool, str) or tool not in self.tools:tool = 'read'
        if not self.prepared.path.lower().endswith('.ntb'):tool = 'read'
        self.tools[tool].setChecked(True)
        self.editor_panel.set_tool(tool)
        if hasattr(self.canvas, 'setEditTool'):
            if tool != 'read' and not self.ink.isChecked():
                self.ink.setChecked(True);self.canvas.setInkVisible(True)
            self.canvas.setEditTool(tool)

    def select_tool(self,tool,*,remember=True):
        if self.pdf_importer or self.worker:return
        if not hasattr(self.canvas,'setEditTool'):return
        if not self.tools[tool].isEnabled():return
        if tool!='read' and not self.ink.isChecked():
            self.ink.setChecked(True);self.canvas.setInkVisible(True)
        self.tools[tool].setChecked(True);self.editor_panel.set_tool(tool)
        self.update_edit_settings();self.canvas.setEditTool(tool)
        if remember and self.prepared and self.prepared.path.lower().endswith('.ntb'):
            self.color_settings.setValue('editor/last_tool', tool)
            self.color_settings.sync()
        self.canvas.setFocus(Qt.FocusReason.OtherFocusReason)
        if tool=='read':self.statusBar().showMessage('阅读：按住页面拖动可平移，松手后短暂惯性滑动；阅读位置会自动记忆')
        if tool=='select':self.statusBar().showMessage('工具 → 选择：自由形状 / 矩形 · Shift 追加 · Alt 减选 · 选框内拖动 · Ctrl+Z 撤销')
        if tool=='image':self.statusBar().showMessage('选中图片后可跨页拖动，或点击“移动到页面…”；已锁定图片需先解锁，四角控制点用于等比例缩放。')
        if tool=='erase':self.statusBar().showMessage('橡皮擦仅擦除笔迹 · 可拖动连续擦除 · Ctrl+Z 撤销 · Esc 取消当前操作')
        if tool=='draw':self.statusBar().showMessage('画笔：移入页面可预览笔尖 · 宽度 0.1～128 · Ctrl+Z 撤销')
        if tool=='highlight':self.statusBar().showMessage('荧光笔：移入页面可预览半透明笔尖 · 宽度 0.1～256 · Ctrl+Z 撤销')
        if tool=='tape':self.statusBar().showMessage('遮字胶带：拖动遮盖内容 · Ctrl+Shift+T 临时揭开/恢复 · 选择工具移动 · 橡皮擦移除')

    def insert_image(self):
        if (not self.prepared or not hasattr(self.canvas,'image_layers') or
                not self.prepared.path.lower().endswith('.ntb') or self.worker):return
        source,_=QFileDialog.getOpenFileName(self,'选择要插入的图片','','图片 (*.png *.jpg *.jpeg *.webp *.bmp)')
        if not source:return
        if not self._image_compat_notice_shown:
            QMessageBox.information(self,'图片保存兼容性',
                '新插入的图片会保存在 NTB 内的 NotabilityEditor 专用图层中。\n'
                '本软件能重新打开、移动、等比例缩放、固定并导出含图片的 PDF；但这不是已验证的官方 Notability 原生图片事件，'
                '官方 Notability 可能无法显示这些新增图片。\n\n'
                '如需跨设备查看图片，请同时导出 PDF。')
            self._image_compat_notice_shown=True
        try:
            if Path(source).stat().st_size>20*1024*1024:raise ValueError('图片原始文件不能超过 20 MB')
            reader=QImageReader(source)
            reader.setAutoTransform(True)
            dimensions=reader.size()
            if not dimensions.isValid() or dimensions.width()*dimensions.height()>16_000_000:
                raise ValueError('图片像素总数不能超过 1600 万')
            image=reader.read()
            if image.isNull():raise ValueError(reader.errorString() or '不支持的图片格式')
            from PyQt6.QtCore import QByteArray,QBuffer,QIODevice
            array=QByteArray();buffer=QBuffer(array)
            if not buffer.open(QIODevice.OpenModeFlag.WriteOnly):raise ValueError('无法创建图片缓冲区')
            if not image.save(buffer,'PNG'):raise ValueError('图片转换为 PNG 失败')
            buffer.close()
            blob=bytes(array)
            if len(blob)>20*1024*1024:raise ValueError('PNG 图片数据不能超过 20 MB')
            page=max(0,min(self.canvas.page_count-1,self.page.value()-1))
            self.canvas.addImage(blob,image.width(),image.height(),page)
            self.select_tool('image')
            self.statusBar().showMessage('图片已插入：默认位于背景上一层。点击选中后可拖动移动、通过四角控制点等比例缩放。')
        except Exception as exc:
            QMessageBox.warning(self,'图片插入失败',str(exc))

    def move_selection_to_page(self):
        from PyQt6.QtWidgets import QInputDialog
        canvas=self.canvas
        if canvas is None or not hasattr(canvas,'moveSelectedToPage'):return
        canvas.finishGesture()
        selection=canvas.selected_image
        source=selection[0] if selection else canvas.selection_page
        if source is None:return
        target,ok=QInputDialog.getInt(self,'移动到页面','目标页码：',
                                    min(canvas.page_count,source+2),1,canvas.page_count)
        if not ok:return
        try:
            if canvas.moveSelectedToPage(target-1):
                self.go_page(target)
                self.statusBar().showMessage(f'已移动到第 {target} 页，可按 Ctrl+Z 撤销',5000)
                self.edit_changed()
        except ValueError as exc:
            QMessageBox.information(self,'暂时无法移动',str(exc))

    def image_operation(self,name):
        """Apply one selected-image command, with visible native-lock feedback.

        A supported inline native lock changes at the FIRST click. The old
        QMessageBox used a default No button, silently cancelling what looked
        like an enabled toolbar operation. The experimental compatibility
        warning now belongs in the tooltip/README, not a blocking dialog.
        """
        canvas=getattr(self,'canvas',None)
        if canvas is None or not hasattr(canvas,'updateImageAction'):
            return
        selection=getattr(canvas,'selected_image',None)
        if not selection:
            self.statusBar().showMessage('请先点击选中一张图片',5000)
            return
        page,identifier=selection
        is_native=str(identifier).startswith('native:')
        item=canvas.editableImageItem(page,identifier)
        if item is None:
            self.statusBar().showMessage('所选图片已不存在，请重新选择',5000)
            return
        if is_native and name=='toggle_lock' and not item.get('lock_editable',False):
            QMessageBox.information(self,'这类官方图片暂不能解锁',
                '此原生图片缺少可编辑的结构，已阻止锁定状态写回。')
            return
        if is_native and name=='delete':
            answer=QMessageBox.warning(
                self,'确认隐藏这张官方图片',
                '删除将在当前 Viewer 中隐藏这张图片，保存时追加官方 type-25 可见性事件。\n'
                '原生图片及其素材仍完整保留，不执行物理删除。\n\n'
                '保存前可使用 Ctrl+Z 撤销隐藏。'
                '建议只对笔记副本操作。确认隐藏吗？',
                QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if answer!=QMessageBox.StandardButton.Yes:return
        if canvas.updateImageAction(name):
            # updateImageAction emits editsChanged; repeat refresh here so the
            # shared menu QAction and secondary toolbar are in sync even when
            # the source canvas emits signals during another edit gesture.
            refresh_menu_state(self)
            if name=='delete' and is_native:
                self.statusBar().showMessage('原生图片已隐藏：保存后追加官方 type-25 可见性事件；素材仍保留在 NTB 中（请用副本验证）',7000)
            if name=='toggle_lock':
                updated=canvas.editableImageItem(page,identifier)
                locked=bool(updated and updated.get('locked'))
                self.statusBar().showMessage(
                    ('图片已锁定：移动和缩放已禁用' if locked else
                     '图片已解除锁定：现在可以拖动、等比例缩放')+
                    ('；保存后追加官方 type-23 状态事件（请用副本进行官方导入验证）' if is_native else ''),
                    7000)
            else:
                self.statusBar().showMessage('图片操作已完成，保存后写入 NTB',5000)
        else:
            # Never present an enabled control that appears to do nothing.
            self.statusBar().showMessage('当前图片不能执行该操作；请查看图片状态或重新选择',6000)

    def edit_changed(self):
        refresh_menu_state(self)
        if hasattr(self,'page_sidebar'):self.page_sidebar.invalidate(ink_change=True)
        model=self.canvas.edits if hasattr(self.canvas,'edits') else None
        editable=bool(self.prepared and self.prepared.path.lower().endswith('.ntb') and not self.worker and not self.pdf_importer)
        self.undo_action.setEnabled(bool(editable and model and model.can_undo))
        self.redo_action.setEnabled(bool(editable and model and model.can_redo))
        self._edit_revision+=1
        if editable and self.save_session and self._has_unsaved_changes() and not self._snapshot_timer.isActive():
            self._snapshot_timer.start()
        # Keep the window title fixed to the app name and version.
        # File information remains available in the status bar and page panel.

    def _has_unsaved_changes(self):
        canvas=self.canvas
        return bool(canvas and hasattr(canvas,'edits') and
                    (canvas.edits.dirty or canvas.image_layers.dirty or
                     canvas.official_image_edits.dirty or
                     (self._is_unsaved_draft() and not self._unsaved_draft_is_empty())))

    def persist_recovery_snapshot(self):
        if not self.save_session or not self._has_unsaved_changes():return
        if self.snapshot_worker or getattr(self.canvas.edits,'pending',None):
            self._snapshot_timer.start();return
        try:
            snapshot=capture_save(self.canvas,self._edit_revision)
        except Exception as exc:
            diagnostic_exception('capture recovery snapshot',exc)
            self.statusBar().showMessage('本地恢复快照未完成，编辑仍保留：'+str(exc),6000)
            return
        worker=SnapshotWorker(self.save_session,snapshot,self.prepared.path,self)
        self.snapshot_worker=worker
        worker.baseline_ready.connect(self.use_save_baseline)
        worker.failed.connect(lambda message:self.statusBar().showMessage('本地恢复快照未完成：'+message,6000))
        def finished():
            if self.snapshot_worker is worker:self.snapshot_worker=None
            worker.deleteLater()
            if self.save_session and self._has_unsaved_changes() and self._edit_revision>snapshot.revision:
                self._snapshot_timer.start()
        worker.finished.connect(finished)
        worker.start()

    def use_save_baseline(self,path):
        if self.save_session and str(self.save_session.baseline)==path and self.prepared:
            self.prepared.archive_path=path
            self.canvas.archive_path=path

    def offer_recovery(self):
        from editing.ne_save_session import scan_recoveries
        if self.worker or self.prepared:return
        scan=scan_recoveries()
        self.show_recovery_issues(scan.issues)
        if scan.entries:
            choice=QMessageBox.question(self,'发现未保存的笔记',
                '上次编辑留下了本地恢复快照。是否恢复为新的 NTB？原文件保持不变。',
                QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes)
            if choice==QMessageBox.StandardButton.Yes:self.recover_unsaved_note(scan=scan)

    def show_recovery_issues(self,issues):
        if not issues:return
        message=QMessageBox(self)
        message.setIcon(QMessageBox.Icon.Warning)
        message.setWindowTitle('部分恢复快照无法读取')
        message.setText(f'发现 {len(issues)} 个无法读取的恢复会话。原始恢复文件已保留，请勿删除。')
        message.setInformativeText('其他完好的快照仍可继续恢复。可展开详细信息查看目录和原因。')
        message.setDetailedText('\n\n'.join(str(issue.folder)+'\n'+issue.reason for issue in issues))
        message.setStandardButtons(QMessageBox.StandardButton.Ok)
        message.exec()

    def recover_unsaved_note(self,checked=False,*,scan=None):
        from editing.ne_save_session import scan_recoveries,read_recovery
        from PyQt6.QtWidgets import QInputDialog
        if self.workspace.busy():return
        if scan is None:
            scan=scan_recoveries()
            self.show_recovery_issues(scan.issues)
        entries=scan.entries
        if not entries:
            if scan.issues:return
            QMessageBox.information(self,'恢复笔记','没有待恢复的本地快照。');return
        labels=[f"{Path(payload['target']).name} · {payload['updated']} · {folder.name[:8]}" for folder,payload in entries]
        selected,ok=QInputDialog.getItem(self,'恢复笔记','选择未保存的笔记',labels,0,False)
        if not ok:return
        folder,payload=entries[labels.index(selected)]
        default=str(Path(payload['target']).with_name(Path(payload['target']).stem+'-恢复.ntb'))
        target,_=QFileDialog.getSaveFileName(self,'恢复为新的 NTB',default,'Notability Bundle (*.ntb)')
        if not target:return
        if not target.lower().endswith('.ntb'):target+='.ntb'
        if Path(target).exists():
            QMessageBox.warning(self,'请选择新文件','恢复必须保存到新的文件，避免覆盖现有笔记。');return
        if not self.confirm_leave(lambda:self.recover_unsaved_note()):return
        try:
            data=read_recovery(folder)
            from zipfile import ZipFile
            source=str(folder/'baseline.ntb')
            with ZipFile(source) as archive:bundle=archive.read('noteBundle')
        except Exception as exc:
            QMessageBox.warning(self,'恢复失败','快照已保留：'+str(exc));return
        self.save_progress=prepare_progress(QProgressDialog('正在恢复 NTB…','取消',0,100,self),'恢复笔记')
        self.save_progress.setWindowModality(Qt.WindowModality.NonModal)
        worker=SaveWorker(source,target,base_bundle=bundle,expected_bundle=bundle,parent=self,**data)
        self.saver=worker
        worker.progress.connect(self.save_progress.setValue)
        worker.failed.connect(lambda message:QMessageBox.warning(self,'恢复失败，快照已保留',message))
        self.save_progress.canceled.connect(worker.stop.set)
        worker.ready.connect(lambda path,bundle:self.statusBar().showMessage('恢复副本已保存：'+path))
        def finished():
            success=worker.succeeded
            self.save_progress.close();worker.deleteLater();self.saver=None
            refresh_menu_state(self)
            if success:
                self._recovery_to_clear=(folder,str(Path(target).resolve()))
                QTimer.singleShot(0,lambda:self.open_file(target))
        worker.finished.connect(finished)
        refresh_menu_state(self);worker.start()

    def _is_unsaved_pdf_draft(self):
        draft=getattr(self,'_active_pdf_draft',None)
        return bool(self.prepared and draft and draft.matches(self.prepared.path))

    def _is_unsaved_new_note_draft(self):
        draft=getattr(self,'_active_new_note_draft',None)
        return bool(self.prepared and draft and draft.matches(self.prepared.path))

    def _active_unsaved_draft(self):
        if self._is_unsaved_pdf_draft():
            return self._active_pdf_draft
        if self._is_unsaved_new_note_draft():
            return self._active_new_note_draft
        return None

    def _is_unsaved_draft(self):
        return self._active_unsaved_draft() is not None

    def _unsaved_draft_kind(self):
        draft=self._active_unsaved_draft()
        return getattr(draft,'kind',None) if draft else None

    def _unsaved_draft_is_empty(self):
        """Inspect current pages, not undo history or the paper pattern."""
        canvas=self.canvas
        if not isinstance(canvas,PdfNoteCanvas):
            return False
        if any(index is not None for index in canvas.pdf_pages):
            return False
        deleted=canvas.official_image_edits.deletion_changes
        for page in range(canvas.page_count):
            if canvas.edits.state(page)[0]:
                return False
            key=canvas.edits.page_key(page)
            if canvas.image_layers.items(key):
                return False
            if any(int(image.get('data-event-id',-1)) not in deleted
                   for image in canvas.images.get(page,())):
                return False
        if any(canvas.native_text.values()):
            return False
        return True

    def confirm_leave(self,after,continue_label="继续"):
        if hasattr(self.canvas,'finishGesture'):self.canvas.finishGesture()
        draft_kind=self._unsaved_draft_kind()
        is_draft=draft_kind is not None
        if is_draft and self._unsaved_draft_is_empty():
            return True
        image_dirty=bool((hasattr(self.canvas,'image_layers') and self.canvas.image_layers.dirty)
                         or (hasattr(self.canvas,'official_image_edits') and self.canvas.official_image_edits.dirty))
        if not is_draft and not image_dirty and (not hasattr(self.canvas,'edits') or not self.canvas.edits.dirty):
            return True
        changes=[]
        edits=getattr(self.canvas,'edits',None)
        if edits is not None and edits.dirty:
            changes.append('手写笔迹、选区或页面顺序')
        native_images=getattr(self.canvas,'official_image_edits',None)
        if native_images is not None:
            if native_images.changes:changes.append('图片位置、大小或所属页面')
            if native_images.lock_changes:changes.append('图片锁定状态')
            if native_images.deletion_changes:changes.append('图片删除')
            if native_images.layer_changes:changes.append('图片与其他内容的图层顺序')
        viewer_images=getattr(self.canvas,'image_layers',None)
        if viewer_images is not None and viewer_images.dirty:
            changes.append('Viewer 插入的图片')
        note_name=Path(self.prepared.path).name if self.prepared and self.prepared.path else '当前笔记'
        choice=show_unsaved_changes_dialog(
            self,
            note_name=note_name,
            changes=changes,
            draft_kind=draft_kind,
            continue_label=continue_label,
        )
        if choice==CHOICE_DISCARD:return True
        if choice==CHOICE_SAVE:self.save_ntb(after=after)
        return False

    def save_ntb(self,after=None,save_as=False):
        if self.saver:
            if not save_as:
                self._save_queued = True
                if after:self.after_save = after
                self.statusBar().showMessage('正在后台保存，最新修改将在本次完成后继续保存…')
            return
        if self.worker or self.pdf_importer or not self.prepared or not self.prepared.path.lower().endswith('.ntb'):return
        self.canvas.finishGesture()
        draft=self._active_unsaved_draft()
        first_save=draft is not None
        if (not save_as and not first_save and not self.canvas.edits.dirty
                and not self.canvas.image_layers.dirty
                and not self.canvas.official_image_edits.dirty):
            self.statusBar().showMessage('当前文件已保存，没有新的修改')
            if after:
                QTimer.singleShot(0,after)
            return
        target=self.prepared.path
        if first_save or save_as:
            library_folder = None
            default=(str(draft.suggested_path) if first_save else
                     str(Path(target).with_name(Path(target).stem+'-副本.ntb')))
            if first_save and hasattr(self, 'workspace'):
                from ui.home.library import category_directory
                import uuid
                folder = category_directory() / ('note-' + uuid.uuid4().hex)
                try:
                    folder.mkdir(parents=True, exist_ok=False)
                except OSError as exc:
                    QMessageBox.warning(self, '无法创建笔记存储目录', str(exc))
                    return
                default = str(folder / Path(default).name)
                library_folder = folder
            dialog_title=(
                '保存新建笔记为 NTB' if first_save and getattr(draft,'kind',None)=='new'
                else '首次保存 PDF 笔记为 NTB' if first_save
                else '另存为 NTB')
            target,_=QFileDialog.getSaveFileName(self,dialog_title,default,'Notability Bundle (*.ntb)')
            if library_folder and (not target or Path(target).parent.resolve() != library_folder.resolve()):
                try:
                    library_folder.rmdir()
                except OSError:
                    pass
            if not target:return
            if not target.lower().endswith('.ntb'):target+='.ntb'
            if first_save and Path(target).resolve()==Path(self.prepared.path).resolve():
                QMessageBox.warning(self,'请选择正式保存位置','不能将临时缓存当作正式保存位置。请重新选择文件夹。')
                return
            if first_save and Path(target).exists():
                decision=QMessageBox.warning(
                    self,'确认覆盖已有 NTB',
                    '所选 NTB 文件已经存在。继续保存将替换该文件，无法撤销。是否覆盖？',
                    QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No)
                if decision!=QMessageBox.StandardButton.Yes:return
            # An ordinary Save As is *not* a page-rebuilding operation.
            # Preserve official NTB event history, independent assets and PDF
            # index as-is, regardless of original page count or page layout.
            # Only an explicitly changed page_plan may enter a destructive path.
        target=str(Path(target).resolve())
        plan=self.canvas.edits.page_plan() if hasattr(self.canvas.edits,'page_plan') else None
        if plan is not None and getattr(self.canvas,'page_edit_kind',None) in (
                'official_grouped_pdf','official_grouped_paper',
                'materialized_grouped_pdf','ntb18_mixed_pdf'):
            choice=QMessageBox.question(self,'官方分组页面保存确认',
                ('本次保存将重新排列原生纸张页面，保留原始笔迹数据、图片及资源。\n'
                 '此写入方式仍需官方 Notability 实机验证。\n\n'
                 if self.canvas.page_edit_kind=='official_grouped_paper' else
                 '本次保存将按新页面顺序重建 PDF 底图，并重新编码可见原生笔迹。\n'
                 '基本画笔笔迹可以保留，但原始笔压、部分曲线细节及未知笔迹属性可能变化。\n\n')
                + '强烈建议先使用“另存为”保留备份，再到官方 Notability 检查。是否继续？',
                QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if choice!=QMessageBox.StandardButton.Yes:return
        try:
            snapshot=capture_save(self.canvas,self._edit_revision,self.canvas.thumbnailBytes())
            data=snapshot.data
            if ((data['native_image_changes'] or data['native_image_lock_changes'] or data['native_image_deletions'] or data['native_image_layer_changes'])
                    and plan is not None and self.canvas.page_edit_kind!='native_per_page'):
                raise ValueError('此分组笔记的图片修改与页面调整需分次保存。当前编辑仍保留。')
        except Exception as exc:
            QMessageBox.warning(self,'无法准备导出',str(exc));return
        self.after_save=after
        self._save_notice=None
        # Only an explicit Save As (or first PDF draft save) should display a path.
        self._save_request_show_path=bool(save_as or first_save)
        self.save_progress=prepare_progress(QProgressDialog('正在写入并校验 NTB…','取消',0,100,self),'正在保存笔记')
        self.save_progress.setWindowModality(Qt.WindowModality.NonModal)
        self.save_progress.setMinimumDuration(2147483647);self.save_progress.setAutoClose(False)
        self._save_status=QWidget(self)
        status_layout=QHBoxLayout(self._save_status);status_layout.setContentsMargins(0,0,0,0)
        status_progress=QProgressBar();status_progress.setRange(0,100);status_progress.setFixedWidth(110)
        cancel_save=QPushButton('取消保存');cancel_save.setFlat(True)
        status_layout.addWidget(status_progress);status_layout.addWidget(cancel_save)
        self.statusBar().addPermanentWidget(self._save_status)
        self.saver=SaveWorker(self.prepared.path,target,base_bundle=self.prepared.bundle_data,
                              expected_bundle=self.disk_bundle,parent=self,session=self.save_session,
                              snapshot=snapshot,expected_stamp=self.disk_stamp,**data)
        self.saver.progress.connect(self.save_progress.setValue)
        self.saver.progress.connect(status_progress.setValue)
        self.saver.baseline_ready.connect(self.use_save_baseline)
        self.saver.ready.connect(self.saved_native)
        self.saver.failed.connect(lambda message:QMessageBox.warning(self,'保存失败，编辑内容仍保留',message))
        self.saver.finished.connect(self.save_finished)
        self.save_progress.canceled.connect(self.saver.stop.set)
        cancel_save.clicked.connect(self.saver.stop.set)
        self.open_action.setEnabled(False)
        self.statusBar().showMessage('正在后台保存，可继续编辑…')
        refresh_menu_state(self)
        self.saver.start()

    def saved_native(self,path,bundle):
        remember=getattr(self,'_remember_current_page',None)
        if remember:remember()
        previous_path=self.prepared.path
        self.prepared.path=path
        baseline=str(self.save_session.baseline)
        self.prepared.archive_path=baseline;self.canvas.archive_path=baseline
        self.disk_bundle=bundle;self.disk_stamp=self.saver.committed_stamp
        snapshot=self.saver.snapshot
        core=getattr(self.canvas.edits,'core',self.canvas.edits)
        core.saved_token=snapshot.token
        self.canvas.image_layers._saved=snapshot.image_digest
        self.canvas.official_image_edits.mark_saved(snapshot.native_digest)
        self.record_recent_file(path)
        history_target=getattr(self,'_page_history_target',None)
        if history_target:
            self._page_history_path=history_target(path,allow_draft=True)
            self._page_history_ready=bool(self._page_history_path)
            remember=getattr(self,'_remember_current_page',None)
            if remember:remember()
        self.edit_changed()
        # Original asset identities and the entire undo history remain live.
        # The private session baseline supplies assets removed from the NTB.
        is_recovery=(Path(previous_path).resolve()!=Path(path).resolve()
                     and Path(path).stem.startswith(Path(previous_path).stem+'-保存恢复'))
        if is_recovery or getattr(self,'_save_request_show_path',False):
            self.statusBar().showMessage('已保存并检查页面素材：'+path)
        else:
            self.statusBar().showMessage('已保存 · 仍有新修改待保存' if self._has_unsaved_changes() else '已保存并检查页面素材')
        # Delay the success notification until the background worker has
        # finished and the progress dialog has closed. Never report success
        # for a cancelled or failed save.
        self._save_notice=(path, is_recovery,
                           getattr(self.saver,'overwrite_error','') if is_recovery and self.saver else '',
                           bool(getattr(self,'_save_request_show_path',False)))

    def save_finished(self):
        success=self.saver.succeeded
        self.save_progress.close();self.save_progress.deleteLater();self.saver.deleteLater();self.saver=None
        self.statusBar().removeWidget(self._save_status);self._save_status.deleteLater()
        self.edit_toolbar.setEnabled(True);self.open_action.setEnabled(True);self.canvas.setEnabled(True);self.canvas.setUpdatesEnabled(True)
        after=self.after_save;self.after_save=None
        notice=self._save_notice if success else None
        self._save_notice=None
        self._save_request_show_path=False
        refresh_menu_state(self)
        if notice:
            path,is_recovery,detail,show_path=notice
            if is_recovery:
                show_copyable_path_notice(self,'已安全保存到新文件',
                    'Windows 拒绝覆盖当前原文件。\n\n原文件保持不变，所有本次修改已保存到：\n'+path+
                    '\n\n当前编辑会话已切换到此文件，可继续编辑。\n\n覆盖失败诊断：\n'+
                    (detail or '未记录到系统错误码')+
                    '\n\n如需排查，可运行项目根目录的 DIAGNOSE_OVERWRITE.bat。',path)
        self.edit_changed()
        if not success:self.statusBar().showMessage('保存未完成，编辑内容仍保留',6000)
        queued=self._save_queued
        self._save_queued=False
        if success and queued and self._has_unsaved_changes():
            QTimer.singleShot(0,lambda:self.save_ntb(after=after))
            return
        if success and after:
            # Some continuations create/replace a document directly. Do not
            # run one against edits made AFTER its captured save revision.
            if self._has_unsaved_changes():
                self.statusBar().showMessage('快照已保存；保存期间有新修改，已留在编辑页。请保存后再继续。')
            else:
                QTimer.singleShot(0,after)

    def export_to_pdf(self):
        if self.worker or self.saver or not self.prepared:return
        if hasattr(self.canvas,'finishGesture'):self.canvas.finishGesture()
        source=Path(self.prepared.path).resolve()
        default=str(source.with_name(source.stem+'-导出.pdf'))
        target,_=QFileDialog.getSaveFileName(self,'导出 PDF',default,'PDF 文档 (*.pdf)')
        if not target:return
        path=Path(target if target.lower().endswith('.pdf') else target+'.pdf').resolve()
        if path==source:
            QMessageBox.warning(self,'导出位置错误','不能覆盖当前打开的原始文件，请另选文件名。');return
        from import_export.ne_export import _pages
        count=len(_pages(self.canvas,self.prepared))
        progress=prepare_progress(QProgressDialog('正在导出 PDF…','取消',0,count,self),'正在导出 PDF')
        progress.setWindowModality(Qt.WindowModality.ApplicationModal)
        progress.setMinimumDuration(0);progress.setAutoClose(False)
        progress.show()
        def update(current,total):
            progress.setMaximum(total);progress.setValue(current)
            QApplication.processEvents()
        try:
            result=write_pdf(self.canvas,self.prepared,str(path),progress=update,
                             cancelled=lambda:progress.wasCanceled(),include_ink=True,
                             enhancement=self.enhance.isChecked())
            self.statusBar().showMessage('PDF 已导出：'+str(result))
            QMessageBox.information(self,'PDF 导出完成',f'已导出 {count} 页：\n{result}')
        except ExportCancelled:
            self.statusBar().showMessage('PDF 导出已取消，原文件未改变')
        except Exception as exc:
            QMessageBox.warning(self,'PDF 导出失败',str(exc))
        finally:
            progress.close()

    def export_to_note(self):
        if self.worker or self.saver or not self.prepared:return
        if hasattr(self.canvas,'finishGesture'):self.canvas.finishGesture()
        source=Path(self.prepared.path).resolve()
        if source.suffix.lower() not in ('.ntb','.note'):return
        if source.suffix.lower()=='.ntb':
            warning=('NTB → NOTE 是实验性格式转换，尚未通过 Notability 官方客户端导入验证。\n\n'
                     '页面背景、PDF 原文和图片将合成为内嵌 PDF（图片不再单独可编辑）；'
                     '画笔和荧光笔保留为独立笔迹。文字框、音频及部分特殊对象无法完整转换。\n\n'
                     '尚未通过官方 Notability 导入验证，请保留原 NTB，并使用副本测试。是否继续？')
            answer=QMessageBox.warning(self,'实验性 NOTE 导出',warning,
                     QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No,QMessageBox.StandardButton.No)
            if answer!=QMessageBox.StandardButton.Yes:return
        default=str(source.with_name(source.stem+'-导出.note'))
        target,_=QFileDialog.getSaveFileName(self,'导出 NOTE',default,'Notability NOTE (*.note)')
        if not target:return
        path=Path(target if target.lower().endswith('.note') else target+'.note').resolve()
        if path==source:
            QMessageBox.warning(self,'导出位置错误','不能覆盖当前打开的原始文件，请另选文件名。');return
        try:
            pages=None
            if source.suffix.lower()=='.ntb':
                pages=[(self.canvas.sizes[i][0],self.canvas.sizes[i][1],
                        tuple(self.canvas.edits.state(i)[0])) for i in range(self.canvas.page_count)]
            if pages is not None:
                # Compose PDF backgrounds and placed images first, retaining
                # live ink separately in Session.plist. Use a temporary
                # directory; export_note commits the NOTE atomically.
                import tempfile
                with tempfile.TemporaryDirectory(prefix='nv-note-background-') as tmp:
                    bg=Path(tmp)/'background.pdf'
                    progress=prepare_progress(QProgressDialog('正在生成 NOTE 背景和图片…','取消',0,len(pages),self),'正在导出 NOTE')
                    progress.setWindowModality(Qt.WindowModality.ApplicationModal)
                    progress.setMinimumDuration(0)
                    progress.setAutoClose(False)
                    progress.show()
                    def update(current,total):
                        progress.setMaximum(total);progress.setValue(current)
                        QApplication.processEvents()
                    try:
                        write_pdf(self.canvas,self.prepared,bg,progress=update,
                                  cancelled=lambda:progress.wasCanceled(),
                                  include_ink=False,enhancement=False,preserve_pdf_sizes=False)
                        if progress.wasCanceled():raise ExportCancelled('已取消 NOTE 导出')
                        result=export_note(source,path,pages,background_pdf=bg)
                    finally:
                        progress.close()
            else:
                result=export_note(source,path)
            self.statusBar().showMessage('NOTE 已导出：'+str(result))
            suffix='（含页面背景与图片的实验性格式，仍需在官方 Notability 验证）' if pages is not None else '（原 NOTE 无损复制）'
            QMessageBox.information(self,'NOTE 导出完成',f'文件已生成{suffix}：\n{result}')
        except ExportCancelled:
            self.statusBar().showMessage('NOTE 导出已取消，原文件未改变')
        except Exception as exc:
            QMessageBox.warning(self,'NOTE 导出失败',str(exc))

    def action(self,toolbar,text,callback,shortcut=None):
        action = QAction(text,self)
        action.triggered.connect(callback)
        if shortcut:
            action.setShortcut(QKeySequence(shortcut))
        toolbar.addAction(action)
        return action

    def import_pdf_as_ntb(self, source=None):
        if hasattr(self,'workspace') and self.workspace.busy():return
        """Prepare an editable NTB with each PDF page as a background."""
        if self.worker or self.saver or self.pdf_importer:
            return
        # Never abandon unsaved NTB edits while creating another document.
        if not self.confirm_leave(lambda: self.import_pdf_as_ntb(source), continue_label='导入 PDF'):
            return
        if not source:
            if self.prepared and Path(self.prepared.path).suffix.lower() == '.pdf':
                source = self.prepared.path
            else:
                source, _ = QFileDialog.getOpenFileName(self, '选择要添加笔记的 PDF', '', 'PDF 文件 (*.pdf)')
                if not source:
                    return
        pdf_path=Path(source).resolve()
        if pdf_path.suffix.lower() != '.pdf' or not pdf_path.is_file():
            QMessageBox.warning(self,'无法创建笔记','请选择现有的 PDF 文件。')
            return
        # Preflight before allocating the application-owned import draft.
        try:
            import fitz
            with fitz.open(str(pdf_path)) as probe:
                if probe.needs_pass:
                    raise ValueError('加密 PDF 暂不支持，请先解密后导入。')
                count=probe.page_count
                from import_export.ne_official_pdf import MIN_PAGES,MAX_IMPORT_PAGES
                if not MIN_PAGES<=count<=MAX_IMPORT_PAGES:
                    raise ValueError(f'PDF 导入支持 {MIN_PAGES}～{MAX_IMPORT_PAGES} 页，当前输入 {count} 页。')
        except Exception as exc:
            QMessageBox.warning(self,'无法读取 PDF',str(exc));return
        # Import to an application-owned temporary cache first. Do not ask for
        # an NTB destination until the user explicitly invokes Save / Save As.
        try:
            draft=ImportDraft(pdf_path)
        except (OSError,ValueError) as exc:
            QMessageBox.warning(self,'无法准备 PDF 缓存',str(exc));return
        self._pending_pdf_draft=draft
        target=str(draft.path)
        self.pdf_import_ready_path=None
        self.pdf_import_progress=prepare_progress(
            QProgressDialog('正在导入 PDF 并创建可编辑 NTB…','取消',0,100,self),'正在创建 NTB 笔记')
        self.pdf_import_progress.setWindowModality(Qt.WindowModality.WindowModal)
        self.pdf_import_progress.setMinimumDuration(0)
        self.pdf_import_progress.setAutoClose(False)
        # Keep the previous document read-only while this import is working;
        # the leave/discard decision was made before the worker started.
        if self.canvas:self.canvas.setEnabled(False)
        self.pdf_importer=PdfImportWorker(str(pdf_path),target,self)
        self.pdf_importer.progress.connect(self.pdf_import_progress.setValue)
        self.pdf_importer.ready.connect(self.pdf_import_succeeded)
        self.pdf_importer.failed.connect(lambda error: QMessageBox.warning(
            self,'PDF 导入失败（原 PDF 未修改）',error))
        self.pdf_importer.finished.connect(self.pdf_import_finished)
        self.pdf_import_progress.canceled.connect(self.pdf_importer.stop.set)
        refresh_menu_state(self)
        self.edit_changed()
        self.pdf_importer.start()

    def pdf_import_succeeded(self, path):
        self.pdf_import_ready_path=path
        self.statusBar().showMessage('PDF 已导入临时缓存，首次保存时才选择正式 NTB 路径')

    def pdf_import_finished(self):
        destination=self.pdf_import_ready_path
        self.pdf_import_ready_path=None
        self.pdf_import_progress.close()
        self.pdf_importer.deleteLater()
        self.pdf_importer=None
        refresh_menu_state(self)
        if self.canvas:self.canvas.setEnabled(True)
        self.edit_changed()
        if destination:
            # The user already approved leaving the previous document before
            # PDF conversion started. Do not ask the same question twice.
            QTimer.singleShot(0,lambda path=destination:self.open_file(path,skip_leave_check=True))
        elif self._pending_pdf_draft:
            self._pending_pdf_draft.cleanup()
            self._pending_pdf_draft=None

    def choose_file(self):
        """Opening intentionally excludes PDF: importing creates an editable NTB."""
        if hasattr(self,'workspace') and self.workspace.busy():return
        path,_ = QFileDialog.getOpenFileName(self,'打开 Notability 笔记','','Notability 笔记 (*.ntb *.note)')
        if path:
            self.open_file(path)

    def create_new_note(self):
        if hasattr(self,'workspace') and self.workspace.busy():return
        if self.worker or self.saver or self.pdf_importer:
            return
        from import_export.ne_newnote import NewNoteDialog
        dialog=NewNoteDialog(self)
        if dialog.exec()!=QDialog.DialogCode.Accepted.value:
            return
        preset,background=dialog.values()
        color=dialog.paper_color
        if not self.confirm_leave(
                lambda: self._create_new_note_draft(preset,background,color),
                continue_label='新建笔记'):
            return
        self._create_new_note_draft(preset,background,color)

    def _create_new_note_draft(self,preset,background,color='#ffffff'):
        from import_export.ne_importdraft import NewNoteDraft
        from import_export.ne_newnote import create_newnote_ntb
        draft=NewNoteDraft()
        try:
            create_newnote_ntb(draft.path,preset,background,color=color)
        except Exception as exc:
            draft.cleanup()
            QMessageBox.warning(self,'无法新建笔记',str(exc))
            return False
        self._pending_new_note_draft=draft
        self.open_file(str(draft.path),skip_leave_check=True)
        return True

    def record_recent_file(self,path):
        """Remember only real opened/saved note files, never temporary drafts."""
        from settings.ne_recent_files import add_recent_file
        value=Path(path)
        if value.suffix.lower() not in ('.ntb','.note') or not value.is_file():
            return
        try:
            add_recent_file(self.color_settings,value)
        except OSError as exc:
            diagnostic_exception("record recent file",exc)
            return
        if hasattr(self,'workspace'):
            try:self.workspace.record(value)
            except OSError as exc:diagnostic_exception('record library note',exc)
        refresh_recent_menu(self)

    def open_recent_file(self,path):
        if not Path(path).is_file():
            from settings.ne_recent_files import remove_recent_file
            from settings.ne_page_history import forget_last_page
            remove_recent_file(self.color_settings,path)
            forget_last_page(self.color_settings,path)
            refresh_recent_menu(self)
            QMessageBox.warning(
                self,'文件不存在',
                '最近打开的文件已被移动或删除：\n'+str(path))
            return
        self.open_file(path)

    def clear_recent_files(self):
        from settings.ne_recent_files import clear_recent_files
        from settings.ne_page_history import clear_last_pages
        clear_recent_files(self.color_settings)
        clear_last_pages(self.color_settings)
        refresh_recent_menu(self)
        self.statusBar().showMessage('已清空最近打开记录',4000)

    def handle_incoming_file(self,path):
        """Single entry point for drag-and-drop and shell command-line files."""
        kind=file_action(path)
        if kind=='open':
            return self.open_file(path)
        if kind=='import':
            return self.import_pdf_as_ntb(path)
        QMessageBox.warning(self,'不支持的文件格式','打开仅支持 NTB / NOTE，导入仅支持 PDF。')

    def open_file(self,path,skip_leave_check=False):
        if file_action(path)!='open':
            QMessageBox.warning(self,'无法打开文件','打开文件仅支持 .ntb 与 .note。\n如需使用 PDF，请选择「导入 PDF」。')
            return
        if (self.worker is not None or self.saver is not None or self.pdf_importer is not None
                or (hasattr(self,'workspace') and self.workspace.busy())):
            return
        if not skip_leave_check and not self.confirm_leave(
                lambda: self.open_file(path), continue_label='打开其他笔记'):
            return
        self.open_action.setEnabled(False)
        self.progress = prepare_progress(QProgressDialog('正在读取文件…','取消',0,100,self),'正在打开文件')
        self.progress.setWindowModality(Qt.WindowModality.WindowModal)
        self.progress.setMinimumDuration(300)
        self.progress.setAutoClose(False)
        self.worker = LoadWorker(path,self)
        self.worker.progress.connect(self.update_progress)
        self.worker.ready.connect(self.install_note)
        self.worker.page_access_ready.connect(self.install_page_access)
        self.worker.failed.connect(self.load_failed)
        self.worker.cancelled.connect(lambda: self.statusBar().showMessage('已取消加载'))
        self.worker.finished.connect(self.load_finished)
        self.progress.canceled.connect(self.worker.stop.set)
        refresh_menu_state(self)
        self.worker.start()

    def update_progress(self,value,message):
        if self.worker and not self.worker.stop.is_set():
            self.progress.setLabelText(message)
            self.progress.setValue(value)

    def load_failed(self,message):
        if hasattr(self, 'progress'):
            self.progress.hide()
        QMessageBox.warning(self,'无法打开文件',message)

    def load_finished(self):
        self.progress.close()
        self.editor_view.schedule_cursor_refresh()
        self.worker.deleteLater()
        self.worker = None
        # If a newly imported draft cannot be opened, reclaim its cache.
        if self._pending_pdf_draft:
            self._pending_pdf_draft.cleanup()
            self._pending_pdf_draft=None
        if self._pending_new_note_draft:
            self._pending_new_note_draft.cleanup()
            self._pending_new_note_draft=None
        refresh_menu_state(self)
        if hasattr(self,'page_sidebar'):self.page_sidebar.refresh()

    def install_page_access(self,prepared):
        # A late result from an old open must never change the current model.
        if self.prepared is not prepared or not hasattr(self.canvas,'pagesChanged'):return
        grant_page_access(self.canvas,prepared)
        self.page_sidebar.refresh();refresh_menu_state(self)

    def install_note(self, prepared):
        """Catch the UI-install phase, which runs in a Qt signal callback.

        The background worker catches parser failures, but exceptions after
        `ready` is emitted must also be caught: otherwise a windowed PyInstaller
        EXE can disappear without an error dialog or console output.
        """
        try:
            self._install_note_impl(prepared)
            if hasattr(self,'progress'):self.progress.hide()
        except Exception as exc:
            diagnostic_exception("install_note/UI", exc)
            # Keep the note archive untouched. Detach the incomplete new view
            # before notifying the user so its timer/paint handlers can't retry.
            try:
                if self.prepared is prepared:
                    self.page_sidebar.attach(None)
                    old = self.scroll.takeWidget()
                    if old is not None:
                        old.deleteLater()
                    self.close_document()
                    self.canvas = None
                    self.disk_bundle = None
                else:
                    prepared.cleanup()
            except Exception as cleanup_exc:
                diagnostic_exception("install_note cleanup", cleanup_exc)
            QMessageBox.critical(
                self, "无法打开这份 NTB",
                "图形界面初始化失败，原笔记未被修改。\n\n"
                + f"错误：{type(exc).__name__}: {exc}\n\n"
                + "诊断日志：\n" + str(log_directory()))

    def _install_note_impl(self,prepared):
        diagnostic_stage("install note: check PDF and parse SVG")
        if self.worker and self.worker.stop.is_set():
            prepared.cleanup()
            return
        document = None
        try:
            if prepared.pdf_path:
                document = QPdfDocument(self)
                error = document.load(prepared.pdf_path)
                if error != QPdfDocument.Error.None_ or not document.pageCount():
                    raise ValueError(f'无法读取 PDF（{error.name}），文件可能损坏或需要密码')
            root = etree.fromstring(prepared.svg)
            if prepared.archive_path or document:
                note_height = float(root.get('data-page-height',str(prepared.height)))
                diagnostic_stage("install note: construct PdfNoteCanvas")
                canvas = PdfNoteCanvas(document,prepared.svg,prepared.width,note_height,archive_path=prepared.archive_path,pdf_source_path=prepared.pdf_path,render_index=prepared.render_index)
                canvas.setEnhancementStrength(self.strength.value()/100)
                canvas.setEnhanced(self.enhance.isChecked())
                canvas.setInkVisible(self.ink.isChecked())
                canvas.setInkSmoothing(True)
                canvas.setSamplingPrecision(self.color_settings.value('draw/sampling_spacing',.05))
                canvas.setSmartInterpolation(
                    self.color_settings.value('draw/smart_interpolation',True,type=bool))
                canvas.setSeamlessPages(self.seamless_pages_action.isChecked())
                count = canvas.page_count
            else:
                canvas = QSvgWidget()
                canvas.load(prepared.svg)
                canvas.resize(round(prepared.width),round(prepared.height))
                count = 1
        except Exception as exc:
            diagnostic_exception("install note: create canvas", exc)
            if document:
                document.close()
                sip.delete(document)
            prepared.cleanup()
            self.load_failed(str(exc))
            return
        diagnostic_stage("install note: swap to new canvas")
        old = self.scroll.takeWidget()
        if old:
            old.deleteLater()
        self.close_document()
        self.prepared,self.document,self.canvas = prepared,document,canvas
        apply_selection_preferences(self)
        if self._pending_pdf_draft and self._pending_pdf_draft.matches(prepared.path):
            self._active_pdf_draft=self._pending_pdf_draft
            self._pending_pdf_draft=None
        if (self._pending_new_note_draft
                and self._pending_new_note_draft.matches(prepared.path)):
            self._active_new_note_draft=self._pending_new_note_draft
            self._pending_new_note_draft=None
        if not self._is_unsaved_draft():
            self.record_recent_file(prepared.path)
        self.disk_bundle = prepared.bundle_data
        self.disk_stamp = file_stamp(prepared.path)
        if prepared.path.lower().endswith('.ntb'):
            self.save_session=SaveSession(prepared.path,prepared.bundle_data)
        if hasattr(canvas,'pagesChanged'):
            canvas.pagesChanged.connect(self.pages_changed)
            # One capability check for ALL page controls, performed only after
            # the real page model is available. A page with unknown provenance
            # does not, by itself, disable insert/whole-page duplicate.
            grant_page_access(canvas,prepared)
        self.edit_toolbar.setEnabled(prepared.path.lower().endswith('.ntb'))
        self.tools['read'].setChecked(True);self.editor_panel.set_tool('read');self.editor_panel.selection_changed(0)
        self.tape_reveal_action.setChecked(False)
        self.update_edit_settings()
        self.restore_edit_tool()
        if hasattr(canvas,'selectionChanged'):
            canvas.selectionChanged.connect(self.editor_panel.selection_changed)
            canvas.selectionChanged.connect(lambda count:refresh_menu_state(self))
        if hasattr(canvas,'editsChanged'):
            canvas.editsChanged.connect(self.edit_changed)
        diagnostic_stage("install note: update toolbar and state")
        self.edit_changed()
        self.scroll.setWidgetResizable(False)
        self.scroll.setWidget(canvas)
        self._install_drop_target(canvas)
        self.page.setRange(1,count)
        self.page.setValue(1)
        self.page.setEnabled(hasattr(canvas,'pageTop'))
        refresh_menu_state(self)
        self.count_label.setText(f' / {count}  ')
        self.edit_changed()  # Recompute title, including the unsaved PDF draft marker.
        self.workspace.show_editor()
        self.fit()
        self.scroll.verticalScrollBar().setValue(0)
        restored_position,restored_page=self._restore_page_history(prepared.path,count)
        draft_kind=self._unsaved_draft_kind()
        if draft_kind=='pdf':
            self.statusBar().showMessage(f'PDF 已导入临时缓存 · {count} 页 · 尚未保存 NTB（Ctrl+S 选择位置）')
        elif draft_kind=='new':
            self.statusBar().showMessage(f'新建笔记草稿 · {count} 页 · 尚未保存 NTB（Ctrl+S 选择位置）')
        else:
            restored=''
            if restored_page is not None:
                zoom_label=('适应宽度' if self.fit_width
                            else f'{round(self.zoom*100)}%')
                restored=f'  ·  已恢复第 {restored_page} 页 / {zoom_label}'
            self.statusBar().showMessage(f'{Path(prepared.path).name}  ·  {count} 页{restored}')
        diagnostic_stage("install note: attach page sidebar")
        self.page_sidebar.attach(canvas)
        self.gpu.attach(canvas)
        if hasattr(canvas,'enablePageWarmup'):canvas.enablePageWarmup()
        if hasattr(canvas,'enableViewTiles'):canvas.enableViewTiles()
        if getattr(canvas,'view_tiles',None) is not None and self.worker is not None:
            canvas.view_tiles.visible_ready.connect(self.worker.page_access_gate.set)
        diagnostic_stage("install note: finished")
        recovered=getattr(self,'_recovery_to_clear',None)
        if recovered and str(Path(prepared.path).resolve())==recovered[1]:
            from editing.ne_save_session import remove_recovery
            try:remove_recovery(recovered[0])
            except OSError as exc:diagnostic_exception('clean recovered session',exc)
            self._recovery_to_clear=None

    def pages_changed(self,focus):
        self.page.setRange(1,self.canvas.page_count);self.count_label.setText(f' / {self.canvas.page_count}  ')
        if self.fit_width:self.fit()
        self.page_sidebar.reset();self.go_page(focus+1);self.page_sidebar.sync(focus)

    def close_document(self):
        self._snapshot_timer.stop()
        if self.snapshot_worker:
            self.snapshot_worker.stop.set()
            self.snapshot_worker.wait()
            self.snapshot_worker.deleteLater()
            self.snapshot_worker=None
        remember=getattr(self,'_remember_current_page',None)
        if remember:remember()
        if hasattr(self,'_page_history_timer'):self._page_history_timer.stop()
        if hasattr(self,'_reading_page_sync_timer'):self._reading_page_sync_timer.stop()
        self._page_history_ready=False
        self._page_history_path=None
        if hasattr(self,'page_sidebar'):self.page_sidebar.attach(None)
        if hasattr(self,'gpu'):self.gpu.detach()
        if self.canvas and hasattr(self.canvas,'stopRendering'):self.canvas.stopRendering()
        if self.document:
            self.document.close()
            sip.delete(self.document)
            self.document = None
        if self.prepared:
            self.prepared.cleanup()
            self.prepared = None
        if self._active_pdf_draft:
            self._active_pdf_draft.cleanup()
            self._active_pdf_draft=None
        if self._active_new_note_draft:
            self._active_new_note_draft.cleanup()
            self._active_new_note_draft=None
        if self.save_session:
            self.save_session.close()
            self.save_session=None

    def _sync_page_now(self):
        if self.canvas and hasattr(self.canvas,'pageAt'):
            self.page.setValue(self.canvas.pageAt(self.scroll.verticalScrollBar().value()+12)+1)
            if hasattr(self,'page_sidebar'):self.page_sidebar.sync(self.page.value()-1)
            schedule=getattr(self,'_schedule_page_history',None)
            if schedule:schedule()

    def _flush_reading_page_sync(self):
        self._sync_page_now()

    def sync_page(self):
        momentum=getattr(getattr(self.canvas,'_reading_pan_timer',None),'isActive',None)
        if (getattr(self.canvas,'_reading_pan',None) is not None
                or (momentum and momentum())):
            if not self._reading_page_sync_timer.isActive():
                self._reading_page_sync_timer.start()
            return
        self._sync_page_now()

    def go_page(self,page):
        if self.canvas and hasattr(self.canvas,'pageTop'):
            page=max(1,min(self.canvas.page_count,page))
            self.scroll.verticalScrollBar().setValue(self.canvas.pageTop(page-1))
            # The last sheet cannot always scroll to the viewport's top.
            # valueChanged synchronously selects its first visible neighbour;
            # restore the explicitly requested page as the operation target.
            self.page.setValue(page)
            self.page_sidebar.sync(page-1)
            schedule=getattr(self,'_schedule_page_history',None)
            if schedule:schedule()

    def change_zoom(self,factor):
        self.fit_width = False
        self.apply_zoom(self.zoom*factor)

    def fit(self):
        self.fit_width = True
        if self.canvas:
            width = max(w for w,h in self.canvas.sizes) if hasattr(self.canvas,'sizes') else self.prepared.width
            self.apply_zoom((self.scroll.viewport().width()-36)/width)

    def apply_zoom(self,zoom):
        if not self.canvas:
            return
        zoom = max(.2,min(5.,zoom))
        bar = self.scroll.verticalScrollBar()
        hbar = self.scroll.horizontalScrollBar()
        old_zoom = self.zoom
        if hasattr(self.canvas,'pageAt'):
            page = self.canvas.pageAt(bar.value())
            fraction = (bar.value()-self.canvas.pageTop(page))/self.canvas.draw_sizes[page][1]
            self.canvas.setZoom(zoom)
            bar.setValue(round(self.canvas.pageTop(page)+fraction*self.canvas.draw_sizes[page][1]))
        else:
            y = bar.value()/old_zoom
            self.canvas.resize(round(self.prepared.width*zoom),round(self.prepared.height*zoom))
            bar.setValue(round(y*zoom))
        hbar.setValue(round(hbar.value()*zoom/old_zoom))
        self.zoom = zoom
        self.zoom_label.setText(f'{round(zoom*100)}%')
        self.sync_page()

    def schedule_strength(self,value):
        self.strength_label.setText(f'{value}% ')
        self.strength_timer.start()

    def apply_strength(self):
        if hasattr(self.canvas,'setEnhancementStrength'):
            self.canvas.setEnhancementStrength(self.strength.value()/100)

    def toggle_gpu(self,enabled):
        if hasattr(self,'gpu_action'):self.gpu_action.setChecked(bool(enabled))
        self.gpu_settings.setValue('gpu_acceleration',bool(enabled))
        self.gpu.setEnabled(enabled)
        self.gpu_state_changed(self.gpu.active,self.gpu.description)

    def gpu_state_changed(self,active,description):
        from infrastructure.ne_diagnostics import debug
        debug(f'Rendering backend: GPU={active}; {description}')
        if hasattr(self,'gpu_action'):
            self.gpu_action.setToolTip(('正在使用 GPU：' if active else '')+description)

    def toggle_enhance(self,enabled):
        self.strength.setEnabled(enabled)
        self.apply_strength()
        if hasattr(self.canvas,'setEnhanced'):
            self.canvas.setEnhanced(enabled)

    def configure_sampling_precision(self):
        from PyQt6.QtWidgets import QInputDialog
        current=getattr(self.canvas,'sampling_precision',.05)
        value,accepted=QInputDialog.getDouble(self,'采样精度',
            '最小采样间距（文档点 pt，1 pt = 1/72 英寸）\n数值越小细节越多；数值越大采样量越少。\n建议：精细 0.025，标准 0.05，流畅 0.2。\n用于之后绘制的画笔、荧光笔和遮字胶带。',current,.01,2.,3)
        if accepted:
            self.canvas.setSamplingPrecision(value)
            self.color_settings.setValue('draw/sampling_spacing',value)
            self.color_settings.sync()

    def toggle_smart_interpolation(self,enabled):
        self.color_settings.setValue('draw/smart_interpolation',bool(enabled))
        self.color_settings.sync()
        if hasattr(self.canvas,'setSmartInterpolation'):
            self.canvas.setSmartInterpolation(bool(enabled))


    def toggle_seamless_pages(self,enabled):
        self.color_settings.setValue('view/seamless_pages',bool(enabled))
        self.color_settings.sync()
        canvas=self.canvas
        if not hasattr(canvas,'setSeamlessPages'):return
        bar=self.scroll.verticalScrollBar()
        page=canvas.pageAt(bar.value())
        offset=bar.value()-canvas.pageTop(page)
        canvas.setSeamlessPages(enabled)
        bar.setValue(round(canvas.pageTop(page)+offset))
        self.sync_page()

    def toggle_ink(self,enabled):
        if not enabled:
            self.tools['read'].setChecked(True)
            self.select_tool('read',remember=False)
        if hasattr(self.canvas,'setInkVisible'):
            self.canvas.setInkVisible(enabled)

    def resizeEvent(self,event):
        super().resizeEvent(event)
        if self.fit_width:
            QTimer.singleShot(0,self.fit)

    def _install_drop_target(self,widget):
        """Cover the empty state, scroll viewport, and loaded canvas."""
        widget.setAttribute(Qt.WidgetAttribute.WA_AcceptTouchEvents)
        widget.setAcceptDrops(True)
        widget.installEventFilter(self)

    def _dropped_local_path(self,event):
        if self.worker or self.saver or self.pdf_importer or not event.mimeData().hasUrls():
            return None
        for url in event.mimeData().urls():
            if not url.isLocalFile():
                continue
            path=url.toLocalFile()
            if file_action(path) and Path(path).is_file():
                return path
        return None

    def eventFilter(self,obj,event):
        if event.type() in (QEvent.Type.DragEnter,QEvent.Type.DragMove,QEvent.Type.Drop):
            path=self._dropped_local_path(event)
            if path:
                event.acceptProposedAction()
                if event.type()==QEvent.Type.Drop:
                    self.handle_incoming_file(path)
                return True
        return super().eventFilter(obj,event)

    def dragEnterEvent(self,event):
        if self._dropped_local_path(event):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self,event):
        if self._dropped_local_path(event):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self,event):
        path=self._dropped_local_path(event)
        if path:
            event.acceptProposedAction()
            self.handle_incoming_file(path)
        else:
            event.ignore()

    def closeEvent(self,event):
        notice = getattr(self, '_free_notice_dialog', None)
        if notice is not None and not sip.isdeleted(notice):
            if not notice.can_dismiss():
                event.ignore()
                notice.raise_()
                notice.activateWindow()
                return
            notice.close()
        job = getattr(getattr(getattr(self,'workspace',None),'home',None),'batch_job',None)
        if job is not None:
            job.cancel()
            if not getattr(self,'_closing_batch',False):
                self._closing_batch = True
                job.finished.connect(self.close)
            event.ignore()
            return
        workspace=getattr(self,'workspace',None)
        if workspace and workspace.importer:
            workspace.exiting=True
            workspace.importer.stop.set()
            workspace.importer.finished.connect(self.close)
            event.ignore()
            return
        if self.pdf_importer:
            self.pdf_importer.stop.set()
            event.ignore()
            self.pdf_importer.finished.connect(self.close)
            return
        if self.saver:
            self.statusBar().showMessage('正在完成保存，完成后关闭…')
            self.saver.finished.connect(self.close)
            event.ignore()
            return
        if self.worker:
            self.worker.stop.set()
            event.ignore()
            # Keep the event loop alive until the worker cleans its temp file.
            self.worker.finished.connect(self.close)
            return
        if not getattr(self,'_restart_approved',False) and not self.confirm_leave(self.close, continue_label='关闭'):
            event.ignore()
            return
        self.close_document()
        if self._pending_pdf_draft:
            self._pending_pdf_draft.cleanup()
            self._pending_pdf_draft=None
        if self._pending_new_note_draft:
            self._pending_new_note_draft.cleanup()
            self._pending_new_note_draft=None
        event.accept()

    def restart_application(self):
        from settings.ne_appsettings import restart_viewer
        restart_viewer(self)


def main():
    try:
        migration = migrate_user_data()
    except Exception as exc:
        migration = {'status': 'pending', 'errors': ['旧版数据迁移未开始：' + str(exc)]}
    install_diagnostics()
    if migration.get('errors'):
        diagnostic_warning('Viewer data migration pending: ' + ' | '.join(migration['errors']))
    diagnostic_stage("Qt application creation")
    set_app_identity()
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_CompressHighFrequencyEvents,False)
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_CompressTabletEvents,False)
    app = QApplication(sys.argv)
    app.setOrganizationName('NotabilityEditor')
    app.setApplicationName('NotabilityEditor')
    app.setApplicationVersion('0.7.9-Beta')
    app.setWindowIcon(QIcon(str(icon_path())))
    app.setStyleSheet(APP_STYLE)
    diagnostic_stage("main window creation")
    window = Viewer()
    window.show()
    diagnostic_stage("main window shown")
    from settings.ne_free_notice import show_startup_notices
    def resume_startup():
        if len(sys.argv)>1 and not sip.isdeleted(window) and window.isVisible():
            window.handle_incoming_file(sys.argv[1])
    QTimer.singleShot(0, lambda: show_startup_notices(window, finished=resume_startup))
    try:
        return app.exec()
    finally:
        # Destroy the window tree while QApplication is still alive. The
        # workspace and signal callbacks retain Python wrappers in cycles;
        # leaving their C++ objects to QApplication/interpreter teardown can
        # crash SIP during wrapper cleanup after main() returns.
        diagnostic_stage("main window destruction")
        if not sip.isdeleted(window):
            sip.delete(window)
        diagnostic_stage("main window destroyed")


if __name__ == '__main__':
    sys.exit(main())

