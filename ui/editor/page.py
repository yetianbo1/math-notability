"""Keep the original native canvas layout inside a separate editor page."""
from PyQt6.QtWidgets import QWidget, QVBoxLayout


class EditorPage(QWidget):
    def __init__(self, scroll):
        super().__init__()
        self.setObjectName('desktopEditor')
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(scroll, 1)
