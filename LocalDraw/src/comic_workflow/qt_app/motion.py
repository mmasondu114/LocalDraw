"""Global interactive UI feedback and smooth animations for LocalDraw Qt6 widgets and popups."""

from __future__ import annotations

import atexit
import weakref

from PySide6.QtCore import (
    QAbstractAnimation,
    QEasingCurve,
    QEvent,
    QObject,
    QParallelAnimationGroup,
    QPoint,
    QPointF,
    QPropertyAnimation,
    QRectF,
    Qt,
    QVariantAnimation,
)
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QRadialGradient
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFrame,
    QGraphicsOpacityEffect,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTextEdit,
    QWidget,
)

from .theme import current_theme, tokens

_INTERACTIVE_FRAME_NAMES = {"HomeCard", "HeroCard", "StatusPill", "ListRow"}
_ELEVATED_CARD_NAMES = {"HomeCard", "HeroCard"}
_FOCUS_WIDGET_TYPES = (QLineEdit, QPlainTextEdit, QTextEdit, QComboBox, QSpinBox, QDoubleSpinBox)
_TRACKED_ANIMS: weakref.WeakSet[QAbstractAnimation] = weakref.WeakSet()


def _track_anim(anim: QAbstractAnimation) -> None:
    _TRACKED_ANIMS.add(anim)


def stop_all_animations() -> None:
    """Stop every tracked animation cleanly before Qt tears down."""
    for anim in list(_TRACKED_ANIMS):
        try:
            anim.stop()
        except RuntimeError:
            pass
    _TRACKED_ANIMS.clear()


def _corner_radius(widget: QWidget) -> float:
    name = widget.objectName()
    if name in ("HomeCard", "HeroCard"):
        return 12.0
    if name == "StatusPill" or bool(widget.property("pill")):
        return min(14.0, widget.height() / 2.0)
    if name == "ListRow":
        return 6.0
    if bool(widget.property("ghost")) or bool(widget.property("segment")) or bool(widget.property("danger")):
        return 6.0
    if bool(widget.property("thumb")):
        return 7.0
    return 7.0


