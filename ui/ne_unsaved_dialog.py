"""Focused dialog for leaving a note with unsaved changes."""
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QPushButton, QStyle, QVBoxLayout,
)

from ui.ne_ui import add_dialog_divider, add_dialog_header


CHOICE_SAVE = "save"
CHOICE_DISCARD = "discard"
CHOICE_CANCEL = "cancel"


class UnsavedChangesDialog(QDialog):
    def __init__(self, parent=None, *, note_name, changes,
                 pdf_draft=False, draft_kind=None, continue_label="继续"):
        super().__init__(parent)
        self.choice = CHOICE_CANCEL
        self.setObjectName("unsavedChangesDialog")
        self.setWindowTitle("未保存的修改")
        self.setModal(True)
        self.setMinimumWidth(520)
        self.setMaximumWidth(620)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 18)
        layout.setSpacing(14)

        heading_row = QHBoxLayout()
        heading_row.setSpacing(14)
        icon_label = QLabel()
        icon = self.style().standardIcon(QStyle.StandardPixmap.SP_MessageBoxWarning)
        icon_label.setPixmap(icon.pixmap(34, 34))
        icon_label.setFixedSize(38, 38)
        icon_label.setAlignment(Qt.AlignmentFlag.AlignTop)
        heading_row.addWidget(icon_label)

        heading_column = QVBoxLayout()
        heading_column.setSpacing(4)
        add_dialog_header(
            heading_column,
            "未保存的修改",
            f"《{note_name}》中有尚未写入文件的更改。",
        )
        heading_row.addLayout(heading_column, 1)
        layout.addLayout(heading_row)

        descriptions = list(changes)
        if pdf_draft and draft_kind is None:
            draft_kind = 'pdf'
        draft_descriptions = {
            'pdf': "PDF 导入的笔记尚未保存为正式 NTB 文件",
            'new': "新建笔记尚未保存为正式 NTB 文件",
        }
        if draft_kind in draft_descriptions:
            descriptions.insert(0, draft_descriptions[draft_kind])
        if not descriptions:
            descriptions.append("当前笔记包含尚未保存的编辑")
        summary = QLabel("\n".join(f"• {item}" for item in descriptions))
        summary.setObjectName("dialogHint")
        summary.setWordWrap(True)
        summary.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(summary)

        warning = QLabel(
            f"如果选择“不保存”，这些修改将丢失。选择“取消”可返回笔记继续编辑。"
        )
        warning.setObjectName("dialogWarning")
        warning.setWordWrap(True)
        layout.addWidget(warning)

        add_dialog_divider(layout)
        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        buttons.addStretch()

        self.cancel_button = QPushButton("取消")
        self.cancel_button.setObjectName("cancelButton")
        self.cancel_button.clicked.connect(self.reject)
        buttons.addWidget(self.cancel_button)

        self.discard_button = QPushButton("不保存")
        self.discard_button.setObjectName("dangerAction")
        self.discard_button.clicked.connect(self._discard)
        buttons.addWidget(self.discard_button)

        self.save_button = QPushButton(f"保存并{continue_label}")
        self.save_button.setObjectName("primaryAction")
        self.save_button.setDefault(True)
        self.save_button.clicked.connect(self._save)
        buttons.addWidget(self.save_button)
        layout.addLayout(buttons)
        self.adjustSize()

    def _save(self):
        self.choice = CHOICE_SAVE
        self.accept()

    def _discard(self):
        self.choice = CHOICE_DISCARD
        self.accept()

    def reject(self):
        self.choice = CHOICE_CANCEL
        super().reject()


def show_unsaved_changes_dialog(parent, *, note_name, changes,
                                pdf_draft=False, draft_kind=None,
                                continue_label="继续"):
    dialog = UnsavedChangesDialog(
        parent,
        note_name=note_name,
        changes=changes,
        pdf_draft=pdf_draft,
        draft_kind=draft_kind,
        continue_label=continue_label,
    )
    dialog.exec()
    return dialog.choice
