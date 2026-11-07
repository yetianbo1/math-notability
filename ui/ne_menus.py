"""Categorized menus sharing the toolbar actions and document state."""
from pathlib import Path
from PyQt6.QtCore import Qt,QUrl
from PyQt6.QtGui import QDesktopServices,QActionGroup
from PyQt6.QtWidgets import (QDialog,QVBoxLayout,QHBoxLayout,QLabel,QCheckBox,
                             QDialogButtonBox,QMessageBox,QMenu,QTabWidget,QWidget,QScrollArea,QFrame,QPushButton,QToolButton)
from ui.ne_style import MENU_STYLE,MENUBAR_STYLE,style_menu
from ui.ne_ui import add_dialog_header,add_dialog_hint,add_dialog_divider
from ui.ne_shortcuts import show_shortcuts
from ui.ne_toolbar import tool_icon
from images.ne_image_toolbar_state import action_states


def apply_selection_preferences(owner,*,activate=False):
    mode=getattr(owner,'selection_mode','exact');shape=getattr(owner,'selection_shape','rectangle')
    canvas=getattr(owner,'canvas',None)
    if hasattr(canvas,'setSelectionOptions'):
        canvas.setSelectionOptions(mode=mode,shape=shape,pen=getattr(owner,'selection_pen',True),
                                   highlighter=getattr(owner,'selection_highlighter',True))
    shape_label={'rectangle':'矩形','lasso':'自由套索'}[shape]
    mode_label={'ai':'本地 AI','smart':'字形归组','exact':'接触整笔','contained':'完整包含'}[mode]
    owner.editor_panel.selection_hint.setText(f'{shape_label} · {mode_label} · Shift 追加 · Alt 减选')
    if activate:owner.select_tool('select')