class _InteractiveOverlay(QWidget):
    """Lightweight transparent overlay rendering hover sheen, press depth, and click ripple."""

    def __init__(self, target: QWidget):
        super().__init__(target)
        self._target = target
        self.setObjectName("_MotionOverlay")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setGeometry(target.rect())
        self.raise_()
        self.hide()

        self._hover_t = 0.0
        self._press_t = 0.0
        self._ripple_t = 1.0
        self._ripple_origin = QPointF(0.0, 0.0)
        self._ripple_active = False

        self._hover_anim = QVariantAnimation(self)
        self._hover_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._hover_anim.valueChanged.connect(self._on_hover_val)
        self._hover_anim.finished.connect(self._sync_visibility)
        _track_anim(self._hover_anim)

        self._press_anim = QVariantAnimation(self)
        self._press_anim.setEasingCurve(QEasingCurve.Type.OutQuad)
        self._press_anim.valueChanged.connect(self._on_press_val)
        self._press_anim.finished.connect(self._sync_visibility)
        _track_anim(self._press_anim)

        self._ripple_anim = QVariantAnimation(self)
        self._ripple_anim.setDuration(290)
        self._ripple_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._ripple_anim.valueChanged.connect(self._on_ripple_val)
        self._ripple_anim.finished.connect(self._on_ripple_done)
        _track_anim(self._ripple_anim)

    def sync_geometry(self) -> None:
        if self._target is not None:
            r = self._target.rect()
            if self.geometry() != r:
                self.setGeometry(r)
            if self.isVisible():
                self.raise_()

    def _ensure_shown(self) -> None:
        self.sync_geometry()
        if not self.isVisible():
            self.show()
            self.raise_()

    def _sync_visibility(self) -> None:
        if self._hover_t <= 0.005 and self._press_t <= 0.005 and not self._ripple_active:
            if self.isVisible():
                self.hide()

    def _on_hover_val(self, val: float) -> None:
        self._hover_t = float(val)
        self.update()

    def _on_press_val(self, val: float) -> None:
        self._press_t = float(val)
        self.update()

    def _on_ripple_val(self, val: float) -> None:
        self._ripple_t = float(val)
        self.update()

    def _on_ripple_done(self) -> None:
        self._ripple_active = False
        self._ripple_t = 1.0
        self.update()
        self._sync_visibility()

    def set_hovered(self, hovered: bool) -> None:
        if hovered and not self._target.isEnabled():
            return
        target_val = 1.0 if hovered else 0.0
        if hovered:
            self._ensure_shown()
        self._hover_anim.stop()
        self._hover_anim.setDuration(145 if hovered else 185)
        self._hover_anim.setStartValue(self._hover_t)
        self._hover_anim.setEndValue(target_val)
        self._hover_anim.start()

    def set_pressed(self, pressed: bool, pos: QPoint | QPointF | None = None) -> None:
        if pressed and not self._target.isEnabled():
            return
        if pressed:
            self._ensure_shown()
            if pos is not None:
                self._ripple_origin = QPointF(pos)
            else:
                self._ripple_origin = QPointF(self.width() * 0.5, self.height() * 0.5)
            self._ripple_active = True
            self._ripple_t = 0.0
            self._ripple_anim.stop()
            self._ripple_anim.setStartValue(0.0)
            self._ripple_anim.setEndValue(1.0)
            self._ripple_anim.start()
        self._press_anim.stop()
        self._press_anim.setDuration(75 if pressed else 155)
        self._press_anim.setStartValue(self._press_t)
        self._press_anim.setEndValue(1.0 if pressed else 0.0)
        self._press_anim.start()

    def reset_immediately(self) -> None:
        self._hover_anim.stop()
        self._press_anim.stop()
        self._ripple_anim.stop()
        self._hover_t = 0.0
        self._press_t = 0.0
        self._ripple_t = 1.0
        self._ripple_active = False
        if self.isVisible():
            self.hide()

    def paintEvent(self, event) -> None:
        w, h = self.width(), self.height()
        if w <= 2 or h <= 2:
            return
        if self._hover_t <= 0.005 and self._press_t <= 0.005 and not self._ripple_active:
            return

        t = tokens(current_theme())
        is_primary = bool(self._target.property("primary")) or (
            bool(self._target.property("pill")) and bool(self._target.property("active"))
        )
        is_danger = bool(self._target.property("danger"))
        is_nav = bool(self._target.property("nav"))
        is_thumb = bool(self._target.property("thumb"))
        is_card = self._target.objectName() in _ELEVATED_CARD_NAMES
        radius = _corner_radius(self._target)

        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(0.5, 0.5, max(1.0, w - 1.0), max(1.0, h - 1.0))
        clip = QPainterPath()
        clip.addRoundedRect(rect, radius, radius)
        p.setClipPath(clip)

        accent = QColor(t["danger"] if is_danger else t["accent"])

        # 1. Soft hover sheen + subtle border accent glow
        if self._hover_t > 0.005:
            if is_primary:
                sheen = QColor(255, 255, 255, int(round(26 * self._hover_t)))
                p.fillRect(rect, sheen)
                top_glow = QPen(QColor(255, 255, 255, int(round(58 * self._hover_t))), 1.0)
                p.setPen(top_glow)
                p.drawLine(QPointF(radius, 1.0), QPointF(max(radius, w - radius), 1.0))
            elif is_card:
                wash = QColor(accent)
                wash.setAlpha(int(round(12 * self._hover_t)))
                p.fillRect(rect, wash)
                top_sheen = QPen(QColor(255, 255, 255, int(round(85 * self._hover_t))), 1.2)
                p.setPen(top_sheen)
                p.drawLine(QPointF(radius, 1.5), QPointF(max(radius, w - radius), 1.5))
                border = QColor(accent)
                border.setAlpha(int(round(165 * self._hover_t)))
                p.setPen(QPen(border, 1.6))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRoundedRect(rect.adjusted(0.6, 0.6, -0.6, -0.6), radius, radius)
            elif is_thumb:
                sheen = QColor(255, 255, 255, int(round(22 * self._hover_t)))
                p.fillRect(rect, sheen)
                border = QColor(accent)
                border.setAlpha(int(round(150 * self._hover_t)))
                p.setPen(QPen(border, 1.6))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRoundedRect(rect.adjusted(0.6, 0.6, -0.6, -0.6), radius, radius)
            else:
                wash = QColor(accent)
                wash.setAlpha(int(round((22 if is_danger else 14) * self._hover_t)))
                p.fillRect(rect, wash)
                border = QColor(accent)
                border.setAlpha(int(round((95 if is_danger else 68) * self._hover_t)))
                p.setPen(QPen(border, 1.0))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRoundedRect(rect.adjusted(0.25, 0.25, -0.25, -0.25), radius, radius)

            # Sidebar nav button animated vertical accent pill on left edge
            if is_nav:
                bar_h = max(6.0, (h * 0.54) * self._hover_t)
                bar_y = (h - bar_h) * 0.5
                bar_col = QColor(accent)
                bar_col.setAlpha(int(round(200 * self._hover_t)))
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(bar_col)
                p.drawRoundedRect(QRectF(2.0, bar_y, 3.0, bar_h), 1.5, 1.5)

        # 2. Tactile press depth shade
        if self._press_t > 0.005:
            press_col = QColor(0, 0, 0, int(round((34 if is_primary else 22) * self._press_t)))
            p.fillRect(rect, press_col)

        # 3. Radial click ripple clipped to rounded rect
        if self._ripple_active:
            max_r = max(w, h) * 1.15
            cur_r = max(4.0, max_r * self._ripple_t)
            fade = max(0.0, 1.0 - self._ripple_t)
            base_col = QColor(255, 255, 255) if is_primary else QColor(accent)
            peak_alpha = 72 if is_primary else (52 if is_danger else 44)
            center_col = QColor(base_col)
            center_col.setAlpha(int(round(peak_alpha * fade)))
            edge_col = QColor(base_col)
            edge_col.setAlpha(0)
            grad = QRadialGradient(self._ripple_origin, cur_r)
            grad.setColorAt(0.0, center_col)
            grad.setColorAt(0.75, center_col)
            grad.setColorAt(1.0, edge_col)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(grad)
            p.drawEllipse(self._ripple_origin, cur_r, cur_r)

        p.end()


