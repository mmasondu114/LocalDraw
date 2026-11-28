"""Refined Warm Paper & Celadon Sage native Qt6 themes for LocalDraw."""

from __future__ import annotations

from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication

from .icons import ensure_qss_icons

THEMES: dict[str, dict[str, str]] = {
    "linen": {
        "name": "暖纸工作室（默认）",
        # Tier 0: Shell & Chrome (crisp warm alabaster)
        "canvas": "#F4F1EC",
        "header": "#EFECE6",
        "sidebar": "#EFECE6",
        # Tier 1: Elevated Workspace Panes & Cards
        "panel": "#FAF8F5",
        "raised": "#F1EDE6",
        "raised_hover": "#E8E2D8",
        "raised_press": "#DDD5C9",
        # Tier 2: Pure Inputs, Segmented Tracks & Gallery Stage
        "field": "#FFFFFF",
        "field_border": "#DDD6CC",
        "field_hover": "#BDB3A7",
        "segment_bg": "#E9E4DC",
        "gallery_bg": "#EAE6DF",
        "gallery_empty": "#F1EDE7",
        "gallery_stage": "#242321",
        # Quiet Hairlines
        "line": "#E2DCD3",
        "line_subtle": "#ECE7DF",
        "btn_border": "#D7D0C5",
        # Typography
        "ink": "#25201D",
        "muted": "#6E655D",
        "caption": "#8B8178",
        "placeholder": "#A59C93",
        # Primary Terracotta Accent
        "accent": "#9E5233",
        "accent_hover": "#874328",
        "accent_press": "#70361F",
        "accent_ink": "#FFFDF9",
        "soft": "#F3E6DD",
        "nav_active_bg": "#E6DCD1",
        "nav_active_ink": "#7B3C22",
        # Home Card 01
        "card_hero_bg": "#FAF8F5",
        "card_hero_border": "#CCA18B",
        # Semantic
        "success": "#3A6D4B",
        "success_bg": "#E6EFE9",
        "warning": "#8A5618",
        "warning_bg": "#FAF0DC",
        "warning_border": "#DFC48C",
        "danger": "#B34034",
        "danger_hover_bg": "#F8E6E3",
        "queue_running_bg": "#F4EAE1",
        "queue_running_border": "#CE9F83",
        "toast_bg": "#2B2521",
        "toast_ink": "#FAF7F2",
    },
    "sage": {
        "name": "浅黛青瓷",
        # Tier 0: Nordic Mist-Sage Shell
        "canvas": "#F3F6F4",
        "header": "#ECF1EE",
        "sidebar": "#ECF1EE",
        # Tier 1: Clean White Studio Panes
        "panel": "#FFFFFF",
        "raised": "#F1F5F2",
        "raised_hover": "#E4ECE7",
        "raised_press": "#D6E2DB",
        # Tier 2: Inputs & Stage
        "field": "#FFFFFF",
        "field_border": "#D2DDD6",
        "field_hover": "#9CB4A6",
        "segment_bg": "#E4ECE7",
        "gallery_bg": "#E7EDE9",
        "gallery_empty": "#EFF4F1",
        "gallery_stage": "#1F2422",
        # Quiet Hairlines
        "line": "#DBE4DE",
        "line_subtle": "#E9EFEA",
        "btn_border": "#CED9D2",
        # Typography
        "ink": "#1A2B24",
        "muted": "#5B6D64",
        "caption": "#778980",
        "placeholder": "#98A9A0",
        # Deep Pine/Jade Accent
        "accent": "#256B58",
        "accent_hover": "#1D5646",
        "accent_press": "#164337",
        "accent_ink": "#FFFFFF",
        "soft": "#E4F0EA",
        "nav_active_bg": "#DBE8E1",
        "nav_active_ink": "#1B5243",
        "card_hero_bg": "#FFFFFF",
        "card_hero_border": "#8FBBA8",
        # Semantic
        "success": "#266B4F",
        "success_bg": "#E3F1EA",
        "warning": "#855317",
        "warning_bg": "#FBF1DC",
        "warning_border": "#DFC58E",
        "danger": "#B23A36",
        "danger_hover_bg": "#F8E5E4",
        "queue_running_bg": "#E6F2EC",
        "queue_running_border": "#82B89E",
        "toast_bg": "#1D2C26",
        "toast_ink": "#F4F8F6",
    },
}