def build_menus(owner):
    bar=owner.menuBar();owner.menus={name:bar.addMenu(name) for name in ('文件','编辑','视图','工具','设置','帮助')}
    owner.setStyleSheet(MENU_STYLE);bar.setStyleSheet(MENUBAR_STYLE)
    file=owner.menus['文件']
    owner.new_note_action=owner.action(
        file,'新建笔记…',owner.create_new_note,'Ctrl+N')
    owner.new_note_action.setIcon(tool_icon('new_note'))
    owner.new_note_action.setIconVisibleInMenu(False)
    owner.new_note_action.setToolTip('新建 NTB 笔记，选择纸张大小和空白、横线或点状底页（Ctrl+N）')
    file.addAction(owner.open_action)
    owner.recent_menu=file.addMenu('最近打开')
    owner.recent_menu.aboutToShow.connect(lambda: refresh_recent_menu(owner))
    refresh_recent_menu(owner)
    file.addSeparator()
    import_menu=owner.import_menu=file.addMenu('导入')
    owner.pdf_note_action=owner.action(import_menu,'从 PDF 导入…',owner.import_pdf_as_ntb,'Ctrl+Shift+N')
    owner.pdf_note_action.setToolTip('仅支持 PDF：保留每页原始底图并创建可编辑的 NTB 笔记')
    file.addSeparator()
    file.addActions([owner.save_action,owner.save_as_action]);file.addSeparator()
    export=file.addMenu('导出')
    owner.export_pdf_action=owner.action(export,'导出为 PDF…',owner.export_to_pdf,'Ctrl+Shift+E')
    owner.export_note_action=owner.action(export,'导出为 NOTE…',owner.export_to_note)
    owner.export_note_action.setToolTip('NTB 转 NOTE 为实验性转换：背景及图片合成为 PDF，官方兼容性仍待验证')
    file.addSeparator()
    owner.exit_action=owner.action(file,'退出',owner.close,'Alt+F4')

    edit=owner.menus['编辑'];edit.addActions([owner.undo_action,owner.redo_action]);edit.addSeparator()
    def select_all():
        owner.select_tool('select');owner.canvas.selectAllInk()
    owner.select_all_action=owner.action(edit,'全选当前页笔迹',select_all)
    owner.select_all_action.setToolTip('选择当前页的全部笔迹；页面获得焦点时可按 Ctrl+A')
    edit.addActions([owner.editor_panel.delete_action,owner.editor_panel.clear_action])
    owner.move_to_page_action=owner.action(edit,'移动到页面…',owner.move_selection_to_page)
    owner.move_to_page_action.setToolTip('将选中的图片或手写笔迹移动到指定页；也可直接拖动到另一页')
    for tool in ('select','image'):
        row=owner.editor_panel.pages[tool][1]
        button=QToolButton();button.setDefaultAction(owner.move_to_page_action)
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        button.setFixedHeight(34);row.addWidget(button)

    owner.menus['工具'].addAction(owner.tools['tape'])
    owner.menus['工具'].addAction(owner.tape_reveal_action)
    owner.menus['工具'].addSeparator()
    selection=owner.menus['工具'].addMenu('选择');owner.selection_menu=selection
    selection.addAction(owner.tools['select']);selection.addSeparator()
    owner.selection_shape_actions={};owner.selection_mode_actions={}
    shapes=selection;algorithms=selection.addMenu('选取算法')
    for key,menu,choices,default in (
            ('shape',shapes,(('lasso','自由形状'),('rectangle','矩形')),'rectangle'),
            ('mode',algorithms,(('exact','接触即选整笔'),('contained','仅选完整包含'),('smart','字形归组补全'),('ai','本地 AI 补全（实验）')),'exact')):
        values=dict(choices);value=owner.color_settings.value('selection/'+key,default)
        if value not in values:value=default
        setattr(owner,'selection_'+key,value)
        group=QActionGroup(owner);group.setExclusive(True)
        for code,label in choices:
            action=menu.addAction(label);action.setCheckable(True);action.setChecked(code==value);group.addAction(action)
            getattr(owner,'selection_'+key+'_actions')[code]=action
            def change(checked=False,k=key,v=code):
                setattr(owner,'selection_'+k,v)
                owner.color_settings.setValue('selection/'+k,v);owner.color_settings.sync()
                apply_selection_preferences(owner,activate=True)
            action.triggered.connect(change)
    owner.selection_mode_actions['ai'].setToolTip('内置模型在本机 CPU 识别文字区域，补齐边界笔画；不上传笔记、不调用外部 AI')
    owner.selection_mode_actions['smart'].setToolTip('使用笔迹间距和字形大小归组；也可点击笔迹选取邻近字形，不运行 AI 模型')
    owner.selection_mode_actions['exact'].setToolTip('选取与矩形或套索接触的整条笔迹')
    owner.selection_mode_actions['contained'].setToolTip('仅选取完全位于矩形或套索内的笔迹')
    included=selection.addMenu('包含的笔迹类型');owner.selection_filter_actions={}
    for key,label in (('pen','手写画笔'),('highlighter','荧光笔')):
        value=owner.color_settings.value('selection/'+key,True,type=bool)
        setattr(owner,'selection_'+key,value)
        action=included.addAction(label);action.setCheckable(True);action.setChecked(value)
        owner.selection_filter_actions[key]=action
        def filter_changed(checked,k=key):
            setattr(owner,'selection_'+k,checked)
            owner.color_settings.setValue('selection/'+k,checked);owner.color_settings.sync()
            apply_selection_preferences(owner,activate=True)
        action.triggered.connect(filter_changed)
    selection.addSeparator()
    for label,level in (('扩展到相邻字形','character'),('扩展到同一行','line')):
        def expand(checked=False,value=level):
            if hasattr(owner.canvas,'expandInkSelection'):owner.canvas.expandInkSelection(value)
        owner.action(selection,label,expand)
    selection.addSeparator();selection.addActions([owner.editor_panel.clear_action,owner.select_all_action])
    owner.editor_panel.bind_selection_actions(owner)
    apply_selection_preferences(owner)

    view=owner.menus['视图'];view.addActions([owner.zoom_in_action,owner.zoom_out_action,owner.fit_action])
    view.addSeparator();pages=view.addMenu('页面导航')
    pages.addActions([owner.previous_action,owner.next_action])
    owner.first_action=owner.action(pages,'第一页',lambda:owner.go_page(1))
    owner.last_action=owner.action(pages,'最后一页',lambda:owner.go_page(owner.page.maximum()))
    def focus_page():
        owner.read_toolbar.show();owner.page.setFocus();owner.page.selectAll()
    owner.goto_action=owner.action(pages,'跳转到页码…',focus_page,'Ctrl+L')
    view.addSeparator();view.addActions([
        owner.ink,owner.sampling_precision_action,owner.smart_interpolation_action,owner.enhance])
    owner.display_action=owner.action(view,'页面增强设置…',lambda:show_display_settings(owner))
    owner.gpu_action=owner.action(owner.menus['设置'],'GPU 加速',owner.toggle_gpu)
    owner.gpu_action.setCheckable(True);owner.gpu_action.setChecked(owner.gpu.enabled)
    owner.gpu_action.setToolTip('可用时使用显卡绘制笔记；不兼容时自动使用软件绘制')
    from settings.ne_appsettings import show_application_info,show_registration
    settings=owner.menus['设置']
    from infrastructure.ne_diagnostics import current_level,set_level,log_directory
    logging=settings.addMenu('日志级别')
    owner.log_level_group=QActionGroup(owner)
    owner.log_level_group.setExclusive(True)
    owner.log_level_actions={}
    for level,label in (('ERROR','错误'),('WARNING','警告'),('INFO','信息（默认）'),('DEBUG','Debug（调试）')):
        action=logging.addAction(label)
        action.setCheckable(True);action.setChecked(current_level()==level)
        action.setToolTip('记录本级别及更严重的日志；切换后立即生效并保存')
        owner.log_level_group.addAction(action);owner.log_level_actions[level]=action
        def change_level(checked=False,value=level):
            if checked:
                set_level(value)
                owner.statusBar().showMessage('日志级别已设为 '+value,4000)
        action.triggered.connect(change_level)
    owner.action(logging,'打开日志目录',lambda:QDesktopServices.openUrl(QUrl.fromLocalFile(str(log_directory()))))
    settings.addSeparator()
    owner.restart_action=owner.action(settings,'重启应用',owner.restart_application)
    owner.register_action=owner.action(settings,'注册应用到 Windows…',lambda:show_registration(owner))
    owner.app_info_action=owner.action(settings,'应用信息与数据位置…',lambda:show_application_info(owner))
    view.addSeparator();panels=view.addMenu('工具栏')
    panels.addActions([owner.read_toolbar.toggleViewAction(),owner.edit_toolbar.toggleViewAction()])

    tools=owner.menus['工具'];tools.addActions(list(owner.tools.values()));tools.addSeparator()
    pictures=tools.addMenu('图片')
    owner.insert_image_action=owner.action(pictures,'插入图片…',owner.insert_image,'Ctrl+Shift+I')
    pictures.addSeparator()
    owner.image_status_action=pictures.addAction('图片状态：未选择')
    owner.image_status_action.setEnabled(False)
    pictures.addSeparator()
    owner.image_actions={}
    image_commands=(
        ('固定图片','toggle_lock','lock'),
        ('上移一层','up','layer_up'),
        ('下移一层','down','layer_down'),
        ('移到最上层','front','layer_front'),
        ('移到背景上一层','back','layer_back'),
        ('删除图片','delete','delete'),
    )
    for label,key,icon in image_commands:
        action=owner.action(pictures,label,lambda checked=False,k=key:owner.image_operation(k))
        action.setIcon(tool_icon(icon))
        action.setToolTip(label)
        owner.image_actions[key]=action
    # Use the same QAction objects in Tools > Images and the editor's
    # secondary settings row (like the eraser controls).  Do NOT allocate a
    # third top-level toolbar or a duplicate action that can drift out of sync.
    owner.insert_image_action.setIcon(tool_icon('image'))
    owner.insert_image_action.setToolTip('插入图片（Ctrl+Shift+I）；默认在背景上一层，选中后拖动四角控制点缩放')
    owner.editor_panel.bind_image_actions(owner)
    owner.defaults_action=owner.action(tools,'默认打开程序…',owner.show_default_settings)
    settings.addAction(owner.defaults_action)

    from settings.ne_updates import show_update_check
    settings.addSeparator()
    owner.check_updates_action=owner.action(settings,'检查更新…',lambda:show_update_check(owner))

    help=owner.menus['帮助']
    owner.help_action=owner.action(help,'操作与快捷键',lambda:show_help(owner),'F1')
    owner.action(help,'自定义快捷键…',lambda:show_shortcuts(owner))
    help.addAction(owner.check_updates_action)
    owner.action(help,'关于 NotabilityEditor',lambda:show_about(owner))
    # Menus use text and checks; toolbar icons retain their button colors.
    for action in [*owner.tools.values(),owner.undo_action,owner.redo_action,owner.save_action,owner.save_as_action,owner.export_pdf_action,owner.export_note_action,
                   owner.editor_panel.delete_action,owner.editor_panel.clear_action]:
        action.setIconVisibleInMenu(False)
    for menu in bar.findChildren(QMenu):style_menu(menu)

    # The settings dialog owns the original slider and action. Presentation
    # changes must not duplicate or diverge from their state.
    dialog=owner.display_dialog=QDialog(owner)
    dialog.setObjectName('settingsDialog')
    dialog.setWindowTitle('页面增强设置')
    dialog.setMinimumWidth(480)
    layout=QVBoxLayout(dialog)
    layout.setContentsMargins(26,24,26,22)
    layout.setSpacing(16)
    add_dialog_header(layout,'页面增强','改善扫描文档的灰度和纸张观感，不影响源文件内容。')
    add_dialog_divider(layout)
    enabled=QCheckBox('启用页面增强')
    enabled.setChecked(owner.enhance.isChecked())
    layout.addWidget(enabled)
    enabled.toggled.connect(owner.enhance.setChecked)
    owner.enhance.toggled.connect(enabled.setChecked)
    # QAction.setChecked does not emit triggered; apply the switch explicitly.
    enabled.toggled.connect(owner.toggle_enhance)
    row=QHBoxLayout()
    row.setSpacing(12)
    row.addWidget(QLabel('柔和度'))
    owner.strength.setMinimumWidth(235)
    owner.strength.setFixedWidth(235)
    row.addWidget(owner.strength,1)
    row.addWidget(owner.strength_label)
    layout.addLayout(row)
    add_dialog_hint(layout,'0% 接近原始扫描图；数值越高，黑白反差越柔和。\n彩色图表和手写笔迹会保留原色。')
    buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
    buttons.button(QDialogButtonBox.StandardButton.Close).setText('完成')
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)