class _FocusRingOverlay(QWidget):
    """Subtle animated accent focus halo for text inputs, combos, and spinboxes."""

    def __init__(self, target: QWidget):
        super().__init__(target)
        self._target = target
        self.setObjectName("_FocusOverlay")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setGeometry(target.rect())
        self.hide()

        self._t = 0.0
        self._anim = QVariantAnimation(self)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._on_val)
        self._anim.finished.connect(self._sync_vis)
        _track_anim(self._anim)

    def sync_geometry(self) -> None:
        if self._target is not None:
            r = self._target.rect()
            if self.geometry() != r:
                self.setGeometry(r)
            if self.isVisible():
                self.raise_()

    def _on_val(self, val: float) -> None:
        self._t = float(val)
        self.update()

    def _sync_vis(self) -> None:
        if self._t <= 0.005 and self.isVisible():
            self.hide()

    def set_focused(self, focused: bool) -> None:
        if focused:
            self.sync_geometry()
            self.show()
            self.raise_()
        self._anim.stop()
        self._anim.setDuration(150 if focused else 160)
        self._anim.setStartValue(self._t)
        self._anim.setEndValue(1.0 if focused else 0.0)
        self._anim.start()

    def reset_immediately(self) -> None:
        self._anim.stop()
        self._t = 0.0
        if self.isVisible():
            self.hide()

    def paintEvent(self, event) -> None:
        if self._t <= 0.005 or self.width() <= 4 or self.height() <= 4:
            return
        t = tokens(current_theme())
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        glow = QColor(t["accent"])
        glow.setAlpha(int(round(76 * self._t)))
        pen = QPen(glow, 2.0)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(QRectF(1.0, 1.0, self.width() - 2.0, self.height() - 2.0), 7.0, 7.0)
        p.end()


