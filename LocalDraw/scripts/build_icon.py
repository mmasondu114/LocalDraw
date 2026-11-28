"""Generate crisp multi-resolution Warm Terracotta Studio LocalDraw.ico."""

from __future__ import annotations

import math
from pathlib import Path

from PIL import Image
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QGuiApplication,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QRadialGradient,
)


def _squircle_path(rect: QRectF, radius_ratio: float = 0.24) -> QPainterPath:
    """Smooth continuous-curve rounded squircle (macOS / Win11 studio silhouette)."""
    x, y, w, h = rect.x(), rect.y(), rect.width(), rect.height()
    r = min(w, h) * radius_ratio
    k = r * 0.55228475
    path = QPainterPath()
    path.moveTo(x + r, y)
    path.lineTo(x + w - r, y)
    path.cubicTo(x + w - r + k, y, x + w, y + r - k, x + w, y + r)
    path.lineTo(x + w, y + h - r)
    path.cubicTo(x + w, y + h - r + k, x + w - r + k, y + h, x + w - r, y + h)
    path.lineTo(x + r, y + h)
    path.cubicTo(x + r - k, y + h, x, y + h - r + k, x, y + h - r)
    path.lineTo(x, y + r)
    path.cubicTo(x, y + r - k, x + r - k, y, x + r, y)
    path.closeSubpath()
    return path