def show_display_settings(owner):
    owner.display_dialog.exec();owner.apply_strength()


def refresh_recent_menu(owner):
    from settings.ne_recent_files import read_recent_files
    from settings.ne_recent_files import VISIBLE_RECENT_FILES
    menu=owner.recent_menu
    stored_paths=read_recent_files(owner.color_settings)
    paths=[path for path in stored_paths if Path(path).is_file()]
    menu.clear()
    if not paths:
        empty=menu.addAction('暂无最近打开的文件')
        empty.setEnabled(False)
    else:
        missing=len(stored_paths)!=len(paths)
        def add_path_action(parent_menu,index,item):
            path=Path(item)
            available=path.is_file()
            nonlocal missing
            missing=missing or not available
            folder=path.parent.name or str(path.parent)
            filename=path.name.replace('&','&&')
            folder=folder.replace('&','&&')
            label=f'&{index}. {filename}    — {folder}'
            if not available:
                label=f'{index}. {filename}（文件不存在）'
            action=parent_menu.addAction(label)
            action.setToolTip(item)
            if available:
                action.triggered.connect(
                    lambda checked=False,value=item: owner.open_recent_file(value))
            else:
                action.setEnabled(False)
            return action
        for index,item in enumerate(paths[:VISIBLE_RECENT_FILES],1):
            add_path_action(menu,index,item)
        remaining=paths[VISIBLE_RECENT_FILES:]
        if remaining:
            more=menu.addMenu(f'更多最近文件（{len(remaining)}）')
            style_menu(more)
            for index,item in enumerate(remaining,VISIBLE_RECENT_FILES+1):
                add_path_action(more,index,item)
        menu.addSeparator()
        clean=menu.addAction('清理失效记录')
        clean.setEnabled(missing)
        clean.triggered.connect(lambda checked=False: clear_invalid_recent_files(owner))
        clear=menu.addAction('清空最近打开记录')
        clear.triggered.connect(lambda checked=False: owner.clear_recent_files())
    style_menu(menu)
    if hasattr(owner,'workspace'):owner.workspace.home.refresh()


