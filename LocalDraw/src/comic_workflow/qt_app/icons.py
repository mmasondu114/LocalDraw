"""Crisp monochrome QPainter vector icons & cached QSS control assets (zero emojis)."""

from __future__ import annotations

import math
import tempfile
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap

_PIXMAP_CACHE: dict[tuple[str, str, int, float], QPixmap] = {}
_QSS_ICON_CACHE: dict[str, dict[str, str]] = {}


def _draw_icon(p: QPainter, name: str, color: QColor) -> None:
    """Draw a clean 16x16 logical-grid geometric vector icon."""
    pen = QPen(color, 1.5)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)

    if name == "home":
        roof = QPainterPath(QPointF(2.5, 7.8))
        roof.lineTo(8.0, 2.8)
        roof.lineTo(13.5, 7.8)
        p.drawPath(roof)
        walls = QPainterPath(QPointF(4.2, 6.8))
        walls.lineTo(4.2, 13.2)
        walls.lineTo(11.8, 13.2)
        walls.lineTo(11.8, 6.8)
        p.drawPath(walls)
        door = QPainterPath(QPointF(6.8, 13.2))
        door.lineTo(6.8, 9.8)
        door.lineTo(9.2, 9.8)
        door.lineTo(9.2, 13.2)
        p.drawPath(door)
    elif name == "queue":
        p.drawEllipse(QPointF(8.0, 8.0), 5.5, 5.5)
        p.drawLine(QPointF(8.0, 4.8), QPointF(8.0, 8.0))
        p.drawLine(QPointF(8.0, 8.0), QPointF(10.5, 9.4))
    elif name == "spark":
        path = QPainterPath(QPointF(8.0, 2.2))
        path.quadTo(QPointF(8.4, 7.6), QPointF(13.8, 8.0))
        path.quadTo(QPointF(8.4, 8.4), QPointF(8.0, 13.8))
        path.quadTo(QPointF(7.6, 8.4), QPointF(2.2, 8.0))
        path.quadTo(QPointF(7.6, 7.6), QPointF(8.0, 2.2))
        p.drawPath(path)
    elif name == "storyboard":
        p.drawRoundedRect(QRectF(2.4, 2.6, 11.2, 10.8), 2.0, 2.0)
        p.drawLine(QPointF(2.4, 7.6), QPointF(13.6, 7.6))
        p.drawLine(QPointF(8.0, 7.6), QPointF(8.0, 13.4))
    elif name == "import":
        p.drawLine(QPointF(8.0, 2.5), QPointF(8.0, 9.8))
        head = QPainterPath(QPointF(5.2, 7.2))
        head.lineTo(8.0, 10.0)
        head.lineTo(10.8, 7.2)
        p.drawPath(head)
        tray = QPainterPath(QPointF(2.8, 10.5))
        tray.lineTo(2.8, 12.8)
        tray.lineTo(13.2, 12.8)
        tray.lineTo(13.2, 10.5)
        p.drawPath(tray)
    elif name == "script":
        p.drawRoundedRect(QRectF(3.2, 2.2, 9.6, 11.6), 1.6, 1.6)
        p.drawLine(QPointF(5.5, 5.6), QPointF(10.5, 5.6))
        p.drawLine(QPointF(5.5, 8.2), QPointF(10.5, 8.2))
        p.drawLine(QPointF(5.5, 10.8), QPointF(8.8, 10.8))
    elif name == "characters":
        p.drawEllipse(QPointF(8.0, 5.2), 2.5, 2.5)
        arc = QPainterPath(QPointF(3.2, 13.4))
        arc.quadTo(QPointF(3.6, 9.6), QPointF(8.0, 9.6))
        arc.quadTo(QPointF(12.4, 9.6), QPointF(12.8, 13.4))
        p.drawPath(arc)
    elif name == "projects" or name == "folder":
        path = QPainterPath(QPointF(2.4, 4.2))
        path.lineTo(6.4, 4.2)
        path.lineTo(7.8, 5.8)
        path.lineTo(13.6, 5.8)
        path.lineTo(13.6, 12.6)
        path.lineTo(2.4, 12.6)
        path.closeSubpath()
        p.drawPath(path)
    elif name == "package":
        box = QPainterPath(QPointF(8.0, 2.2))
        box.lineTo(13.2, 5.0)
        box.lineTo(13.2, 11.0)
        box.lineTo(8.0, 13.8)
        box.lineTo(2.8, 11.0)
        box.lineTo(2.8, 5.0)
        box.closeSubpath()
        p.drawPath(box)
        p.drawLine(QPointF(2.8, 5.0), QPointF(8.0, 7.8))
        p.drawLine(QPointF(13.2, 5.0), QPointF(8.0, 7.8))
        p.drawLine(QPointF(8.0, 7.8), QPointF(8.0, 13.8))
    elif name == "sliders":
        for y, cx in ((4.2, 5.8), (8.0, 10.4), (11.8, 6.6)):
            p.drawLine(QPointF(2.5, y), QPointF(cx - 1.5, y))
            p.drawLine(QPointF(cx + 1.5, y), QPointF(13.5, y))
            p.drawEllipse(QPointF(cx, y), 1.4, 1.4)
    elif name == "palette":
        p.drawEllipse(QPointF(8.0, 8.0), 5.5, 5.5)
        p.setBrush(color)
        p.setPen(Qt.PenStyle.NoPen)
        half = QPainterPath()
        half.moveTo(8.0, 2.5)
        half.arcTo(QRectF(2.5, 2.5, 11.0, 11.0), 90, 180)
        half.closeSubpath()
        p.drawPath(half)
    elif name == "refresh":
        p.drawArc(QRectF(2.8, 2.8, 10.4, 10.4), 35 * 16, 285 * 16)
        tip = QPainterPath(QPointF(10.6, 2.6))
        tip.lineTo(13.2, 4.4)
        tip.lineTo(10.4, 5.8)
        p.drawPath(tip)
    elif name == "play":
        tri = QPainterPath(QPointF(5.2, 3.6))
        tri.lineTo(12.2, 8.0)
        tri.lineTo(5.2, 12.4)
        tri.closeSubpath()
        p.drawPath(tri)
    elif name == "stop":
        p.drawRoundedRect(QRectF(4.2, 4.2, 7.6, 7.6), 1.5, 1.5)
    elif name == "wand":
        p.drawLine(QPointF(3.0, 13.0), QPointF(11.4, 4.6))
        p.drawLine(QPointF(11.8, 2.2), QPointF(11.8, 4.0))
        p.drawLine(QPointF(10.9, 3.1), QPointF(12.7, 3.1))
        p.drawLine(QPointF(5.0, 4.2), QPointF(5.0, 6.0))
        p.drawLine(QPointF(4.1, 5.1), QPointF(5.9, 5.1))
    elif name == "copy":
        p.drawRoundedRect(QRectF(5.4, 5.2, 7.6, 8.2), 1.4, 1.4)
        back = QPainterPath(QPointF(3.0, 10.4))
        back.lineTo(3.0, 3.0)
        back.lineTo(10.4, 3.0)
        p.drawPath(back)
    elif name == "paste":
        p.drawRoundedRect(QRectF(3.4, 3.8, 9.2, 9.8), 1.5, 1.5)
        p.drawRoundedRect(QRectF(5.8, 2.2, 4.4, 2.6), 1.0, 1.0)
    elif name == "download":
        p.drawLine(QPointF(8.0, 2.5), QPointF(8.0, 9.8))
        head = QPainterPath(QPointF(5.2, 7.2))
        head.lineTo(8.0, 10.0)
        head.lineTo(10.8, 7.2)
        p.drawPath(head)
        p.drawLine(QPointF(3.0, 13.0), QPointF(13.0, 13.0))
    elif name == "trash":
        p.drawLine(QPointF(3.0, 4.4), QPointF(13.0, 4.4))
        p.drawLine(QPointF(6.2, 2.6), QPointF(9.8, 2.6))
        body = QPainterPath(QPointF(4.4, 4.4))
        body.lineTo(5.2, 13.2)
        body.lineTo(10.8, 13.2)
        body.lineTo(11.6, 4.4)
        p.drawPath(body)
        p.drawLine(QPointF(6.8, 6.8), QPointF(7.0, 11.0))
        p.drawLine(QPointF(9.2, 6.8), QPointF(9.0, 11.0))
    elif name == "plus":
        p.drawLine(QPointF(8.0, 3.6), QPointF(8.0, 12.4))
        p.drawLine(QPointF(3.6, 8.0), QPointF(12.4, 8.0))
    elif name == "close":
        p.drawLine(QPointF(4.4, 4.4), QPointF(11.6, 11.6))
        p.drawLine(QPointF(11.6, 4.4), QPointF(4.4, 11.6))
    elif name == "chevron-left":
        path = QPainterPath(QPointF(10.2, 3.6))
        path.lineTo(5.6, 8.0)
        path.lineTo(10.2, 12.4)
        p.drawPath(path)
    elif name == "chevron-right":
        path = QPainterPath(QPointF(5.8, 3.6))
        path.lineTo(10.4, 8.0)
        path.lineTo(5.8, 12.4)
        p.drawPath(path)
    elif name == "chevron-down":
        path = QPainterPath(QPointF(3.8, 6.0))
        path.lineTo(8.0, 10.4)
        path.lineTo(12.2, 6.0)
        p.drawPath(path)
    elif name == "chevron-up":
        path = QPainterPath(QPointF(3.8, 10.0))
        path.lineTo(8.0, 5.6)
        path.lineTo(12.2, 10.0)
        p.drawPath(path)
    elif name == "spin-up":
        pen.setWidthF(1.7)
        p.setPen(pen)
        path = QPainterPath(QPointF(4.2, 10.2))
        path.lineTo(8.0, 6.0)
        path.lineTo(11.8, 10.2)
        p.drawPath(path)
    elif name == "spin-down":
        pen.setWidthF(1.7)
        p.setPen(pen)
        path = QPainterPath(QPointF(4.2, 5.8))
        path.lineTo(8.0, 10.0)
        path.lineTo(11.8, 5.8)
        p.drawPath(path)
    elif name == "check":
        pen.setWidthF(1.9)
        p.setPen(pen)
        path = QPainterPath(QPointF(3.4, 8.4))
        path.lineTo(6.6, 11.4)
        path.lineTo(12.6, 4.8)
        p.drawPath(path)
    elif name == "dot":
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color)
        p.drawEllipse(QPointF(8.0, 8.0), 3.0, 3.0)
    elif name == "circle":
        p.drawEllipse(QPointF(8.0, 8.0), 4.2, 4.2)
    elif name == "alert":
        tri = QPainterPath(QPointF(8.0, 2.5))
        tri.lineTo(13.6, 13.0)
        tri.lineTo(2.4, 13.0)
        tri.closeSubpath()
        p.drawPath(tri)
        p.drawLine(QPointF(8.0, 6.2), QPointF(8.0, 9.2))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color)
        p.drawEllipse(QPointF(8.0, 11.2), 0.9, 0.9)
    elif name == "layout":
        p.drawRoundedRect(QRectF(2.4, 2.4, 11.2, 11.2), 1.8, 1.8)
        p.drawLine(QPointF(7.2, 2.4), QPointF(7.2, 13.6))
        p.drawLine(QPointF(7.2, 8.2), QPointF(13.6, 8.2))
    elif name == "export":
        tray = QPainterPath(QPointF(2.8, 8.8))
        tray.lineTo(2.8, 13.0)
        tray.lineTo(13.2, 13.0)
        tray.lineTo(13.2, 8.8)
        p.drawPath(tray)
        p.drawLine(QPointF(8.0, 9.8), QPointF(8.0, 2.8))
        head = QPainterPath(QPointF(5.2, 5.4))
        head.lineTo(8.0, 2.6)
        head.lineTo(10.8, 5.4)
        p.drawPath(head)
    elif name == "image":
        p.drawRoundedRect(QRectF(2.2, 3.0, 11.6, 10.0), 1.8, 1.8)
        p.drawEllipse(QPointF(5.6, 6.2), 1.1, 1.1)
        path = QPainterPath(QPointF(3.2, 11.6))
        path.lineTo(6.8, 8.0)
        path.lineTo(9.4, 10.4)
        path.lineTo(11.2, 8.8)
        path.lineTo(13.0, 11.6)
        p.drawPath(path)
    elif name == "merge":
        p.drawEllipse(QPointF(4.4, 4.4), 1.8, 1.8)
        p.drawEllipse(QPointF(4.4, 11.6), 1.8, 1.8)
        p.drawEllipse(QPointF(11.8, 8.0), 1.8, 1.8)
        p.drawLine(QPointF(6.2, 4.8), QPointF(10.0, 7.4))
        p.drawLine(QPointF(6.2, 11.2), QPointF(10.0, 8.6))
    elif name in ("star", "star-filled"):
        star = QPainterPath()
        cx, cy = 8.0, 8.2
        r_out, r_in = 5.6, 2.45
        for i in range(10):
            angle = math.radians(-90.0 + i * 36.0)
            r = r_out if i % 2 == 0 else r_in
            pt = QPointF(cx + r * math.cos(angle), cy + r * math.sin(angle))
            if i == 0:
                star.moveTo(pt)
            else:
                star.lineTo(pt)
        star.closeSubpath()
        if name == "star-filled":
            p.setBrush(color)
            pen.setWidthF(1.1)
            p.setPen(pen)
        else:
            pen.setWidthF(1.35)
            p.setPen(pen)
        p.drawPath(star)


