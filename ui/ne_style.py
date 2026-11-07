"""Shared menu appearance for the menu bar, submenus and page popup."""
from PyQt6.QtCore import Qt

MENU_STYLE = '''
    QMenu { background:#ffffff; color:#334155; border:1px solid #e0e5ed; border-radius:12px; padding:7px 5px; }
    QMenu::item { padding:9px 28px 9px 12px; min-width:160px; border-radius:7px; margin:1px 4px; font-size:13px; }
    QMenu::item:selected { background:#edf3ff; color:#2456b8; }
    QMenu::item:disabled { color:#a4adbb; }
    QMenu::icon { padding-left:7px; }
    QMenu::separator { height:1px; background:#edf0f5; margin:6px 12px; }
'''

MENUBAR_STYLE = '''
    QMenuBar { background:#ffffff; color:#475569; padding:3px 6px; border-bottom:1px solid #edf1f6; font-size:13px; }
    QMenuBar::item { padding:5px 13px; background:transparent; border-radius:6px; }
    QMenuBar::item:selected,QMenuBar::item:pressed { background:#edf3ff; color:#2456b8; }
'''

def style_menu(menu):
    menu.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    menu.setStyleSheet(MENU_STYLE)


# Consistent, high-contrast Windows desktop styling. Widgets which render a
# document (PDF / ink canvas) intentionally have no global background rule.
APP_STYLE = r"""
QMainWindow { background:#f6f8fc; color:#1e293b; }
QStatusBar { background:#ffffff; color:#64748b; border-top:1px solid #e2e8f0; padding:2px 10px; font-size:12px; }
QStatusBar::item { border:0; }
QDialog, QMessageBox, QProgressDialog { background:#f8fafc; color:#1e293b; }
QDialog QLabel, QMessageBox QLabel, QProgressDialog QLabel { color:#334155; font-size:13px; }
QDialog QLabel#dialogTitle { color:#0f172a; font-size:20px; font-weight:700; }
QDialog QLabel#dialogSubtitle { color:#64748b; font-size:13px; }
QDialog QLabel#dialogHint { background:#eef4ff; color:#36527b; border:1px solid #d5e3fc; border-radius:9px; padding:12px; }
QDialog QLabel#dialogWarning { background:#fff7ed; color:#9a3412; border:1px solid #fed7aa; border-radius:9px; padding:12px; }
QDialog QFrame#dialogDivider { background:#e2e8f0; border:none; max-height:1px; }
QDialog QPushButton, QMessageBox QPushButton, QProgressDialog QPushButton {
    background:#ffffff; color:#334155; border:1px solid #cbd5e1;
    border-radius:8px; padding:8px 16px; min-width:66px; min-height:20px; font-size:13px;
}
QDialog QPushButton:hover, QMessageBox QPushButton:hover, QProgressDialog QPushButton:hover { background:#f1f5f9; border-color:#94a3b8; }
QDialog QPushButton:pressed, QMessageBox QPushButton:pressed { background:#e2e8f0; }
QDialog QPushButton:focus, QMessageBox QPushButton:focus { border:2px solid #2563eb; padding:7px 15px; }
QDialog QPushButton:default, QMessageBox QPushButton:default,
QDialog QPushButton#primaryAction { background:#2563eb; color:#ffffff; border-color:#2563eb; font-weight:600; }
QDialog QPushButton:default:hover, QMessageBox QPushButton:default:hover,
QDialog QPushButton#primaryAction:hover { background:#1d4ed8; }
QDialog QPushButton#dangerAction { background:#dc2626; color:#ffffff; border-color:#dc2626; font-weight:600; }
QDialog QPushButton#dangerAction:hover { background:#b91c1c; }
QDialog QPushButton:disabled, QMessageBox QPushButton:disabled { color:#94a3b8; background:#f1f5f9; border-color:#e2e8f0; }
QDialog QCheckBox { color:#334155; spacing:9px; font-size:13px; }
QDialog QCheckBox::indicator { width:18px; height:18px; }
QDialog QSlider::groove:horizontal { background:#e2e8f0; height:6px; border-radius:3px; }
QDialog QSlider::handle:horizontal { background:#2563eb; border:2px solid #ffffff; width:17px; height:17px; margin:-7px 0; border-radius:9px; }
QDialog QTabWidget::pane { background:#ffffff; border:1px solid #e2e8f0; border-radius:10px; padding:10px; }
QDialog QTabBar::tab { color:#64748b; background:transparent; padding:10px 17px; margin-right:3px; border-bottom:2px solid transparent; }
QDialog QTabBar::tab:selected { color:#1d4ed8; border-color:#2563eb; font-weight:600; }
QDialog QTabBar::tab:hover { color:#1d4ed8; }
QProgressBar { background:#e2e8f0; border:0; border-radius:5px; text-align:center; color:#0f172a; min-height:10px; }
QProgressBar::chunk { background:#2563eb; border-radius:5px; }
QToolTip { color:#334155; background:#ffffff; border:1px solid #cbd5e1; border-radius:5px; padding:7px; font-size:12px; }
"""
