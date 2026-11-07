"""Shortcut recording, conflict checks, QSettings persistence and QAction binding."""
from PyQt6.QtCore import Qt, QSettings
from PyQt6.QtGui import QKeySequence
from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
                             QTreeWidget, QTreeWidgetItem, QMessageBox, QDialogButtonBox,
                             QAbstractItemView)
from ui.ne_shortcut_data import SPECS, default_bindings, conflicting
from ui.ne_ui import add_dialog_header, add_dialog_hint


def normalize(text):
    if not text:
        return ''
    seq = QKeySequence.fromString(text, QKeySequence.SequenceFormat.PortableText)
    return seq.toString(QKeySequence.SequenceFormat.PortableText)


def normalize_binding(binding):
    return ';'.join(filter(None, (normalize(item) for item in binding.split(';'))))


def key_from_event(event):
    if event.key() in (Qt.Key.Key_Control, Qt.Key.Key_Shift, Qt.Key.Key_Alt,
                       Qt.Key.Key_Meta, Qt.Key.Key_unknown):
        return ''
    return QKeySequence(event.keyCombination()).toString(QKeySequence.SequenceFormat.PortableText)


def find_conflict(bindings, key, binding):
    new = set(filter(None, binding.split(';')))
    if not new:
        return None
    for other, value in bindings.items():
        if other != key and conflicting(key, other) and new.intersection(value.split(';')):
            return other
    return None


class ShortcutManager:
    def __init__(self, owner):
        self.owner = owner
        self.settings = QSettings('NotabilityEditor', 'NotabilityEditor')
        self.bindings = default_bindings()
        for key in SPECS:
            value = self.settings.value('shortcuts/' + key, None)
            if value is not None:
                self.bindings[key] = normalize_binding(str(value))
        # Safeguard imported or manually modified registry values.
        for key in SPECS:
            if find_conflict(self.bindings, key, self.bindings[key]):
                self.bindings[key] = SPECS[key][2]

    def matches(self, name, event):
        entered = key_from_event(event)
        return bool(entered and entered in self.bindings[name].split(';'))

    def apply(self):
        owner = self.owner
        actions = {
            'fullscreen': owner.fullscreen_action,
            'open': owner.open_action, 'save': owner.save_action,
            'save_as': owner.save_as_action, 'pdf_note': owner.pdf_note_action, 'export_pdf': owner.export_pdf_action,
            'export_note': owner.export_note_action,
            'undo': owner.undo_action, 'redo': owner.redo_action,
            'previous': owner.previous_action, 'next': owner.next_action,
            'zoom_in': owner.zoom_in_action, 'zoom_out': owner.zoom_out_action,
            'fit': owner.fit_action, 'goto': owner.goto_action,
            'sidebar': owner.page_toggle_action, 'help': owner.help_action,
        }
        actions.update({key: owner.tools[key] for key in ('draw', 'highlight', 'erase', 'select', 'image', 'read')})
        for name, action in actions.items():
            shortcuts = [QKeySequence.fromString(s, QKeySequence.SequenceFormat.PortableText)
                         for s in self.bindings[name].split(';') if s]
            action.setShortcuts(shortcuts)
        owner.editor_panel.page_button.setToolTip('页面管理（' + (self.bindings['sidebar'] or '未分配') + '）')

    def save(self, bindings):
        for name, value in bindings.items():
            self.settings.setValue('shortcuts/' + name, value)
        self.settings.sync()
        self.bindings = dict(bindings)
        self.apply()


class RecordButton(QPushButton):
    def __init__(self, callback, parent=None):
        super().__init__('录制快捷键', parent)
        self.callback = callback
        self.recording = False
        self.clicked.connect(self.start_recording)

    def start_recording(self):
        self.recording = True
        self.setText('请按下组合键…（Esc 取消）')
        self.setFocus(Qt.FocusReason.OtherFocusReason)

    def keyPressEvent(self, event):
        if not self.recording:
            return super().keyPressEvent(event)
        if event.key() == Qt.Key.Key_Escape:
            self.stop_recording()
            event.accept(); return
        value = key_from_event(event)
        if value:
            self.stop_recording()
            self.callback(value)
        event.accept()

    def keyReleaseEvent(self, event):
        if self.recording:
            event.accept(); return
        super().keyReleaseEvent(event)

    def stop_recording(self):
        self.recording = False
        self.setText('录制快捷键')


class ShortcutsDialog(QDialog):
    def __init__(self, manager, parent=None):
        super().__init__(parent)
        self.manager = manager
        self.bindings = dict(manager.bindings)
        self.setWindowTitle('自定义操作与快捷键')
        self.resize(680, 575)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(13)
        add_dialog_header(layout, '自定义快捷键', '选中操作后点击“录制快捷键”，直接按下新组合键。')
        add_dialog_hint(layout, '修改在点击“保存”后生效，重启后继续保留。Escape 取消录制；冲突快捷键不能同时分配给有重叠作用范围的操作。')
        self.tree = QTreeWidget(self)
        self.tree.setHeaderLabels(['操作', '当前快捷键'])
        self.tree.setRootIsDecorated(True)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.rows = {}
        groups = {}
        for key, (category, label, _, _) in SPECS.items():
            if category not in groups:
                groups[category] = QTreeWidgetItem(self.tree, [category, ''])
            row = QTreeWidgetItem(groups[category], [label, self.bindings[key] or '未设置'])
            row.setData(0, Qt.ItemDataRole.UserRole, key)
            self.rows[key] = row
        self.tree.expandAll()
        self.tree.setColumnWidth(0, 390)
        self.tree.setCurrentItem(self.rows['open'])
        layout.addWidget(self.tree, 1)
        edit = QHBoxLayout()
        self.record = RecordButton(self.assign, self)
        edit.addWidget(self.record)
        clear = QPushButton('清除当前绑定')
        clear.clicked.connect(lambda: self.assign(''))
        edit.addWidget(clear)
        restore = QPushButton('全部恢复默认')
        restore.clicked.connect(self.restore_all)
        edit.addWidget(restore)
        edit.addStretch()
        layout.addLayout(edit)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText('保存')
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('取消')
        buttons.accepted.connect(self.accept_changes)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def selected(self):
        item = self.tree.currentItem()
        return item.data(0, Qt.ItemDataRole.UserRole) if item else None

    def assign(self, shortcut):
        key = self.selected()
        if key is None:
            QMessageBox.information(self, '请选择操作', '请先选中列表中的具体操作。')
            return
        shortcut = normalize(shortcut)
        if shortcut in ('Alt+F4', 'Ctrl+Alt+Delete', 'Meta+L'):
            QMessageBox.warning(self, '系统保留快捷键', '该组合键由 Windows 系统保留，请选择其他按键。')
            return
        conflict = find_conflict(self.bindings, key, shortcut)
        if conflict:
            QMessageBox.warning(self, '快捷键冲突',
                                f'“{shortcut}”已被“{SPECS[conflict][1]}”使用。\n请先为该操作设置其他按键或清除绑定。')
            return
        self.bindings[key] = shortcut
        self.rows[key].setText(1, shortcut or '未设置')

    def restore_all(self):
        self.bindings = default_bindings()
        for key, row in self.rows.items():
            row.setText(1, self.bindings[key] or '未设置')

    def accept_changes(self):
        self.manager.save(self.bindings)
        self.accept()


def show_shortcuts(owner):
    ShortcutsDialog(owner.shortcut_manager, owner).exec()