def render_icon_image(size: int) -> Image.Image:
    """Render LocalDraw icon at 4x supersampling, tuned for crispness down to 16px."""
    hi = max(256, size * 4)
    qimg = QImage(hi, hi, QImage.Format.Format_RGBA8888)
    qimg.fill(Qt.GlobalColor.transparent)

    p = QPainter(qimg)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

    # Scale logical 100x100 design grid into hi x hi
    s = hi / 100.0
    p.scale(s, s)

    # Outer margin: smaller margin for tiny taskbar icons so it fills cleanly
    margin = 4.5 if size <= 32 else 6.0
    box = QRectF(margin, margin, 100.0 - 2 * margin, 100.0 - 2 * margin)
    sq = _squircle_path(box, 0.25)

    # Subtle ambient shadow for larger sizes
    if size >= 48:
        shadow_box = QRectF(box.x(), box.y() + 2.2, box.width(), box.height())
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(38, 18, 10, 42))
        p.drawPath(_squircle_path(shadow_box, 0.25))

    # Rich warm terracotta studio gradient (matches Warm Paper & Terracotta theme)
    bg_grad = QLinearGradient(box.topLeft(), box.bottomRight())
    bg_grad.setColorAt(0.0, QColor("#C46D45"))
    bg_grad.setColorAt(0.52, QColor("#A24E2D"))
    bg_grad.setColorAt(1.0, QColor("#7B371E"))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(bg_grad)
    p.drawPath(sq)

    # Subtle top-left warm studio sheen
    sheen = QRadialGradient(QPointF(box.left() + box.width() * 0.28, box.top() + box.height() * 0.18), box.width() * 0.78)
    sheen.setColorAt(0.0, QColor(255, 236, 214, 58))
    sheen.setColorAt(0.55, QColor(255, 224, 196, 12))
    sheen.setColorAt(1.0, QColor(255, 224, 196, 0))
    p.setBrush(sheen)
    p.drawPath(sq)

    # Crisp subtle inner rim highlight
    rim_pen = QPen(QColor(255, 244, 230, 56), 1.4 if size >= 32 else 2.0)
    p.setPen(rim_pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    inner_box = box.adjusted(0.8, 0.8, -0.8, -0.8)
    p.drawPath(_squircle_path(inner_box, 0.24))

    # Iconic asymmetric manga-studio panel emblem in warm ivory (#FFFBF5)
    # Slightly thicker strokes at small sizes so 16px/24px stay razor sharp
    stroke = 6.2 if size <= 24 else (5.4 if size <= 32 else 4.8)
    pad = 24.5 if size <= 24 else 26.0
    frame = QRectF(pad, pad, 100.0 - 2 * pad, 100.0 - 2 * pad)
    div_x = frame.left() + frame.width() * 0.48
    div_y = frame.top() + frame.height() * 0.54

    # Soft dark warm shadow under emblem for subtle depth
    if size >= 32:
        sh_pen = QPen(QColor(56, 22, 10, 62), stroke)
        sh_pen.setCapStyle(Qt.PenCapStyle.FlatCap)
        sh_pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(sh_pen)
        sh_frame = frame.translated(0, 1.5)
        p.drawRoundedRect(sh_frame, 6.5, 6.5)
        p.drawLine(QPointF(div_x, sh_frame.top() + stroke * 0.3), QPointF(div_x, sh_frame.bottom() - stroke * 0.3))
        p.drawLine(QPointF(sh_frame.left() + stroke * 0.3, div_y + 1.5), QPointF(div_x, div_y + 1.5))

    # Warm translucent fill inside the right hero panel (adds visual richness & atelier identity)
    right_panel = QRectF(
        div_x + stroke * 0.45,
        frame.top() + stroke * 0.45,
        frame.right() - div_x - stroke * 0.9,
        frame.height() - stroke * 0.9,
    )
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(255, 248, 238, 44))
    p.drawRoundedRect(right_panel, 3.2, 3.2)

    # Crisp ivory comic-panel strokes
    fg_pen = QPen(QColor("#FFFBF5"), stroke)
    fg_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    fg_pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(fg_pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRoundedRect(frame, 6.5, 6.5)
    p.drawLine(QPointF(div_x, frame.top()), QPointF(div_x, frame.bottom()))
    p.drawLine(QPointF(frame.left(), div_y), QPointF(div_x, div_y))

    # Delicate champagne-gold 4-point spark inside the right hero panel (signature creative accent)
    if size >= 24:
        cx = div_x + (frame.right() - div_x) * 0.52
        cy = frame.top() + frame.height() * 0.48
        r_spark = 8.2 if size <= 32 else 7.6
        spark = QPainterPath(QPointF(cx, cy - r_spark))
        spark.quadTo(QPointF(cx + r_spark * 0.16, cy - r_spark * 0.16), QPointF(cx + r_spark, cy))
        spark.quadTo(QPointF(cx + r_spark * 0.16, cy + r_spark * 0.16), QPointF(cx, cy + r_spark))
        spark.quadTo(QPointF(cx - r_spark * 0.16, cy + r_spark * 0.16), QPointF(cx - r_spark, cy))
        spark.quadTo(QPointF(cx - r_spark * 0.16, cy - r_spark * 0.16), QPointF(cx, cy - r_spark))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#FBE3C2"))
        p.drawPath(spark)

    p.end()

    # Convert QImage RGBA8888 -> PIL Image and Lanczos-downsample cleanly to target size
    ptr = qimg.constBits()
    pil = Image.frombytes("RGBA", (hi, hi), bytes(ptr))
    if hi != size:
        pil = pil.resize((size, size), Image.Resampling.LANCZOS)
    return pil


def build_ico(output: Path, preview_path: Path | None = None) -> None:
    app = QGuiApplication.instance() or QGuiApplication(["build-icon"])
    sizes = [256, 128, 64, 48, 32, 24, 16]
    imgs = [render_icon_image(sz) for sz in sizes]
    output.parent.mkdir(parents=True, exist_ok=True)
    # Pillow saves all provided append_images frames cleanly into a multi-res Windows ICO
    imgs[0].save(
        str(output),
        format="ICO",
        append_images=imgs[1:],
    )
    if preview_path is not None:
        # Build visual preview sheet on light and dark backgrounds
        sheet = Image.new("RGBA", (620, 320), (244, 241, 236, 255))
        # Dark right strip to simulate dark taskbar
        dark_band = Image.new("RGBA", (240, 320), (32, 32, 32, 255))
        sheet.paste(dark_band, (380, 0))
        sheet.alpha_composite(imgs[0], (24, 32))  # 256px on warm paper
        y = 32
        for sz, im in zip([64, 48, 32, 24, 16], imgs[2:]):
            sheet.alpha_composite(im, (304, y))
            sheet.alpha_composite(im, (420, y))
            y += sz + 14
        # Also show 128px on dark background
        sheet.alpha_composite(imgs[1], (476, 96))
        preview_path.parent.mkdir(parents=True, exist_ok=True)
        sheet.save(str(preview_path))


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    for name in ("LocalDraw.ico", "LocalDraw-app.ico"):
        out_ico = root / "scripts/windows" / name
        build_ico(out_ico)
        print("Generated:", out_ico, out_ico.stat().st_size, "bytes")