def clear_invalid_recent_files(owner):
    from settings.ne_recent_files import prune_missing_recent_files,read_recent_files
    before=len(read_recent_files(owner.color_settings))
    after=prune_missing_recent_files(owner.color_settings)
    refresh_recent_menu(owner)
    if before!=len(after):
        owner.statusBar().showMessage('已清理失效的最近打开记录',4000)


def refresh_menu_state(owner):
    if not hasattr(owner,'menus'):return
    batch=bool(getattr(getattr(getattr(owner,'workspace',None),'home',None),'batch_job',None))
    busy=bool(owner.worker or owner.saver or getattr(owner,'pdf_importer',None)
              or getattr(getattr(owner,'workspace',None),'importer',None) or batch)
    if hasattr(owner,'restart_action'):owner.restart_action.setEnabled(not busy)
    editing_busy=bool(owner.worker or getattr(owner,'pdf_importer',None)
                      or getattr(getattr(owner,'workspace',None),'importer',None) or batch)
    ready=bool(owner.prepared and not editing_busy)
    owner.pdf_note_action.setEnabled(not busy)
    if hasattr(owner,'new_note_action'):owner.new_note_action.setEnabled(not busy)
    if hasattr(owner,'insert_image_action'):
        image_ready=bool(ready and owner.prepared.path.lower().endswith('.ntb')
                         and hasattr(owner.canvas,'image_layers'))
        owner.insert_image_action.setEnabled(image_ready)
        commands,locked=action_states(owner.canvas,image_ready)
        selection=getattr(owner.canvas,'selected_image',None)
        native_selected=bool(selection and str(selection[1]).startswith('native:'))
        item=owner.canvas.editableImageItem(*selection) if selection and hasattr(owner.canvas,'editableImageItem') else None
        if item is None:
            status='图片状态：未选择'
        elif item.get('locked'):
            status='图片状态：已锁定 · 请先解锁才能移动或缩放'
        elif native_selected and item.get('lock_source')=='unmarked':
            status='图片状态：未锁定 · 可移动或等比例缩放'
        else:
            status='图片状态：未锁定 · 可移动或等比例缩放'
        owner.image_status_action.setText(status)
        if hasattr(owner.editor_panel,'image_status_label'):
            owner.editor_panel.image_status_label.setText(status.replace('图片状态：','状态：'))
        for key,action in owner.image_actions.items():
            action.setEnabled(commands[key])
            labels={'up':('上移一层','上移一层'),'down':('下移一层','下移一层'),
                    'front':('移到最上层','移到最上层'),'back':('移到最下层','移到背景上一层')}
            if key in labels:action.setText(labels[key][0 if native_selected else 1])
            action.setToolTip(
                ('在 NTB 中添加原生 type-25 隐藏事件，不会物理删除图片素材；需实机复验'
                 if key=='delete' else
                 ('只跨越与当前图片实际重叠的文字或笔迹，一次点击即可看到遮挡变化；无可移动对象时按钮禁用。'
                  if key in ('up','down') else
                  '移动到当前页所有图片、文字和笔迹的最上层或最下层；已锁定图片须先解除锁定。'))
                if native_selected else action.text())
        lock_action=owner.image_actions['toggle_lock']
        lock_action.setText('解除锁定' if locked else '锁定图片')
        lock_action.setToolTip(
            ('点击解除锁定，保存后使用官方 type-23 状态事件写回；建议先用副本验证'
             if locked else '点击立即锁定图片；禁止继续拖动或缩放')
            if native_selected else
            ('解除锁定：允许拖动与等比例缩放' if locked else '锁定位置和大小'))
        lock_action.setIcon(tool_icon('unlock' if locked else 'lock'))
        if native_selected and not commands['toggle_lock']:
            lock_action.setToolTip('该图片结构无法安全写回锁定状态')
    editable=bool(ready and owner.prepared.path.lower().endswith('.ntb') and hasattr(owner.canvas,'edits'))
    paged=bool(ready and hasattr(owner.canvas,'pageTop'))
    owner.export_pdf_action.setEnabled(ready and not busy)
    owner.export_note_action.setEnabled(bool(ready and not busy and owner.prepared.path.lower().endswith(('.note','.ntb'))))
    # Keep the toolbar shortcut and File > Export in sync throughout load/save.
    owner.reading_panel.export_button.setEnabled(ready and not busy)
    owner.open_action.setEnabled(not busy)
    owner.edit_toolbar.setEnabled(not editing_busy)
    owner.editor_panel.settings.setEnabled(editable)
    owner.save_action.setEnabled(editable)
    owner.save_as_action.setEnabled(editable and not busy)
    owner.select_all_action.setEnabled(editable)
    for name,action in owner.tools.items():action.setEnabled(paged if name=='read' else image_ready if name=='image' else editable)
    for action in (owner.zoom_in_action,owner.zoom_out_action,owner.fit_action):action.setEnabled(ready)
    for action in (owner.previous_action,owner.next_action,owner.first_action,owner.last_action,owner.goto_action):action.setEnabled(paged)
    for action in (owner.ink,owner.sampling_precision_action,owner.smart_interpolation_action,
                   owner.enhance,owner.display_action):action.setEnabled(paged)
    model=getattr(owner.canvas,'edits',None)
    owner.undo_action.setEnabled(bool(editable and model and model.can_undo))
    owner.redo_action.setEnabled(bool(editable and model and model.can_redo))
    if hasattr(owner,'page_sidebar'):owner.page_sidebar.refresh()
    if hasattr(owner,'recent_menu'):owner.recent_menu.setEnabled(not busy)
    selected=bool(editable and getattr(owner.canvas,'selected_indices',None))
    owner.editor_panel.delete_action.setEnabled(selected);owner.editor_panel.clear_action.setEnabled(selected)
    if hasattr(owner,'move_to_page_action'):
        selection=getattr(owner.canvas,'selected_image',None)
        image=owner.canvas.editableImageItem(*selection) if selection else None
        movable=bool(image and not image['locked']) if selection else selected
        owner.move_to_page_action.setEnabled(bool(editable and movable and owner.canvas.page_count>1))

    if hasattr(owner,'workspace'):owner.workspace.refresh_state()