def fade_widget_in(widget: QWidget | None, *, duration: int = 165) -> None:
    """Play a clean one-shot opacity fade-in and strip the effect immediately on finish."""
    if widget is None or not widget.isVisible():
        return
    prev = widget.property("_ld_fade_anim")
    if isinstance(prev, QPropertyAnimation):
        prev.stop()
    eff = widget.graphicsEffect()
    if not isinstance(eff, QGraphicsOpacityEffect):
        eff = QGraphicsOpacityEffect(widget)
        widget.setGraphicsEffect(eff)
    eff.setOpacity(0.0)
    anim = QPropertyAnimation(eff, b"opacity", widget)
    anim.setDuration(duration)
    anim.setStartValue(0.0)
    anim.setEndValue(1.0)
    anim.setEasingCurve(QEasingCurve.Type.OutCubic)

    def _cleanup() -> None:
        try:
            if widget.graphicsEffect() is eff:
                widget.setGraphicsEffect(None)
            widget.setProperty("_ld_fade_anim", None)
        except RuntimeError:
            pass

    anim.finished.connect(_cleanup)
    widget.setProperty("_ld_fade_anim", anim)
    _track_anim(anim)
    anim.start()


def animate_dialog_enter(dialog: QDialog) -> None:
    """Smoothly fade and glide a dialog into view (no jarring instant snap)."""
    if dialog.property("_ld_skip_dialog_anim"):
        return
    prev = dialog.property("_ld_dlg_anim")
    if isinstance(prev, QParallelAnimationGroup):
        prev.stop()

    # Clamp & center non-fullscreen dialogs on first show if not already clamped
    is_lightbox = dialog.objectName() == "ImageLightbox" or dialog.isMaximized() or dialog.isFullScreen()
    if not is_lightbox and not isinstance(dialog, QMessageBox) and not dialog.property("_ld_fitted"):
        dialog.setProperty("_ld_fitted", True)
        screen = dialog.screen()
        if screen is not None:
            avail = screen.availableGeometry()
            w = min(dialog.width(), int(avail.width() * 0.94))
            h = min(dialog.height(), int(avail.height() * 0.90))
            if (w, h) != (dialog.width(), dialog.height()):
                dialog.resize(w, h)
                geo = dialog.frameGeometry()
                geo.moveCenter(avail.center())
                dialog.move(geo.topLeft())

    group = QParallelAnimationGroup(dialog)
    dialog.setWindowOpacity(0.0)

    op = QPropertyAnimation(dialog, b"windowOpacity", group)
    op.setDuration(185 if is_lightbox else 200)
    op.setStartValue(0.0)
    op.setEndValue(1.0)
    op.setEasingCurve(QEasingCurve.Type.OutCubic)
    group.addAnimation(op)

    if not is_lightbox:
        end_pos = dialog.property("_ld_dlg_target_pos")
        if not isinstance(end_pos, QPoint):
            end_pos = dialog.pos()
        dialog.setProperty("_ld_dlg_target_pos", end_pos)
        start_pos = QPoint(end_pos.x(), end_pos.y() + 14)
        dialog.move(start_pos)
        slide = QPropertyAnimation(dialog, b"pos", group)
        slide.setDuration(215)
        slide.setStartValue(start_pos)
        slide.setEndValue(end_pos)
        slide.setEasingCurve(QEasingCurve.Type.OutCubic)
        group.addAnimation(slide)

    def _done() -> None:
        try:
            dialog.setWindowOpacity(1.0)
            dialog.setProperty("_ld_dlg_anim", None)
            dialog.setProperty("_ld_dlg_target_pos", None)
        except RuntimeError:
            pass

    group.finished.connect(_done)
    dialog.setProperty("_ld_dlg_anim", group)
    _track_anim(group)
    group.start()