_ACTIVE_THEME: str = "linen"


def current_theme() -> str:
    return _ACTIVE_THEME


def tokens(theme_name: str | None = None) -> dict[str, str]:
    key = theme_name or _ACTIVE_THEME
    return THEMES.get(key, THEMES["linen"])


def build_qss(theme_name: str = "linen", large_text: bool = False) -> str:
    t = tokens(theme_name)
    ic = ensure_qss_icons(theme_name, t)
    # 14px body = Windows 11 Fluent body size; screen density is handled by scaling.py (QT_SCALE_FACTOR).
    base_px = 16 if large_text else 14
    small_px = 13 if large_text else 12
    h1_px = 22 if large_text else 19
    h2_px = 16 if large_text else 15
    home_h1 = 30 if large_text else 26
    return f"""
* {{
    font-family: "SF Pro Text", "Segoe UI Variable Text", "Segoe UI", "PingFang SC", "Microsoft YaHei UI", sans-serif;
    font-size: {base_px}px;
    color: {t["ink"]};
}}
QMainWindow, QDialog {{
    background-color: {t["canvas"]};
}}
QWidget#Canvas, QScrollArea, QScrollArea > QWidget > QWidget {{
    background-color: transparent;
}}
QFrame#TopHeader {{
    background-color: {t["header"]};
    border-bottom: 1px solid {t["line"]};
}}
QFrame#HeaderDivider {{
    background-color: {t["line"]};
    border: none;
}}
QFrame#Sidebar {{
    background-color: {t["sidebar"]};
    border-right: 1px solid {t["line"]};
}}
QFrame#SidebarSep {{
    background-color: {t["line"]};
    border: none;
}}
QFrame#Pane {{
    background-color: {t["panel"]};
    border: 1px solid {t["line"]};
    border-radius: 10px;
}}
QFrame#Card {{
    background-color: {t["panel"]};
    border: 1px solid {t["line"]};
    border-radius: 9px;
}}
QFrame#SubCard {{
    background-color: {t["raised"]};
    border: 1px solid {t["line_subtle"]};
    border-radius: 8px;
}}
QFrame#PaneFooter {{
    background-color: transparent;
    border: none;
    border-top: 1px solid {t["line_subtle"]};
}}
QFrame#HeroCard {{
    background-color: {t["card_hero_bg"]};
    border: 1.5px solid {t["card_hero_border"]};
    border-radius: 12px;
}}
QFrame#HomeCard {{
    background-color: {t["panel"]};
    border: 1px solid {t["line"]};
    border-radius: 12px;
}}
QFrame#HomeCard:hover, QFrame#HeroCard:hover {{
    border: 1.5px solid {t["accent"]};
}}
QFrame#ListRow {{
    background-color: transparent;
    border: none;
    border-bottom: 1px solid {t["line_subtle"]};
    border-radius: 6px;
}}
QFrame#ListRow:hover {{
    background-color: {t["raised"]};
}}
QFrame#GalleryPane {{
    background-color: {t["panel"]};
    border: 1px solid {t["line"]};
    border-radius: 10px;
}}
QFrame#GalleryStage {{
    background-color: {t["gallery_stage"]};
    border: 1px solid {t["line"]};
    border-radius: 8px;
}}
QFrame#GalleryEmpty {{
    background-color: {t["gallery_empty"]};
    border: 1px solid {t["line_subtle"]};
    border-radius: 8px;
}}
QFrame#StatusPill {{
    background-color: {t["soft"]};
    border: 1px solid {t["line"]};
    border-radius: 14px;
}}
QFrame#StatusPill:hover {{
    border-color: {t["field_hover"]};
    background-color: {t["raised_hover"]};
}}
QFrame#StatusPill[state="ready"] {{
    background-color: {t["success_bg"]};
    border-color: {t["line"]};
}}
QFrame#StatusPill[state="ready"]:hover {{
    border-color: {t["success"]};
}}
QFrame#StatusPill[state="busy"] {{
    background-color: {t["queue_running_bg"]};
    border-color: {t["queue_running_border"]};
}}
QFrame#SegmentTrack {{
    background-color: {t["segment_bg"]};
    border: 1px solid {t["line_subtle"]};
    border-radius: 8px;
}}
QFrame#JobBanner {{
    background-color: {t["queue_running_bg"]};
    border: 1px solid {t["queue_running_border"]};
    border-radius: 8px;
}}
QFrame#JobBanner[state="idle"] {{
    background-color: {t["panel"]};
    border: 1px solid {t["line_subtle"]};
}}
QFrame#JobBanner[state="done"] {{
    background-color: {t["panel"]};
    border: 1px solid {t["line"]};
}}
QProgressBar[state="done"]::chunk {{
    background-color: {t["success"]};
}}
QFrame#JobBanner[state="paused"],
QFrame#JobBanner[state="failed"] {{
    background-color: {t["warning_bg"]};
    border: 1px solid {t["warning_border"]};
}}
QFrame#NoticeBar {{
    background-color: {t["toast_bg"]};
    border: 1px solid rgba(255, 255, 255, 28);
    border-radius: 12px;
}}
QFrame#NoticeBar QLabel {{
    color: {t["toast_ink"]};
    font-weight: 500;
    background: transparent;
}}
QFrame#NoticeBar QLabel#ToastIconBadge {{
    background-color: rgba(255, 255, 255, 34);
    border-radius: 11px;
}}
QFrame#NoticeBar QPushButton {{
    color: {t["toast_ink"]};
    background-color: transparent;
    border: 1px solid transparent;
    border-radius: 6px;
}}
QFrame#NoticeBar QPushButton:hover {{
    background-color: rgba(255, 255, 255, 26);
}}
QFrame#NoticeBar QPushButton:pressed {{
    background-color: rgba(255, 255, 255, 42);
}}
QFrame#NoticeBar[error="true"] {{
    background-color: {t["danger"]};
    border: 1px solid rgba(255, 255, 255, 42);
}}
QMessageBox {{
    background-color: {t["panel"]};
}}
QMessageBox QLabel {{
    color: {t["ink"]};
    min-width: 240px;
}}
QMessageBox QPushButton {{
    min-width: 78px;
    padding: 6px 14px;
}}
QFrame#WarnBox {{
    background-color: {t["warning_bg"]};
    border: 1px solid {t["warning_border"]};
    border-radius: 7px;
}}
QFrame#InfoBox {{
    background-color: {t["raised"]};
    border: 1px solid {t["line_subtle"]};
    border-radius: 7px;
}}
QLabel#BrandTitle {{
    font-size: 15px;
    font-weight: 700;
    letter-spacing: 0.4px;
}}
QLabel#HomeHeroTitle {{
    font-size: {home_h1}px;
    font-weight: 700;
}}
QLabel#StageTitle {{
    font-size: {h1_px}px;
    font-weight: 700;
}}
QLabel#SectionTitle {{
    font-size: {h2_px}px;
    font-weight: 600;
}}
QLabel#Caption {{
    font-size: {small_px}px;
    color: {t["caption"]};
    font-weight: 600;
}}
QLabel#Muted {{
    font-size: {small_px}px;
    color: {t["muted"]};
}}
QLabel#Keycap {{
    background-color: {t["raised"]};
    color: {t["muted"]};
    border: 1px solid {t["line"]};
    border-radius: 4px;
    padding: 1px 6px;
    font-size: {small_px}px;
    font-weight: 600;
}}
QLabel#BadgeSuccess {{
    background-color: {t["success_bg"]};
    color: {t["success"]};
    border-radius: 5px;
    padding: 2px 7px;
    font-size: {small_px}px;
    font-weight: 600;
}}
QLabel#BadgeAccent {{
    background-color: {t["soft"]};
    color: {t["nav_active_ink"]};
    border-radius: 5px;
    padding: 2px 7px;
    font-size: {small_px}px;
    font-weight: 600;
}}
QLabel#BadgeWarn {{
    background-color: {t["warning_bg"]};
    color: {t["warning"]};
    border: 1px solid {t["warning_border"]};
    border-radius: 5px;
    padding: 2px 7px;
    font-size: {small_px}px;
    font-weight: 600;
}}
QLabel#BadgeMuted {{
    background-color: {t["raised"]};
    color: {t["muted"]};
    border: 1px solid {t["line_subtle"]};
    border-radius: 5px;
    padding: 2px 7px;
    font-size: {small_px}px;
    font-weight: 600;
}}
QPushButton {{
    background-color: {t["raised"]};
    color: {t["ink"]};
    border: 1px solid {t["btn_border"]};
    border-radius: 7px;
    padding: 5px 12px;
    min-height: 20px;
    font-weight: 500;
}}
QPushButton:hover {{
    background-color: {t["raised_hover"]};
    border-color: {t["field_hover"]};
}}
QPushButton:pressed {{
    background-color: {t["raised_press"]};
    border-color: {t["accent"]};
}}
QPushButton:disabled {{
    color: {t["placeholder"]};
    background-color: transparent;
    border-color: {t["line_subtle"]};
}}
QPushButton[primary="true"] {{
    background-color: {t["accent"]};
    color: {t["accent_ink"]};
    border: none;
    font-weight: 600;
}}
QPushButton[primary="true"]:hover {{
    background-color: {t["accent_hover"]};
}}
QPushButton[primary="true"]:pressed {{
    background-color: {t["accent_press"]};
}}
QPushButton[primary="true"]:disabled {{
    background-color: {t["line"]};
    color: {t["muted"]};
}}
QPushButton[ghost="true"] {{
    background-color: transparent;
    color: {t["muted"]};
    border: 1px solid transparent;
    border-radius: 6px;
    padding: 4px 8px;
}}
QPushButton[ghost="true"]:hover {{
    background-color: {t["raised_hover"]};
    color: {t["ink"]};
}}
QPushButton[ghost="true"]:pressed {{
    background-color: {t["raised_press"]};
}}
QPushButton[ghost="true"]:checked {{
    background-color: {t["soft"]};
    color: {t["nav_active_ink"]};
}}
QPushButton[danger="true"] {{
    background-color: transparent;
    color: {t["muted"]};
    border: 1px solid transparent;
    border-radius: 6px;
}}
QPushButton[danger="true"]:hover {{
    background-color: {t["danger_hover_bg"]};
    color: {t["danger"]};
    border-color: {t["danger_hover_bg"]};
}}
QPushButton[danger="true"]:pressed {{
    background-color: {t["danger"]};
    color: {t["accent_ink"]};
    border-color: {t["danger"]};
}}
QPushButton[nav="true"] {{
    background-color: transparent;
    color: {t["muted"]};
    border: none;
    border-radius: 7px;
    text-align: left;
    padding: 6px 10px;
    font-weight: 500;
}}
QPushButton[nav="true"]:hover {{
    background-color: {t["raised_hover"]};
    color: {t["ink"]};
}}
QPushButton[nav="true"][active="true"] {{
    background-color: {t["nav_active_bg"]};
    color: {t["nav_active_ink"]};
    font-weight: 600;
}}
QPushButton[segment="true"] {{
    background-color: transparent;
    color: {t["muted"]};
    border: 1px solid transparent;
    border-radius: 6px;
    padding: 4px 10px;
    min-height: 20px;
    font-weight: 500;
}}
QPushButton[segment="true"]:hover {{
    color: {t["ink"]};
}}
QPushButton[segment="true"][active="true"] {{
    background-color: {t["panel"]};
    color: {t["ink"]};
    border: 1px solid {t["line_subtle"]};
    font-weight: 600;
}}
QPushButton[pill="true"] {{
    background-color: {t["raised"]};
    color: {t["muted"]};
    border: 1px solid {t["line"]};
    border-radius: 13px;
    padding: 3px 11px;
    min-height: 20px;
    font-size: {small_px}px;
}}
QPushButton[pill="true"]:hover {{
    background-color: {t["raised_hover"]};
    color: {t["ink"]};
}}
QPushButton[pill="true"][active="true"] {{
    background-color: {t["accent"]};
    color: {t["accent_ink"]};
    border-color: {t["accent"]};
    font-weight: 600;
}}
QPushButton[compact="true"] {{
    padding: 3px 8px;
    min-height: 18px;
    font-size: {small_px}px;
}}
QPushButton[thumb="true"] {{
    background-color: {t["raised"]};
    border: 1.5px solid {t["line_subtle"]};
    border-radius: 7px;
    padding: 0px;
}}
QPushButton[thumb="true"]:hover {{
    border-color: {t["field_hover"]};
}}
QPushButton[thumb="true"][applied="true"] {{
    border-color: {t["success"]};
}}
QPushButton[thumb="true"][selected="true"] {{
    border: 2px solid {t["accent"]};
    background-color: {t["soft"]};
}}
QLabel#ThumbTag {{
    color: {t["muted"]};
    font-size: 11px;
    font-weight: 500;
    background: transparent;
}}
QLabel#ThumbTagApplied {{
    color: {t["success"]};
    font-size: 11px;
    font-weight: 700;
    background: transparent;
}}
QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QSpinBox, QDoubleSpinBox, QDateTimeEdit {{
    background-color: {t["field"]};
    color: {t["ink"]};
    border: 1px solid {t["field_border"]};
    border-radius: 7px;
    padding: 5px 9px;
    min-height: 20px;
    selection-background-color: {t["accent"]};
    selection-color: {t["accent_ink"]};
}}
QLineEdit:hover, QTextEdit:hover, QPlainTextEdit:hover, QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover {{
    border-color: {t["field_hover"]};
}}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {{
    border: 1.5px solid {t["accent"]};
}}
QComboBox {{
    padding: 4px 26px 4px 10px;
    min-height: 22px;
}}
QComboBox::drop-down {{
    subcontrol-origin: padding;
    subcontrol-position: center right;
    width: 24px;
    border: none;
    background: transparent;
}}
QComboBox::down-arrow {{
    image: url({ic["chevron_down"]});
    width: 12px;
    height: 12px;
}}
QComboBox QAbstractItemView {{
    background-color: {t["panel"]};
    border: 1px solid {t["line"]};
    border-radius: 8px;
    padding: 4px;
    outline: none;
    selection-background-color: {t["soft"]};
    selection-color: {t["ink"]};
}}
QSpinBox, QDoubleSpinBox {{
    padding: 4px 24px 4px 8px;
    min-height: 22px;
}}
QSpinBox::up-button, QDoubleSpinBox::up-button,
QSpinBox::down-button, QDoubleSpinBox::down-button {{
    subcontrol-origin: border;
    width: 20px;
    border: none;
    border-left: 1px solid {t["line_subtle"]};
    background-color: transparent;
}}
QSpinBox::up-button, QDoubleSpinBox::up-button {{
    subcontrol-position: top right;
    border-top-right-radius: 6px;
}}
QSpinBox::down-button, QDoubleSpinBox::down-button {{
    subcontrol-position: bottom right;
    border-bottom-right-radius: 6px;
}}
QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover,
QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover {{
    background-color: {t["raised_hover"]};
}}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{
    image: url({ic["spin_up"]});
    width: 10px;
    height: 10px;
}}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{
    image: url({ic["spin_down"]});
    width: 10px;
    height: 10px;
}}
QCheckBox {{
    spacing: 7px;
}}
QCheckBox::indicator {{
    width: 15px;
    height: 15px;
    border-radius: 4px;
    border: 1.5px solid {t["field_hover"]};
    background-color: {t["field"]};
}}
QCheckBox::indicator:hover {{
    border-color: {t["accent"]};
}}
QCheckBox::indicator:checked {{
    background-color: {t["accent"]};
    border-color: {t["accent"]};
    image: url({ic["check"]});
}}
QListWidget, QListView, QTableWidget {{
    background-color: {t["raised"]};
    border: 1px solid {t["line_subtle"]};
    border-radius: 8px;
    padding: 4px;
    outline: none;
}}
QListWidget::item {{
    padding: 5px 8px;
    border-radius: 6px;
    margin: 1px 0;
}}
QListWidget::item:hover {{
    background-color: {t["raised_hover"]};
}}
QListWidget::item:selected {{
    background-color: {t["soft"]};
    color: {t["ink"]};
}}
QProgressBar {{
    background-color: {t["raised"]};
    border: 1px solid {t["line_subtle"]};
    border-radius: 4px;
    height: 7px;
    text-align: center;
}}
QProgressBar::chunk {{
    background-color: {t["accent"]};
    border-radius: 4px;
}}
QSplitter::handle {{
    background-color: transparent;
    width: 6px;
    height: 6px;
}}
QSplitter::handle:hover {{
    background-color: {t["accent"]};
    border-radius: 3px;
}}
QTabWidget::pane {{
    border: 1px solid {t["line"]};
    border-radius: 8px;
    background-color: {t["panel"]};
}}
QTabBar::tab {{
    background-color: transparent;
    color: {t["muted"]};
    border: none;
    border-bottom: 2px solid transparent;
    padding: 6px 12px;
    margin-right: 4px;
}}
QTabBar::tab:hover {{
    color: {t["ink"]};
}}
QTabBar::tab:selected {{
    color: {t["accent"]};
    border-bottom: 2px solid {t["accent"]};
    font-weight: 600;
}}
QGroupBox {{
    border: 1px solid {t["line"]};
    border-radius: 9px;
    margin-top: 10px;
    padding-top: 10px;
    font-weight: 600;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 4px;
    color: {t["caption"]};
}}
QScrollBar:vertical {{
    background: transparent;
    width: 7px;
    margin: 2px 1px;
}}
QScrollBar::handle:vertical {{
    background: {t["line"]};
    border-radius: 3px;
    min-height: 28px;
}}
QScrollBar::handle:vertical:hover {{
    background: {t["field_hover"]};
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0px;
}}
QScrollBar:horizontal {{
    background: transparent;
    height: 7px;
    margin: 1px 2px;
}}
QScrollBar::handle:horizontal {{
    background: {t["line"]};
    border-radius: 3px;
    min-width: 28px;
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    width: 0px;
}}
"""


