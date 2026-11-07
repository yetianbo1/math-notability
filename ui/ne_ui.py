"""Small presentation helpers for dialogs. No document state is modified here."""
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QFrame, QLabel, QVBoxLayout


def add_dialog_header(layout, title, subtitle):
    """Introduce one clear heading and a wrapping explanation to a dialog."""
    heading=QLabel(title)
    heading.setObjectName('dialogTitle')
    heading.setWordWrap(True)
    layout.addWidget(heading)
    explanation=QLabel(subtitle)
    explanation.setObjectName('dialogSubtitle')
    explanation.setWordWrap(True)
    layout.addWidget(explanation)
    return heading


def add_dialog_hint(layout, text, warning=False):
    label=QLabel(text)
    label.setObjectName('dialogWarning' if warning else 'dialogHint')
    label.setWordWrap(True)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    layout.addWidget(label)
    return label


def add_dialog_divider(layout):
    divider=QFrame()
    divider.setObjectName('dialogDivider')
    divider.setFixedHeight(1)
    layout.addWidget(divider)
    return divider


def prepare_progress(dialog, title):
    """Unify native progress dialog dimensions while preserving cancel behavior."""
    dialog.setWindowTitle(title)
    dialog.setMinimumWidth(440)
    dialog.setAutoClose(False)
    dialog.setMinimumDuration(150)
    return dialog