def _finish_dialog_anim_on_hide(dialog: QDialog) -> None:
    prev = dialog.property("_ld_dlg_anim")
    if isinstance(prev, QParallelAnimationGroup):
        prev.stop()
    target_pos = dialog.property("_ld_dlg_target_pos")
    if isinstance(target_pos, QPoint):
        dialog.move(target_pos)
    dialog.setWindowOpacity(1.0)
    dialog.setProperty("_ld_dlg_anim", None)
    dialog.setProperty("_ld_dlg_target_pos", None)


def animate_popup_fade(popup: QWidget, *, duration: int = 135) -> None:
    """Gentle fade-in for combo dropdown menus and lightweight popups."""
    prev = popup.property("_ld_popup_anim")
    if isinstance(prev, QPropertyAnimation):
        prev.stop()
    popup.setWindowOpacity(0.0)
    anim = QPropertyAnimation(popup, b"windowOpacity", popup)
    anim.setDuration(duration)
    anim.setStartValue(0.0)
    anim.setEndValue(1.0)
    anim.setEasingCurve(QEasingCurve.Type.OutCubic)

    def _done() -> None:
        try:
            popup.setWindowOpacity(1.0)
            popup.setProperty("_ld_popup_anim", None)
        except RuntimeError:
            pass

    anim.finished.connect(_done)
    popup.setProperty("_ld_popup_anim", anim)
    _track_anim(anim)
    anim.start()