def apply_theme(qt_app: QApplication, theme_name: str = "linen", large_text: bool = False) -> None:
    global _ACTIVE_THEME
    _ACTIVE_THEME = theme_name if theme_name in THEMES else "linen"
    t = tokens(_ACTIVE_THEME)
    pal = QPalette()
    pal.setColor(QPalette.ColorRole.Window, QColor(t["canvas"]))
    pal.setColor(QPalette.ColorRole.WindowText, QColor(t["ink"]))
    pal.setColor(QPalette.ColorRole.Base, QColor(t["field"]))
    pal.setColor(QPalette.ColorRole.AlternateBase, QColor(t["panel"]))
    pal.setColor(QPalette.ColorRole.Text, QColor(t["ink"]))
    pal.setColor(QPalette.ColorRole.PlaceholderText, QColor(t["placeholder"]))
    pal.setColor(QPalette.ColorRole.Button, QColor(t["raised"]))
    pal.setColor(QPalette.ColorRole.ButtonText, QColor(t["ink"]))
    pal.setColor(QPalette.ColorRole.Highlight, QColor(t["accent"]))
    pal.setColor(QPalette.ColorRole.HighlightedText, QColor(t["accent_ink"]))
    qt_app.setPalette(pal)
    font = QFont("Segoe UI Variable Text")
    font.setFamilies(["SF Pro Text", "Segoe UI Variable Text", "PingFang SC", "Microsoft YaHei UI"])
    font.setPixelSize(16 if large_text else 14)
    qt_app.setFont(font)
    qt_app.setStyleSheet(build_qss(_ACTIVE_THEME, large_text))