def _dialog_close_buttons(dialog,layout):
    buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
    buttons.button(QDialogButtonBox.StandardButton.Close).setText('关闭')
    buttons.rejected.connect(dialog.accept)
    layout.addWidget(buttons)


def show_about(owner):
    """Consistent, non-blocking-content About window (modal only while opened)."""
    dialog=QDialog(owner)
    dialog.setObjectName('aboutDialog')
    dialog.setWindowTitle('关于 NotabilityEditor')
    dialog.setMinimumWidth(490)
    layout=QVBoxLayout(dialog)
    layout.setContentsMargins(26,24,26,22)
    layout.setSpacing(16)
    add_dialog_header(layout,'NotabilityEditor','版本 0.7.9-Beta · Windows 本地笔记查看与编辑')
    add_dialog_divider(layout)
    info=QLabel('打开文件支持 NTB、NOTE；导入功能目前只支持 PDF，并创建可编辑的 NTB。\nNTB 支持画笔、荧光笔、页面管理及批量删除。\n'
                '可导出带底图和笔迹的 PDF，也可导出 NOTE。')
    info.setWordWrap(True)
    layout.addWidget(info)
    add_dialog_hint(layout,'兼容性提示：PDF → NTB 与 NTB → NOTE 尚未通过官方 Notability 客户端验证；'
                    '尚未通过官方 Notability 完整兼容验证。导出前请备份原始文件。',warning=True)
    license_label=QLabel('本项目原创代码采用 0BSD 许可，无需署名；上游及第三方组件遵循各自许可。\n软件按现状提供，使用前请备份；许可范围和免责条款随程序资源及源码包提供（LICENSE_SCOPE、THIRD_PARTY_NOTICES、DISCLAIMER）。')
    license_label.setWordWrap(True)
    layout.addWidget(license_label)
    _dialog_close_buttons(dialog,layout)
    dialog.exec()