class MotionEngine(QObject):
    """Application-wide Qt event filter adding tactile animations to all buttons, cards, inputs, and dialogs."""

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._active = True

    def detach(self) -> None:
        self._active = False

    def _overlay_for(self, widget: QWidget, *, create: bool = True) -> _InteractiveOverlay | None:
        ov = widget.findChild(_InteractiveOverlay, "_MotionOverlay", Qt.FindChildOption.FindDirectChildrenOnly)
        if ov is None and create:
            ov = _InteractiveOverlay(widget)
        return ov

    def _focus_for(self, widget: QWidget, *, create: bool = True) -> _FocusRingOverlay | None:
        ov = widget.findChild(_FocusRingOverlay, "_FocusOverlay", Qt.FindChildOption.FindDirectChildrenOnly)
        if ov is None and create:
            ov = _FocusRingOverlay(widget)
        return ov

    @staticmethod
    def _is_interactive_button(obj: object) -> bool:
        return isinstance(obj, QPushButton) and not bool(obj.property("_ld_no_motion"))

    @staticmethod
    def _is_interactive_frame(obj: object) -> bool:
        return isinstance(obj, QFrame) and obj.objectName() in _INTERACTIVE_FRAME_NAMES

    @staticmethod
    def _sync_cursor(widget: QWidget) -> None:
        if widget.isEnabled():
            if widget.cursor().shape() != Qt.CursorShape.PointingHandCursor:
                widget.setCursor(Qt.CursorShape.PointingHandCursor)
        else:
            widget.unsetCursor()

    def eventFilter(self, obj, event):  # noqa: N802 (Qt API)
        if not self._active or QApplication.closingDown():
            return False
        etype = event.type()

        # 1. Dialog & Popup smooth entrances and safe hide cleanup
        if etype == QEvent.Type.Hide:
            if isinstance(obj, QDialog):
                _finish_dialog_anim_on_hide(obj)
                return False
            if isinstance(obj, QWidget):
                popup_anim = obj.property("_ld_popup_anim")
                if isinstance(popup_anim, QPropertyAnimation):
                    popup_anim.stop()
                    obj.setWindowOpacity(1.0)
                    obj.setProperty("_ld_popup_anim", None)
                fade_anim = obj.property("_ld_fade_anim")
                if isinstance(fade_anim, QPropertyAnimation):
                    fade_anim.stop()
                    if isinstance(obj.graphicsEffect(), QGraphicsOpacityEffect):
                        obj.setGraphicsEffect(None)
                    obj.setProperty("_ld_fade_anim", None)

        if etype == QEvent.Type.Show:
            if isinstance(obj, QDialog):
                animate_dialog_enter(obj)
                return False
            if isinstance(obj, QWidget) and obj.metaObject().className() == "QComboBoxPrivateContainer":
                animate_popup_fade(obj)
                return False
            if self._is_interactive_button(obj) or self._is_interactive_frame(obj):
                self._sync_cursor(obj)
                return False
            if isinstance(obj, (QCheckBox, QComboBox)):
                self._sync_cursor(obj)
                return False

        # 2. Keep cursor synced when enabled/disabled toggles
        if etype == QEvent.Type.EnabledChange:
            if isinstance(obj, (QPushButton, QCheckBox, QComboBox)) or self._is_interactive_frame(obj):
                self._sync_cursor(obj)
                if not obj.isEnabled():
                    ov = self._overlay_for(obj, create=False)
                    if ov is not None:
                        ov.reset_immediately()
            return False

        # 3. Button & interactive card hover / press / ripple / resize
        if self._is_interactive_button(obj) or self._is_interactive_frame(obj):
            if etype == QEvent.Type.Enter:
                self._sync_cursor(obj)
                self._overlay_for(obj, create=True).set_hovered(True)
            elif etype == QEvent.Type.Leave:
                ov = self._overlay_for(obj, create=False)
                if ov is not None:
                    ov.set_hovered(False)
                    ov.set_pressed(False)
            elif etype == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                self._overlay_for(obj, create=True).set_pressed(True, event.position())
            elif etype == QEvent.Type.MouseButtonRelease:
                ov = self._overlay_for(obj, create=False)
                if ov is not None:
                    ov.set_pressed(False)
            elif etype == QEvent.Type.Resize:
                ov = self._overlay_for(obj, create=False)
                if ov is not None:
                    ov.sync_geometry()
            elif etype == QEvent.Type.Hide:
                ov = self._overlay_for(obj, create=False)
                if ov is not None:
                    ov.reset_immediately()
            return False

        # 4. Input focus halo animations
        if isinstance(obj, _FOCUS_WIDGET_TYPES):
            if etype == QEvent.Type.FocusIn:
                self._focus_for(obj, create=True).set_focused(True)
            elif etype == QEvent.Type.FocusOut:
                ov = self._focus_for(obj, create=False)
                if ov is not None:
                    ov.set_focused(False)
            elif etype == QEvent.Type.Resize:
                ov = self._focus_for(obj, create=False)
                if ov is not None:
                    ov.sync_geometry()
            elif etype == QEvent.Type.Hide:
                ov = self._focus_for(obj, create=False)
                if ov is not None:
                    ov.reset_immediately()
            return False

        return False


def install_motion(qt_app: QApplication | None = None) -> MotionEngine | None:
    """Install the global MotionEngine once on the active QApplication."""
    app = qt_app or QApplication.instance()
    if app is None:
        return None
    existing = getattr(app, "_ld_motion_engine", None)
    if isinstance(existing, MotionEngine):
        return existing
    engine = MotionEngine(app)
    app.installEventFilter(engine)

    def _cleanup() -> None:
        engine.detach()
        stop_all_animations()
        try:
            app.removeEventFilter(engine)
        except RuntimeError:
            pass

    app.aboutToQuit.connect(_cleanup)
    atexit.register(_cleanup)
    app._ld_motion_engine = engine
    return engine