def icon_pixmap(name: str, color: str, size: int = 16, dpr: float = 2.0) -> QPixmap:
    key = (name, color.lower(), int(size), float(dpr))
    cached = _PIXMAP_CACHE.get(key)
    if cached is not None:
        return cached
    px_size = max(8, int(round(size * dpr)))
    pm = QPixmap(px_size, px_size)
    pm.fill(Qt.GlobalColor.transparent)
    pm.setDevicePixelRatio(dpr)
    painter = QPainter(pm)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    scale = size / 16.0
    painter.scale(scale, scale)
    _draw_icon(painter, name, QColor(color))
    painter.end()
    _PIXMAP_CACHE[key] = pm
    return pm


def studio_icon(name: str, color: str | None = None, size: int = 16) -> QIcon:
    if color is None:
        from .theme import tokens

        color = tokens()["muted"]
    return QIcon(icon_pixmap(name, color, size=size))


def ensure_qss_icons(theme_name: str, theme_tokens: dict[str, str]) -> dict[str, str]:
    """Render crisp @2x control icons to temp PNGs for Qt QSS url(...) rules."""
    cache_key = f"{theme_name}:{theme_tokens['muted']}:{theme_tokens['accent_ink']}"
    if cache_key in _QSS_ICON_CACHE:
        return _QSS_ICON_CACHE[cache_key]
    out_dir = Path(tempfile.gettempdir()) / "localdraw_qss_icons" / theme_name
    out_dir.mkdir(parents=True, exist_ok=True)
    specs = {
        "chevron_down": ("chevron-down", theme_tokens["muted"], 12),
        "chevron_up": ("chevron-up", theme_tokens["muted"], 12),
        "spin_up": ("spin-up", theme_tokens["muted"], 10),
        "spin_down": ("spin-down", theme_tokens["muted"], 10),
        "check": ("check", theme_tokens["accent_ink"], 11),
    }
    paths: dict[str, str] = {}
    for key, (icon_name, col, sz) in specs.items():
        target = out_dir / f"{key}.png"
        pm = icon_pixmap(icon_name, col, size=sz, dpr=2.0)
        pm.save(str(target), "PNG")
        paths[key] = target.resolve().as_posix()
    _QSS_ICON_CACHE[cache_key] = paths
    return paths