def show_help(owner):
    """A readable, categorized reference instead of a very tall message box."""
    dialog=QDialog(owner)
    dialog.setObjectName('helpDialog')
    dialog.setWindowTitle('操作与快捷键')
    dialog.resize(650,550)
    dialog.setMinimumSize(570,440)
    layout=QVBoxLayout(dialog)
    layout.setContentsMargins(24,22,24,20)
    layout.setSpacing(14)
    add_dialog_header(layout,'操作指南与快捷键','以下说明使用默认快捷键。你自定义的快捷键以“自定义快捷键”窗口显示的当前绑定为准。')
    customize=QPushButton('录制 / 自定义快捷键…')
    customize.setObjectName('primaryAction')
    customize.clicked.connect(lambda:(dialog.accept(),show_shortcuts(owner)))
    layout.addWidget(customize)
    tabs=QTabWidget(dialog)
    data=(
        ('文件',[
            ('新建笔记', 'Ctrl+N · 选择 A4、信封、A5、B5、Legal、A3、DL/C5 等纸张和空白、横线、点状底页'),
            ('打开', 'Ctrl+O · 只打开 NTB 或 NOTE；拖入这两种文件也会直接打开'),
            ('最近打开', '文件 → 最近打开 保留最近 100 个笔记，主菜单显示 10 个，其余通过“更多最近文件”查看；失效路径可单独清理'),
            ('导入 PDF', 'Ctrl+Shift+N · 拖入 PDF 或点击导入，生成可编辑 NTB，原 PDF 不变'),
            ('保存', 'Ctrl+S 保存修改；Ctrl+Shift+S 另存为 NTB'),
            ('导出 PDF', 'Ctrl+Shift+E · 保留页面背景、图片和可见笔迹'),
            ('导出 NOTE', '原 NOTE 无损复制；NTB → NOTE 包含背景 PDF 和独立笔迹，仍属实验性转换'),
        ]),
        ('编辑',[
            ('切换工具', 'B 画笔 · H 荧光笔 · E 橡皮擦 · S 选择 · V 阅读'),
            ('自定义颜色', '画笔、荧光笔分别支持预设色与自定义 HEX 色值'),
            ('笔宽和预览', '两种工具最小笔宽 0.1；移动鼠标可预览实际笔尖大小'),
            ('笔迹操作', 'Ctrl+Z 撤销 · Ctrl+Y / Ctrl+Shift+Z 回退 · Delete 删除选中笔迹'),
            ('笔迹选取', '工具 → 选择 切换自由形状或矩形，以及接触整笔、完整包含、字形归组；Shift 追加，Alt 减选，Esc 取消'),
            ('图片编辑', 'Ctrl+Shift+I 插入图片，选择工具拖动移动或拖动四角等比例缩放；主工具栏「图片」显示二级操作栏，可固定、解锁、调层级及删除（背景不可越过）'),
        ]),
        ('页面',[
            ('无缝衔接', '在页面菜单勾选或取消；开启后画笔、荧光笔和橡皮擦可连续跨页，一次操作可一次撤销'),
            ('页面侧栏', 'Ctrl+Shift+P 显示/隐藏；右键缩略图进入页面操作'),
            ('粘贴', '可插入当前页之前或之后，也可覆盖当前页；Ctrl+V 默认插入后面'),
            ('批量删除', '点击「批量选择页面」，支持全选、取消全选、反选、删除所选'),
            ('批量快捷键', 'Ctrl+A 全选 · Ctrl+I 反选 · Delete 删除所选 · Esc 退出'),
            ('撤销', '批量删除作为一次编辑操作，Ctrl+Z 可整批恢复；至少保留一页'),
        ]),
        ('视图',[
            ('缩放', 'Ctrl+= 放大 · Ctrl+- 缩小 · Ctrl+0 适应页面宽度'),
            ('翻页', 'Alt+Left / Alt+Right · Ctrl+L 输入页码，再按 Enter 跳转'),
            ('图像增强', '在「视图 → 页面增强设置」调节扫描页面柔和度'),
            ('只读限制', 'NOTE 仅可阅读；PDF 通过导入创建 NTB 后才能进行笔迹和页面编辑'),
        ]),
    )
    for tab_name,entries in data:
        pane=QWidget()
        pane_layout=QVBoxLayout(pane)
        pane_layout.setContentsMargins(16,20,16,12)
        pane_layout.setSpacing(13)
        for heading,description in entries:
            header=QLabel(heading)
            header.setStyleSheet('font-weight:600;color:#0f172a;font-size:13px;')
            pane_layout.addWidget(header)
            details=QLabel(description)
            details.setWordWrap(True)
            details.setTextInteractionFlags(details.textInteractionFlags() |
                Qt.TextInteractionFlag.TextSelectableByMouse)
            details.setStyleSheet('color:#64748b;')
            pane_layout.addWidget(details)
        pane_layout.addStretch()
        scroll=QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet('background:#ffffff;border:0;')
        scroll.setWidget(pane)
        tabs.addTab(scroll,tab_name)
    layout.addWidget(tabs,1)
    _dialog_close_buttons(dialog,layout)
    dialog.exec()

