"""Shared native Qt6 widgets, async workers, search helpers, and image viewer."""

from __future__ import annotations

from collections import Counter, OrderedDict
import re
import threading
import traceback
import unicodedata
from pathlib import Path
from typing import Any, Callable, Iterable

from PySide6.QtCore import (
    QEasingCurve,
    QEvent,
    QMimeData,
    QObject,
    QParallelAnimationGroup,
    QPoint,
    QPointF,
    QPropertyAnimation,
    QRect,
    QRectF,
    QRunnable,
    QSize,
    Qt,
    QThreadPool,
    QTimer,
    QUrl,
    QVariantAnimation,
    Signal,
)
from PySide6.QtGui import QColor, QDrag, QGuiApplication, QImage, QImageReader, QInputMethodEvent, QKeyEvent, QPainter, QPixmap, QWheelEvent

from ..lora_inspection import (
    apply_lora_triggers,
    insert_trigger_phrase,
    remove_trigger_phrase,
    resolve_lora_trigger,
    trigger_present_in_prompt,
)
from .icons import icon_pixmap, studio_icon
from .theme import current_theme, tokens
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFrame,
    QGraphicsOpacityEffect,
    QGraphicsPixmapItem,
    QGraphicsScene,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QToolTip,
    QVBoxLayout,
    QWidget,
)


# ---------------- Async Worker ----------------


class _WorkerSignals(QObject):
    finished = Signal(object)
    error = Signal(str)


class _FuncTask(QRunnable):
    def __init__(self, fn: Callable[[], Any]):
        super().__init__()
        self.fn = fn
        self.signals = _WorkerSignals()

    def run(self) -> None:
        try:
            res = self.fn()
            self.signals.finished.emit(res)
        except Exception as exc:
            self.signals.error.emit(str(exc) or traceback.format_exc(limit=3))


def run_async(
    fn: Callable[[], Any],
    on_ok: Callable[[Any], None] | None = None,
    on_err: Callable[[str], None] | None = None,
) -> _FuncTask:
    task = _FuncTask(fn)
    if on_ok is not None:
        task.signals.finished.connect(on_ok)
    if on_err is not None:
        task.signals.error.connect(on_err)
    QThreadPool.globalInstance().start(task)
    return task


# ---------------- Search & LoRA Name Helpers ----------------

_LORA_MODEL_FOLDERS = {"anime", "sdxl", "krea2", "moss_image", "qwen_edit"}
_LORA_PREFIXES = {
    "krea2", "krea20", "krea2raw", "krea2turbo",
    "anima", "anima38", "anima38b",
    "sdxl", "moss", "mossimage", "qwen", "qwenedit",
}
_NORM_NON_ALNUM = re.compile(r"[^\w]+", re.UNICODE)


def normalize_search(value: str) -> str:
    s = unicodedata.normalize("NFKD", str(value or "").lower())
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    s = s.replace("ł", "l")
    return _NORM_NON_ALNUM.sub("", s).replace("_", "")


def lora_display_name(file: str) -> str:
    parts = str(file or "").replace("\\", "/").split("/")
    stem = re.sub(r"\.[^.]+$", "", parts[-1]) if parts else ""
    if len(parts) == 2 and parts[0] in _LORA_MODEL_FOLDERS:
        return stem
    segs = stem.split("_")
    norm = lambda v: re.sub(r"[\s._-]+", "", v.lower())
    if len(segs) >= 3 and norm(segs[0] + segs[1]) in _LORA_PREFIXES and segs[2]:
        stem = "_".join(segs[2:])
    elif len(segs) >= 2 and norm(segs[0]) in _LORA_PREFIXES and segs[1]:
        stem = "_".join(segs[1:])
    return stem.split("_", 1)[0] or stem


def lora_variant(file: str) -> tuple[str, str]:
    folders = str(file or "").replace("\\", "/").split("/")[:-1]
    if any(re.match(r"^cos\s*服装$", part, re.I) for part in folders):
        return ("cos", "Cos 服装版")
    if "角色" in folders:
        return ("character", "角色版")
    return ("", "")


_STEP_ZH_RE = re.compile(r"(\d+)\s*步")
_VERSION_ZH_RE = re.compile(r"版本\s*([A-Za-z0-9._-]+)")
_STEP_EN_RE = re.compile(r"(?:^|[_\-\s])(?:steps?|st)[_-]?(\d+)(?:$|[_\-\s])", re.I)
_EPOCH_RE = re.compile(r"(?:^|[_\-\s])(?:epoch|ep)[_-]?(\d+)(?:$|[_\-\s])", re.I)
_SEMVER_RE = re.compile(r"(?:^|[_\-\s])(v\d+(?:\.\d+)?[a-z]?)(?:$|[_\-\s])", re.I)
_TRAIL_STEP_RE = re.compile(r"[_\-]0*(\d{3,6})$")

ROLE_LORA_FILE = int(Qt.ItemDataRole.UserRole)
ROLE_LORA_FAV = int(Qt.ItemDataRole.UserRole) + 1
LORA_STAR_COLOR = "#D98A1B"


def lora_detail_tag(file: str) -> str:
    """Extract a concise step/version/suffix tag to distinguish same-name LoRAs."""
    parts = str(file or "").replace("\\", "/").split("/")
    stem = re.sub(r"\.[^.]+$", "", parts[-1]) if parts else ""
    if not stem:
        return ""
    m = _STEP_ZH_RE.search(stem)
    if m:
        return f"{m.group(1)}步"
    m = _VERSION_ZH_RE.search(stem)
    if m:
        return f"版本{m.group(1)}"
    m = _STEP_EN_RE.search(stem)
    if m:
        return f"{int(m.group(1))}步"
    m = _EPOCH_RE.search(stem)
    if m:
        return f"epoch {int(m.group(1))}"
    m = _SEMVER_RE.search(stem)
    if m:
        return m.group(1)
    m = _TRAIL_STEP_RE.search(stem)
    if m:
        return f"{int(m.group(1))}步"
    # Fallback: remaining tokens after display name
    base = lora_display_name(file)
    idx = stem.find(base)
    tail = stem[idx + len(base) :].strip(" _-.") if idx >= 0 else ""
    return tail


def lora_choice_name(file: str, *, detail: str | None = None) -> str:
    _, label = lora_variant(file)
    base = lora_display_name(file)
    parts = [base]
    if label:
        parts.append(label)
    if detail:
        parts.append(detail)
    return " · ".join(parts)


def lora_disambiguated_names(files: Iterable[str]) -> dict[str, str]:
    """Map each LoRA file to its display label, adding step/version tags whenever duplicates exist."""
    file_list = [str(f or "").replace("\\", "/") for f in files]
    base_map = {f: lora_choice_name(f) for f in file_list}
    base_counts = Counter(base_map.values())

    resolved: dict[str, str] = {}
    for f in file_list:
        base = base_map[f]
        if base_counts[base] <= 1:
            resolved[f] = base
            continue
        tag = lora_detail_tag(f)
        if tag and tag.lower() not in base.lower():
            resolved[f] = f"{base} · {tag}"
        else:
            stem = re.sub(r"\.[^.]+$", "", f.split("/")[-1])
            resolved[f] = f"{base} · {stem}" if stem != base else base

    # Second pass: if any duplicate still collides (e.g. same step count from different author/folder), disambiguate cleanly
    res_counts = Counter(resolved.values())
    if any(c > 1 for c in res_counts.values()):
        for f in file_list:
            cur = resolved[f]
            if res_counts[cur] <= 1:
                continue
            parts = f.split("/")
            stem = re.sub(r"\.[^.]+$", "", parts[-1])
            folder = parts[-2] if len(parts) >= 2 else ""
            if folder and folder not in cur and folder not in _LORA_MODEL_FOLDERS:
                cand = f"{cur} ({folder})"
            else:
                cand = stem
            resolved[f] = cand
        # Final fallback to full stem if folder still collided
        res_counts2 = Counter(resolved.values())
        for f in file_list:
            if res_counts2[resolved[f]] > 1:
                resolved[f] = re.sub(r"\.[^.]+$", "", f.split("/")[-1])
    return resolved


def sort_loras_favorites_first(files: Iterable[str], favorites: Iterable[str]) -> list[str]:
    """Keep matching LoRAs stable while floating favorited LoRAs to the very top."""
    fav_rank: dict[str, int] = {}
    for idx, fav in enumerate(favorites or ()):
        nf = str(fav or "").replace("\\", "/").strip()
        if nf and nf not in fav_rank:
            fav_rank[nf] = idx
    indexed = list(enumerate(files))
    indexed.sort(
        key=lambda pair: (
            0 if str(pair[1]).replace("\\", "/") in fav_rank else 1,
            fav_rank.get(str(pair[1]).replace("\\", "/"), 0),
            pair[0],
        )
    )
    return [f for _, f in indexed]


def lora_matches_search(file: str, info: dict | None, query: str) -> bool:
    raw_q = str(query or "").strip()
    if not raw_q:
        return True
    parts = str(file or "").replace("\\", "/").split("/")
    subfolders = [p for p in parts[:-1] if p and p not in _LORA_MODEL_FOLDERS]
    terms = [
        lora_display_name(file),
        lora_detail_tag(file),
        *subfolders,
        (info or {}).get("pinyin", ""),
        *((info or {}).get("english_aliases") or []),
    ]
    norm_terms = [normalize_search(t) for t in terms if t]
    # Support multi-token queries such as "温迪 2800" while keeping exact single-token matching fast
    tokens_q = [normalize_search(tok) for tok in raw_q.split() if normalize_search(tok)]
    if not tokens_q:
        return True
    return all(any(tok in nt for nt in norm_terms) for tok in tokens_q)


class _LoraStarDelegate(QStyledItemDelegate):
    """High-performance native item delegate rendering a right-side favorite star per LoRA row."""

    STAR_BOX_W = 24
    STAR_BOX_H = 24
    STAR_RIGHT_PAD = 2

    def __init__(self, view: QListWidget):
        super().__init__(view)
        self._view = view
        self.hovered_file: str | None = None
        self.pressed_file: str | None = None

    @classmethod
    def star_rect(cls, row_rect: QRect) -> QRect:
        x = row_rect.right() - cls.STAR_BOX_W - cls.STAR_RIGHT_PAD
        y = row_rect.top() + (row_rect.height() - cls.STAR_BOX_H) // 2
        return QRect(x, y, cls.STAR_BOX_W, cls.STAR_BOX_H)

    def sizeHint(self, option: QStyleOptionViewItem, index) -> QSize:  # noqa: N802 (Qt API)
        sz = super().sizeHint(option, index)
        return QSize(sz.width(), max(28, sz.height()))

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:
        file_key = index.data(ROLE_LORA_FILE)
        if not file_key:
            super().paint(painter, option, index)
            return

        # Paint native row background/hover/selection across full row width, then elide text before the star
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        star_r = self.star_rect(opt.rect)
        opt_text = QStyleOptionViewItem(opt)
        opt_text.rect = opt.rect.adjusted(0, 0, -(self.STAR_BOX_W + self.STAR_RIGHT_PAD + 2), 0)

        # Draw row background over full rect so highlight spans cleanly underneath the star too
        style = opt.widget.style() if opt.widget else QApplication.style()
        bg_opt = QStyleOptionViewItem(opt)
        bg_opt.text = ""
        bg_opt.icon = studio_icon("dot", "#00000000", 1) if not bg_opt.icon.isNull() else bg_opt.icon
        bg_opt.features &= ~QStyleOptionViewItem.ViewItemFeature.HasDisplay
        bg_opt.features &= ~QStyleOptionViewItem.ViewItemFeature.HasDecoration
        bg_opt.features &= ~QStyleOptionViewItem.ViewItemFeature.HasCheckIndicator
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, bg_opt, painter, opt.widget)

        # Draw icon, checkbox, and elided text inside the trimmed left area (suppressing double background)
        opt_text.state &= ~QStyle.StateFlag.State_MouseOver
        opt_text.state &= ~QStyle.StateFlag.State_Selected
        super().paint(painter, opt_text, index)

        is_fav = bool(index.data(ROLE_LORA_FAV))
        is_hov = self.hovered_file == file_key
        is_prs = self.pressed_file == file_key
        t = tokens(current_theme())

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if is_hov or is_prs:
            pill = QColor(LORA_STAR_COLOR if is_fav else t["accent"])
            pill.setAlpha(44 if is_prs else 24)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(pill)
            painter.drawRoundedRect(QRectF(star_r).adjusted(1.0, 1.0, -1.0, -1.0), 6.0, 6.0)

        if is_fav:
            pm = icon_pixmap("star-filled", LORA_STAR_COLOR, size=14, dpr=2.0)
        else:
            col = LORA_STAR_COLOR if is_hov else t["muted"]
            pm = icon_pixmap("star", col, size=14, dpr=2.0)
            if not is_hov:
                painter.setOpacity(0.55)
        px = star_r.x() + (star_r.width() - 14) // 2
        py = star_r.y() + (star_r.height() - 14) // 2
        painter.drawPixmap(px, py, pm)
        painter.restore()


class _LoraStarController(QObject):
    """Handles right-side star hover/click/tooltip on a LoRA QListWidget without triggering row pick."""

    def __init__(self, view: QListWidget, delegate: _LoraStarDelegate, on_toggle: Callable[[str], None]):
        super().__init__(view)
        self._view = view
        self._viewport = view.viewport()
        self._delegate = delegate
        self._on_toggle = on_toggle
        self._alive = True
        view.setMouseTracking(True)
        self._viewport.setMouseTracking(True)
        self._viewport.installEventFilter(self)
        view.destroyed.connect(self._mark_dead)

    def _mark_dead(self) -> None:
        self._alive = False

    def _hit_star(self, pos: QPoint) -> tuple[str | None, bool, QRect | None]:
        idx = self._view.indexAt(pos)
        if not idx.isValid():
            return None, False, None
        file_key = idx.data(ROLE_LORA_FILE)
        if not file_key:
            return None, False, None
        vr = self._view.visualRect(idx)
        sr = self._delegate.star_rect(vr)
        if sr.contains(pos):
            return str(file_key), bool(idx.data(ROLE_LORA_FAV)), sr
        return None, False, None

    def _set_hover(self, file_key: str | None) -> None:
        if self._delegate.hovered_file != file_key:
            self._delegate.hovered_file = file_key
            if file_key:
                self._viewport.setCursor(Qt.CursorShape.PointingHandCursor)
            else:
                self._viewport.unsetCursor()
            self._viewport.update()

    def eventFilter(self, obj, event):  # noqa: N802 (Qt API)
        if not self._alive or QApplication.closingDown():
            return False
        try:
            if obj is not self._viewport:
                return False
            et = event.type()
            if et == QEvent.Type.Destroy:
                self._alive = False
                return False
            if et == QEvent.Type.MouseMove:
                f, _, _ = self._hit_star(event.position().toPoint())
                self._set_hover(f)
                return False
            if et == QEvent.Type.Leave:
                self._delegate.pressed_file = None
                self._set_hover(None)
                return False
            if et == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                f, _, _ = self._hit_star(event.position().toPoint())
                if f:
                    self._delegate.pressed_file = f
                    self._viewport.update()
                    return True
            if et == QEvent.Type.MouseButtonDblClick and event.button() == Qt.MouseButton.LeftButton:
                f, _, _ = self._hit_star(event.position().toPoint())
                if f:
                    return True
            if et == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
                f, _, _ = self._hit_star(event.position().toPoint())
                was_pressed = self._delegate.pressed_file
                if was_pressed is not None:
                    self._delegate.pressed_file = None
                    self._viewport.update()
                if f and (was_pressed is None or was_pressed == f):
                    self._on_toggle(f)
                    return True
                if was_pressed is not None:
                    return True
            if et == QEvent.Type.ToolTip:
                f, is_fav, sr = self._hit_star(event.pos())
                if f and sr is not None:
                    tip = "取消收藏" if is_fav else "收藏此 LoRA（搜索同类型时排第一）"
                    QToolTip.showText(event.globalPos(), tip, self._viewport, sr)
                    return True
        except RuntimeError:
            self._alive = False
        return False


def attach_lora_star_delegate(view: QListWidget, on_toggle: Callable[[str], None]) -> _LoraStarDelegate:
    """Install the native favorite-star delegate and click handler on any LoRA QListWidget."""
    delegate = _LoraStarDelegate(view)
    view.setItemDelegate(delegate)
    controller = _LoraStarController(view, delegate, on_toggle)
    view._ld_star_controller = controller
    view._ld_star_delegate = delegate
    return delegate


_DECIMAL_TRANS = str.maketrans({
    "\u3002": ".",  # Chinese period
    "\uff0e": ".",  # Full-width dot
    "\uff61": ".",  # Half-width ideographic period
    "\uff10": "0",
    "\uff11": "1",
    "\uff12": "2",
    "\uff13": "3",
    "\uff14": "4",
    "\uff15": "5",
    "\uff16": "6",
    "\uff17": "7",
    "\uff18": "8",
    "\uff19": "9",
})


def normalize_decimal_input(text: str) -> str:
    """Convert Chinese/full-width periods and digits into ASCII decimal characters."""
    return str(text or "").translate(_DECIMAL_TRANS)


class DecimalSpinBox(QDoubleSpinBox):
    """QDoubleSpinBox that automatically turns Chinese periods (。) into ASCII '.' during typing, IME commit, or paste."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._forwarding = False
        le = self.lineEdit()
        if le is not None:
            le.installEventFilter(self)

    def eventFilter(self, obj, event):  # noqa: N802 (Qt API)
        if self._forwarding or QApplication.closingDown():
            return super().eventFilter(obj, event)
        if obj is self.lineEdit():
            et = event.type()
            if et == QEvent.Type.KeyPress:
                raw = event.text()
                mapped = normalize_decimal_input(raw)
                if raw and mapped != raw:
                    key = Qt.Key.Key_Period if mapped == "." else event.key()
                    self._forwarding = True
                    try:
                        synth = QKeyEvent(
                            QEvent.Type.KeyPress,
                            key,
                            event.modifiers(),
                            mapped,
                            event.isAutoRepeat(),
                            event.count(),
                        )
                        QApplication.sendEvent(obj, synth)
                    finally:
                        self._forwarding = False
                    return True
            elif et == QEvent.Type.InputMethod:
                commit = event.commitString()
                mapped = normalize_decimal_input(commit)
                if commit and mapped != commit:
                    self._forwarding = True
                    try:
                        synth = QInputMethodEvent(event.preeditString(), event.attributes())
                        synth.setCommitString(mapped, event.replacementStart(), event.replacementLength())
                        QApplication.sendEvent(obj, synth)
                    finally:
                        self._forwarding = False
                    return True
        return super().eventFilter(obj, event)

    def validate(self, text: str, pos: int):  # noqa: N802 (Qt API)
        norm = normalize_decimal_input(text)
        state, fixed, new_pos = super().validate(norm, pos)
        return state, normalize_decimal_input(fixed), new_pos

    def fixup(self, text: str) -> str:
        return super().fixup(normalize_decimal_input(text))

    def valueFromText(self, text: str) -> float:  # noqa: N802 (Qt API)
        return super().valueFromText(normalize_decimal_input(text))


def character_matches_search(card: dict, query: str) -> bool:
    q = normalize_search(query)
    if not q:
        return True
    terms = [
        card.get("name", ""),
        card.get("pinyin", ""),
        *(card.get("english_aliases") or []),
        *(card.get("search_terms") or []),
        *((card.get("source") or {}).get("aliases") or []),
    ]
    return any(q in normalize_search(t) for t in terms if t)


def checkpoint_family_and_dir(checkpoint: str, diffusion_models: dict) -> tuple[str, str]:
    info = (diffusion_models or {}).get(checkpoint) or {}
    fam = info.get("family", "")
    if fam == "krea2":
        return ("Krea2", "krea2")
    if fam == "moss_image":
        return ("MOSS-Image", "moss_image")
    if fam in ("anima38", "anima", "delta") or checkpoint == "Anima-3.8B-v1.1.safetensors":
        return ("Anima", "anime")
    if fam == "qwen_edit":
        return ("Qwen-Edit", "qwen_edit")
    return ("SDXL", "sdxl")


def copy_images_to_clipboard(paths: list[Path]) -> int:
    valid = [p.resolve() for p in paths if p and p.is_file()]
    if not valid:
        return 0
    cb = QGuiApplication.clipboard()
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(p)) for p in valid])
    if len(valid) == 1:
        img = QImage(str(valid[0]))
        if not img.isNull():
            mime.setImageData(img)
    cb.setMimeData(mime)
    return len(valid)


# ---------------- Symmetric LoRA Trigger Word Helpers ----------------
# Re-exported from comic_workflow.lora_inspection for shared frontend/backend word-boundary handling.


# ---------------- Shared Image Cache & Async Decoder ----------------


class _DecodeTask(QRunnable):
    def __init__(self, fn: Callable[[], None]):
        super().__init__()
        self.setAutoDelete(True)
        self._fn = fn

    def run(self) -> None:
        try:
            self._fn()
        except Exception:
            pass


class ImageCache(QObject):
    """Thread-safe LRU pixmap/thumbnail cache with background prefetch and header-only size lookup."""

    _full_decoded = Signal(object, object, object)  # (file_key, path, QImage)
    _thumb_decoded = Signal(object, object, object, object)  # (thumb_key, path, QImage, orig_size)

    MAX_FULL_BYTES = 180 * 1024 * 1024
    MAX_FULL_ITEMS = 28
    MAX_DECODED_BYTES = 96 * 1024 * 1024
    MAX_DECODED_ITEMS = 12
    MAX_THUMB_ITEMS = 320
    MAX_SIZE_ITEMS = 512

    def __init__(self) -> None:
        super().__init__()
        self._lock = threading.Lock()
        self._size_cache: OrderedDict[tuple[str, int, int], tuple[int, int]] = OrderedDict()
        self._full_cache: OrderedDict[tuple[str, int, int], tuple[QPixmap, int]] = OrderedDict()
        self._full_bytes = 0
        self._decoded_full: OrderedDict[tuple[str, int, int], tuple[QImage, int]] = OrderedDict()
        self._decoded_bytes = 0
        self._inflight_full: set[tuple[str, int, int]] = set()
        self._full_waiters: dict[tuple[str, int, int], list[Callable[[Path, QPixmap], None]]] = {}

        self._thumb_cache: OrderedDict[tuple[tuple[str, int, int], int, int], QPixmap] = OrderedDict()
        self._thumb_by_file: dict[tuple[str, int, int], tuple[tuple[str, int, int], int, int]] = {}
        self._inflight_thumbs: set[tuple[tuple[str, int, int], int, int]] = set()
        self._thumb_waiters: dict[
            tuple[tuple[str, int, int], int, int], list[Callable[[Path, QPixmap], None]]
        ] = {}

        self._pool = QThreadPool()
        self._pool.setMaxThreadCount(3)
        self._pool.setExpiryTimeout(15000)

        self._full_decoded.connect(self._on_full_decoded, Qt.ConnectionType.QueuedConnection)
        self._thumb_decoded.connect(self._on_thumb_decoded, Qt.ConnectionType.QueuedConnection)

    @staticmethod
    def file_key(path: Path | None) -> tuple[str, int, int] | None:
        if path is None:
            return None
        try:
            st = path.stat()
        except OSError:
            return None
        return (str(path), st.st_mtime_ns, st.st_size)

    def image_size(self, path: Path | None) -> tuple[int, int]:
        key = self.file_key(path)
        if key is None or path is None:
            return (0, 0)
        cached = self._size_cache.get(key)
        if cached is not None:
            self._size_cache.move_to_end(key)
            return cached
        full = self._full_cache.get(key)
        if full is not None:
            sz = (full[0].width(), full[0].height())
            self._remember_size(key, sz)
            return sz
        reader = QImageReader(str(path))
        reader.setAutoTransform(True)
        qsz = reader.size()
        if qsz.isValid():
            sz = (qsz.width(), qsz.height())
            self._remember_size(key, sz)
            return sz
        return (0, 0)

    def _remember_size(self, key: tuple[str, int, int], sz: tuple[int, int]) -> None:
        if sz[0] <= 0 or sz[1] <= 0:
            return
        self._size_cache[key] = sz
        self._size_cache.move_to_end(key)
        while len(self._size_cache) > self.MAX_SIZE_ITEMS:
            self._size_cache.popitem(last=False)

    def _store_full_pixmap(self, key: tuple[str, int, int], pm: QPixmap) -> None:
        if pm.isNull():
            return
        est = max(1, pm.width() * pm.height() * 4)
        self._remember_size(key, (pm.width(), pm.height()))
        old = self._full_cache.pop(key, None)
        if old is not None:
            self._full_bytes -= old[1]
        self._full_cache[key] = (pm, est)
        self._full_bytes += est
        while (self._full_bytes > self.MAX_FULL_BYTES or len(self._full_cache) > self.MAX_FULL_ITEMS) and len(self._full_cache) > 1:
            _, (_, bytes_used) = self._full_cache.popitem(last=False)
            self._full_bytes -= bytes_used

    def _store_thumb_pixmap(self, tkey: tuple[tuple[str, int, int], int, int], pm: QPixmap) -> None:
        if pm.isNull():
            return
        self._thumb_cache[tkey] = pm
        self._thumb_cache.move_to_end(tkey)
        self._thumb_by_file[tkey[0]] = tkey
        while len(self._thumb_cache) > self.MAX_THUMB_ITEMS:
            evicted_key, _ = self._thumb_cache.popitem(last=False)
            if self._thumb_by_file.get(evicted_key[0]) == evicted_key:
                self._thumb_by_file.pop(evicted_key[0], None)

    def _pop_decoded_full(self, key: tuple[str, int, int]) -> QImage | None:
        with self._lock:
            entry = self._decoded_full.pop(key, None)
            if entry is not None:
                self._decoded_bytes -= entry[1]
                return entry[0]
        return None

    def _push_decoded_full(self, key: tuple[str, int, int], img: QImage) -> None:
        if img.isNull():
            return
        est = max(1, img.width() * img.height() * 4)
        with self._lock:
            old = self._decoded_full.pop(key, None)
            if old is not None:
                self._decoded_bytes -= old[1]
            self._decoded_full[key] = (img, est)
            self._decoded_bytes += est
            while (
                self._decoded_bytes > self.MAX_DECODED_BYTES
                or len(self._decoded_full) > self.MAX_DECODED_ITEMS
            ) and len(self._decoded_full) > 1:
                _, (_, bytes_used) = self._decoded_full.popitem(last=False)
                self._decoded_bytes -= bytes_used

    def peek_pixmap(self, path: Path | None) -> QPixmap | None:
        key = self.file_key(path)
        if key is None:
            return None
        entry = self._full_cache.get(key)
        if entry is not None:
            self._full_cache.move_to_end(key)
            return entry[0]
        img = self._pop_decoded_full(key)
        if img is not None and not img.isNull():
            pm = QPixmap.fromImage(img)
            self._store_full_pixmap(key, pm)
            return pm
        return None

    def get_pixmap(self, path: Path | None) -> QPixmap:
        pm = self.peek_pixmap(path)
        if pm is not None:
            return pm
        key = self.file_key(path)
        if key is None or path is None:
            return QPixmap()
        reader = QImageReader(str(path))
        reader.setAutoTransform(True)
        img = reader.read()
        pm = QPixmap.fromImage(img) if not img.isNull() else QPixmap(str(path))
        self._store_full_pixmap(key, pm)
        return pm

    def request_pixmap(
        self,
        path: Path | None,
        on_ready: Callable[[Path, QPixmap], None],
        *,
        sync_below_bytes: int = 384 * 1024,
    ) -> bool:
        """Deliver full pixmap via on_ready; returns True if delivered synchronously."""
        if path is None:
            return True
        pm = self.peek_pixmap(path)
        if pm is not None:
            on_ready(path, pm)
            return True
        key = self.file_key(path)
        if key is None:
            on_ready(path, QPixmap())
            return True
        if key[2] <= sync_below_bytes:
            pm = self.get_pixmap(path)
            on_ready(path, pm)
            return True
        self._full_waiters.setdefault(key, []).append(on_ready)
        self._schedule_full_decode(key, path, priority=1)
        return False

    def prefetch(self, paths: list[Path | None]) -> None:
        for path in paths:
            key = self.file_key(path)
            if key is None or path is None:
                continue
            if key in self._full_cache:
                self._full_cache.move_to_end(key)
                continue
            with self._lock:
                if key in self._decoded_full or key in self._inflight_full:
                    continue
            self._schedule_full_decode(key, path, priority=-1)

    def _schedule_full_decode(self, key: tuple[str, int, int], path: Path, *, priority: int = 0) -> None:
        with self._lock:
            if key in self._inflight_full:
                return
            self._inflight_full.add(key)

        def _work() -> None:
            reader = QImageReader(str(path))
            reader.setAutoTransform(True)
            img = reader.read()
            if not img.isNull():
                self._push_decoded_full(key, img)
            self._full_decoded.emit(key, path, img)

        self._pool.start(_DecodeTask(_work), priority)

    def _on_full_decoded(self, key: tuple[str, int, int], path: Path, img: QImage) -> None:
        with self._lock:
            self._inflight_full.discard(key)
        entry = self._full_cache.get(key)
        if entry is not None:
            pm = entry[0]
            self._pop_decoded_full(key)
        elif img is not None and not img.isNull():
            self._pop_decoded_full(key)
            pm = QPixmap.fromImage(img)
            self._store_full_pixmap(key, pm)
        else:
            pm = QPixmap()
        waiters = self._full_waiters.pop(key, [])
        for cb in waiters:
            try:
                cb(path, pm)
            except RuntimeError:
                pass

    def peek_thumbnail(self, path: Path | None, w: int, h: int) -> QPixmap | None:
        key = self.file_key(path)
        if key is None:
            return None
        tkey = (key, w, h)
        pm = self._thumb_cache.get(tkey)
        if pm is not None:
            self._thumb_cache.move_to_end(tkey)
            return pm
        return None

    def peek_any_thumbnail(self, path: Path | None) -> QPixmap | None:
        key = self.file_key(path)
        if key is None:
            return None
        tkey = self._thumb_by_file.get(key)
        if tkey is not None:
            return self._thumb_cache.get(tkey)
        return None

    def _scale_pixmap_thumb(self, src: QPixmap, w: int, h: int) -> QPixmap:
        scaled = src.scaled(
            w * 2,
            h * 2,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        scaled.setDevicePixelRatio(2.0)
        return scaled

    def get_thumbnail(self, path: Path | None, w: int, h: int) -> QPixmap:
        pm = self.peek_thumbnail(path, w, h)
        if pm is not None:
            return pm
        key = self.file_key(path)
        if key is None or path is None:
            return QPixmap()
        tkey = (key, w, h)
        full = self.peek_pixmap(path)
        if full is not None and not full.isNull():
            thumb = self._scale_pixmap_thumb(full, w, h)
            self._store_thumb_pixmap(tkey, thumb)
            return thumb
        reader = QImageReader(str(path))
        reader.setAutoTransform(True)
        qsz = reader.size()
        if qsz.isValid():
            self._remember_size(key, (qsz.width(), qsz.height()))
        img = reader.read()
        if img.isNull():
            return QPixmap()
        self._remember_size(key, (img.width(), img.height()))
        scaled = img.scaled(
            w * 2,
            h * 2,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        thumb = QPixmap.fromImage(scaled)
        thumb.setDevicePixelRatio(2.0)
        self._store_thumb_pixmap(tkey, thumb)
        return thumb

    def request_thumbnail(
        self,
        path: Path | None,
        w: int,
        h: int,
        on_ready: Callable[[Path, QPixmap], None],
        *,
        sync_below_bytes: int = 384 * 1024,
    ) -> bool:
        """Deliver HiDPI-sharp scaled thumbnail via on_ready; returns True if synchronous."""
        if path is None:
            return True
        pm = self.peek_thumbnail(path, w, h)
        if pm is not None:
            on_ready(path, pm)
            return True
        key = self.file_key(path)
        if key is None:
            on_ready(path, QPixmap())
            return True
        tkey = (key, w, h)
        full = self.peek_pixmap(path)
        if full is not None and not full.isNull():
            thumb = self._scale_pixmap_thumb(full, w, h)
            self._store_thumb_pixmap(tkey, thumb)
            on_ready(path, thumb)
            return True
        if key[2] <= sync_below_bytes:
            thumb = self.get_thumbnail(path, w, h)
            on_ready(path, thumb)
            return True
        self._thumb_waiters.setdefault(tkey, []).append(on_ready)
        with self._lock:
            if tkey in self._inflight_thumbs:
                return False
            self._inflight_thumbs.add(tkey)

        def _work() -> None:
            reader = QImageReader(str(path))
            reader.setAutoTransform(True)
            img = reader.read()
            if img.isNull():
                self._thumb_decoded.emit(tkey, path, QImage(), (0, 0))
                return
            orig_sz = (img.width(), img.height())
            scaled = img.scaled(
                w * 2,
                h * 2,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self._thumb_decoded.emit(tkey, path, scaled, orig_sz)

        self._pool.start(_DecodeTask(_work), 0)
        return False

    def _on_thumb_decoded(
        self,
        tkey: tuple[tuple[str, int, int], int, int],
        path: Path,
        img: QImage,
        orig_sz: tuple[int, int],
    ) -> None:
        with self._lock:
            self._inflight_thumbs.discard(tkey)
        if orig_sz and orig_sz[0] > 0:
            self._remember_size(tkey[0], orig_sz)
        pm = self._thumb_cache.get(tkey)
        if pm is None:
            if img is not None and not img.isNull():
                pm = QPixmap.fromImage(img)
                pm.setDevicePixelRatio(2.0)
                self._store_thumb_pixmap(tkey, pm)
            else:
                pm = QPixmap()
        waiters = self._thumb_waiters.pop(tkey, [])
        for cb in waiters:
            try:
                cb(path, pm)
            except RuntimeError:
                pass

    def clear(self) -> None:
        self._size_cache.clear()
        self._full_cache.clear()
        self._full_bytes = 0
        self._thumb_cache.clear()
        self._thumb_by_file.clear()
        with self._lock:
            self._decoded_full.clear()
            self._decoded_bytes = 0


_IMAGE_CACHE: ImageCache | None = None


def image_cache() -> ImageCache:
    global _IMAGE_CACHE
    if _IMAGE_CACHE is None:
        _IMAGE_CACHE = ImageCache()
    return _IMAGE_CACHE


# ---------------- Image Viewer & Lightbox ----------------


class ZoomableImageView(QGraphicsView):
    """Smooth native wheel-zoom, drag-pan, double-click action/fit, and cached image viewer."""

    clicked = Signal()
    double_clicked = Signal()
    step_requested = Signal(int)

    def __init__(self, parent: QWidget | None = None, *, dark_bg: bool = False, defer_single_click: bool = False):
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self._item = QGraphicsPixmapItem()
        self._item.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
        self._scene.addItem(self._item)
        self._pixmap = QPixmap()
        self._path: Path | None = None
        self._loaded_key: tuple[str, int, int] | None = None
        self._load_seq = 0
        self._zoom = 1.0
        self._fit_mode = True
        self._press_pos: QPointF | None = None
        self._defer_single_click = bool(defer_single_click)
        self._click_timer = QTimer(self)
        self._click_timer.setSingleShot(True)
        self._click_timer.timeout.connect(self._emit_deferred_click)

        self.setRenderHints(
            QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform
        )
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.SmartViewportUpdate)
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setFrameShape(QFrame.Shape.NoFrame)
        if dark_bg:
            self.setStyleSheet("background-color: #171b19;")
        else:
            self.setStyleSheet("background-color: transparent;")

    def _sync_interaction_mode(self) -> None:
        if self._pixmap.isNull():
            self.setDragMode(QGraphicsView.DragMode.NoDrag)
            self.unsetCursor()
        elif self._fit_mode:
            self.setDragMode(QGraphicsView.DragMode.NoDrag)
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        else:
            self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)

    def _apply_pixmap(self, pm: QPixmap, key: tuple[str, int, int] | None) -> None:
        self._pixmap = pm
        self._loaded_key = key
        self._item.setPixmap(pm)
        if pm.isNull():
            self._scene.setSceneRect(0, 0, 1, 1)
        else:
            self._scene.setSceneRect(self._item.boundingRect())
            self._fit_mode = True
            self._fit()
        self._sync_interaction_mode()

    def set_image(self, path: Path | None, *, force: bool = False) -> None:
        cache = image_cache()
        key = cache.file_key(path)
        if (
            not force
            and key is not None
            and key == self._loaded_key
            and not self._pixmap.isNull()
        ):
            self._path = path
            return

        self._path = path
        self._load_seq += 1
        seq = self._load_seq
        if path is None or key is None:
            self._click_timer.stop()
            self._apply_pixmap(QPixmap(), None)
            return

        cached = cache.peek_pixmap(path)
        if cached is not None:
            self._apply_pixmap(cached, key)
            return

        # Show cached thumbnail immediately only when full-res will decode asynchronously.
        if key[2] > 384 * 1024:
            thumb = cache.peek_any_thumbnail(path)
            if thumb is not None and not thumb.isNull():
                self._apply_pixmap(thumb, None)

        def _on_loaded(_p: Path, pm: QPixmap) -> None:
            if seq != self._load_seq:
                return
            self._apply_pixmap(pm, key if not pm.isNull() else None)

        cache.request_pixmap(path, _on_loaded)

    def current_path(self) -> Path | None:
        return self._path

    def image_dimensions(self) -> tuple[int, int]:
        if self._loaded_key is not None and not self._pixmap.isNull():
            return (self._pixmap.width(), self._pixmap.height())
        return image_cache().image_size(self._path)

    def _fit(self) -> None:
        if self._pixmap.isNull():
            return
        self.resetTransform()
        self.fitInView(self._item, Qt.AspectRatioMode.KeepAspectRatio)
        self._zoom = 1.0
        self._fit_mode = True
        self._sync_interaction_mode()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._fit_mode and not self._pixmap.isNull():
            self._fit()

    def _emit_deferred_click(self) -> None:
        if self._fit_mode and not self._pixmap.isNull():
            self.clicked.emit()

    def _click_delay_ms(self) -> int:
        return min(max(QApplication.doubleClickInterval(), 180), 300)

    def hideEvent(self, event) -> None:
        self._click_timer.stop()
        super().hideEvent(event)

    def wheelEvent(self, event: QWheelEvent) -> None:
        if self._pixmap.isNull():
            return
        self._click_timer.stop()
        factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
        new_zoom = max(0.25, min(12.0, self._zoom * factor))
        actual = new_zoom / self._zoom
        self._zoom = new_zoom
        self._fit_mode = False
        self._sync_interaction_mode()
        self.scale(actual, actual)

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._click_timer.stop()
            self._press_pos = None
            if not self._fit_mode:
                self._fit()
                event.accept()
                return
            if not self._pixmap.isNull():
                self.double_clicked.emit()
                event.accept()
                return
        super().mouseDoubleClickEvent(event)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_pos = event.position()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._press_pos is not None:
            if (event.position() - self._press_pos).manhattanLength() < 6 and self._fit_mode and not self._pixmap.isNull():
                if self._defer_single_click:
                    self._click_timer.start(self._click_delay_ms())
                else:
                    self.clicked.emit()
        self._press_pos = None
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Left:
            self.step_requested.emit(-1)
            event.accept()
            return
        if event.key() == Qt.Key.Key_Right:
            self.step_requested.emit(1)
            event.accept()
            return
        super().keyPressEvent(event)


class ImageLightboxDialog(QDialog):
    """Fullscreen dark lightbox with wheel zoom, prev/next, copy, and cached prefetch."""

    def __init__(
        self,
        paths: list[Path],
        start_index: int = 0,
        parent: QWidget | None = None,
        *,
        labels: list[str] | None = None,
    ):
        super().__init__(parent)
        self.setObjectName("ImageLightbox")
        self.setWindowTitle("大图预览 · LocalDraw")
        self.resize(1280, 820)
        paired: list[tuple[Path, str]] = []
        for idx, p in enumerate(paths):
            if p and p.is_file():
                lbl = labels[idx] if labels and idx < len(labels) else ""
                paired.append((p, lbl))
        self._paths = [p for p, _ in paired]
        self._labels = [lbl for _, lbl in paired]
        self._index = max(0, min(start_index, len(self._paths) - 1)) if self._paths else 0
        self._base_info_text = ""

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        bar = QFrame()
        bar.setStyleSheet("background-color: #1e2320; border-bottom: 1px solid #303834;")
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(14, 8, 14, 8)
        self._info = QLabel("")
        self._info.setStyleSheet("color: #f3eee7; font-weight: 600;")
        self._prev = QPushButton("上一张")
        self._prev.setIcon(studio_icon("chevron-left", "#f3eee7", 14))
        self._next = QPushButton("下一张")
        self._next.setIcon(studio_icon("chevron-right", "#f3eee7", 14))
        copy_btn = QPushButton("复制原图")
        copy_btn.setIcon(studio_icon("copy", "#f3eee7", 14))
        fit_btn = QPushButton("适应窗口")
        close_btn = QPushButton("关闭")
        close_btn.setIcon(studio_icon("close", "#f3eee7", 14))
        for b in (self._prev, self._next, copy_btn, fit_btn, close_btn):
            b.setStyleSheet(
                "QPushButton { background:#2c332f; color:#f3eee7; border:1px solid #445049; padding:5px 12px; border-radius:6px; }"
                "QPushButton:hover { background:#38423d; border-color:#5a6b62; }"
                "QPushButton:pressed { background:#232926; }"
            )
        bl.addWidget(self._info)
        bl.addStretch(1)
        bl.addWidget(self._prev)
        bl.addWidget(self._next)
        bl.addWidget(fit_btn)
        bl.addWidget(copy_btn)
        bl.addWidget(close_btn)
        root.addWidget(bar)

        self._viewer = ZoomableImageView(self, dark_bg=True)
        self._viewer.step_requested.connect(self._step)
        root.addWidget(self._viewer, 1)

        self._prev.clicked.connect(lambda: self._step(-1))
        self._next.clicked.connect(lambda: self._step(1))
        fit_btn.clicked.connect(self._viewer._fit)
        copy_btn.clicked.connect(self._copy)
        close_btn.clicked.connect(self.accept)
        self._show()

    def _copy(self) -> None:
        if self._paths and copy_images_to_clipboard([self._paths[self._index]]):
            self._info.setText(self._base_info_text + "  ·  已复制")

    def _step(self, delta: int) -> None:
        if len(self._paths) <= 1:
            return
        self._index = (self._index + delta) % len(self._paths)
        self._show()

    def _show(self) -> None:
        if not self._paths:
            return
        p = self._paths[self._index]
        self._viewer.set_image(p)
        w, h = image_cache().image_size(p)
        lbl = self._labels[self._index] if self._index < len(self._labels) else ""
        parts = [f"{self._index + 1} / {len(self._paths)}"]
        if lbl:
            parts.append(lbl)
        if w > 0 and h > 0:
            parts.append(f"{w}×{h}")
        self._base_info_text = "  ·  ".join(parts)
        self._info.setText(self._base_info_text)
        self._prev.setEnabled(len(self._paths) > 1)
        self._next.setEnabled(len(self._paths) > 1)
        if len(self._paths) > 1:
            neighbors = [
                self._paths[(self._index + 1) % len(self._paths)],
                self._paths[(self._index - 1) % len(self._paths)],
            ]
            image_cache().prefetch(neighbors)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Left:
            self._step(-1)
        elif event.key() == Qt.Key.Key_Right:
            self._step(1)
        elif event.key() == Qt.Key.Key_Escape:
            self.accept()
        else:
            super().keyPressEvent(event)


# ---------------- Image Size Controls ----------------


class ImageSizeWidget(QWidget):
    """Native equivalent of web/image-size.js with 8px/16px alignment."""

    changed = Signal()

    RATIOS = [
        ("3:2", "3:2 · 横图"),
        ("2:3", "2:3 · 竖图"),
        ("1:1", "1:1 · 方图"),
        ("16:9", "16:9 · 宽屏"),
        ("9:16", "9:16 · 竖屏"),
        ("custom", "自定义比例"),
    ]
    RESOLUTIONS = [
        ("default", "默认"),
        ("1024", "1K (1024)"),
        ("1536", "1.5K (1536)"),
        ("2048", "2K (2048)"),
    ]

    def __init__(self, *, allow_storyboard: bool = False, parent: QWidget | None = None):
        super().__init__(parent)
        self._alignment = 8
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)

        row = QHBoxLayout()
        row.setSpacing(6)
        self.ratio = QComboBox()
        if allow_storyboard:
            self.ratio.addItem("按各格分镜比例", "storyboard")
        for val, lbl in self.RATIOS:
            self.ratio.addItem(lbl, val)
        self.resolution = QComboBox()
        for val, lbl in self.RESOLUTIONS:
            self.resolution.addItem(lbl, val)
        row.addWidget(QLabel("比例"))
        row.addWidget(self.ratio, 1)
        row.addWidget(QLabel("尺寸"))
        row.addWidget(self.resolution, 1)
        lay.addLayout(row)

        self._custom_row = QWidget()
        cl = QHBoxLayout(self._custom_row)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(6)
        self.custom_w = QSpinBox()
        self.custom_w.setRange(1, 9999)
        self.custom_w.setValue(4)
        self.custom_h = QSpinBox()
        self.custom_h.setRange(1, 9999)
        self.custom_h.setValue(3)
        cl.addWidget(QLabel("宽比"))
        cl.addWidget(self.custom_w, 1)
        cl.addWidget(QLabel("高比"))
        cl.addWidget(self.custom_h, 1)
        self._custom_row.setVisible(False)
        lay.addWidget(self._custom_row)

        self.hint = QLabel("")
        self.hint.setObjectName("Muted")
        lay.addWidget(self.hint)

        self.ratio.currentIndexChanged.connect(self._on_change)
        self.resolution.currentIndexChanged.connect(self._on_change)
        self.custom_w.valueChanged.connect(self._on_change)
        self.custom_h.valueChanged.connect(self._on_change)
        self._update_hint()

    def set_alignment(self, align: int) -> None:
        self._alignment = 16 if align == 16 else 8
        self._update_hint()

    def _on_change(self) -> None:
        self._custom_row.setVisible(self.ratio.currentData() == "custom")
        self._update_hint()
        self.changed.emit()

    def _update_hint(self) -> None:
        from ..image_size import dimensions

        r = self.aspect_ratio_value()
        res = self.resolution.currentData() or "default"
        if r == "storyboard":
            self.hint.setText(f"分镜比例 · {self.resolution.currentText()} · {self._alignment}px")
            return
        try:
            w, h = dimensions(r, res, alignment=self._alignment)
            self.hint.setText(f"{w} × {h} px · {self._alignment}px 对齐")
        except Exception as exc:
            self.hint.setText(str(exc))

    def aspect_ratio_value(self) -> str:
        r = self.ratio.currentData() or "3:2"
        if r == "custom":
            return f"{self.custom_w.value()}:{self.custom_h.value()}"
        return r

    def read(self) -> dict[str, Any]:
        return {
            "aspect_ratio": self.aspect_ratio_value(),
            "resolution": self.resolution.currentData() or "default",
        }

    def summary(self) -> str:
        from ..image_size import dimensions

        r = self.aspect_ratio_value()
        res = self.resolution.currentData() or "default"
        if r == "storyboard":
            return f"分镜比例 · {self.resolution.currentText()}"
        try:
            w, h = dimensions(r, res, alignment=self._alignment)
            return f"{w}×{h}"
        except Exception:
            return r

    def apply(self, saved: dict) -> None:
        if not saved:
            return
        ratio = str(saved.get("aspect_ratio") or "3:2")
        idx = self.ratio.findData(ratio)
        if idx >= 0:
            self.ratio.setCurrentIndex(idx)
        elif ":" in ratio:
            cidx = self.ratio.findData("custom")
            if cidx >= 0:
                self.ratio.setCurrentIndex(cidx)
                parts = ratio.split(":", 1)
                if parts[0].isdigit() and parts[1].isdigit():
                    self.custom_w.setValue(int(parts[0]))
                    self.custom_h.setValue(int(parts[1]))
        res = str(saved.get("resolution") or "default")
        ridx = self.resolution.findData(res)
        if ridx >= 0:
            self.resolution.setCurrentIndex(ridx)
        self._update_hint()


# ---------------- Live Job Banner ----------------


class JobBannerWidget(QFrame):
    """Compact persistent task progress strip (stays visible when idle)."""

    control_requested = Signal(str, str)  # action, job_id

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("JobBanner")
        self.setProperty("state", "idle")
        self._job_id: str | None = None

        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 6, 10, 6)
        lay.setSpacing(4)

        top = QHBoxLayout()
        top.setSpacing(6)
        self._state_badge = QLabel("待命")
        self._state_badge.setObjectName("BadgeMuted")
        self._title = QLabel("暂无进行中的任务")
        self._title.setStyleSheet("font-weight: 600;")
        self._title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._title.setMinimumWidth(0)
        self._eta = QLabel("")
        self._eta.setObjectName("Muted")
        self._pause_btn = QPushButton("暂停")
        self._pause_btn.setProperty("compact", True)
        self._resume_btn = QPushButton("继续")
        self._resume_btn.setProperty("compact", True)
        self._resume_btn.setProperty("primary", True)
        self._cancel_btn = QPushButton("结束")
        self._cancel_btn.setProperty("compact", True)
        self._cancel_btn.setProperty("danger", True)

        top.addWidget(self._state_badge)
        top.addWidget(self._title, 1)
        top.addWidget(self._eta)
        top.addWidget(self._pause_btn)
        top.addWidget(self._resume_btn)
        top.addWidget(self._cancel_btn)
        lay.addLayout(top)

        self._bar = QProgressBar()
        self._bar.setTextVisible(False)
        self._bar.setFixedHeight(6)
        self._bar.setRange(0, 1)
        self._bar.setValue(0)
        lay.addWidget(self._bar)

        self._detail = QLabel("")
        self._detail.setObjectName("Muted")
        self._detail.setVisible(False)
        lay.addWidget(self._detail)

        self._pause_btn.setVisible(False)
        self._resume_btn.setVisible(False)
        self._cancel_btn.setVisible(False)

        self._pause_btn.clicked.connect(lambda: self._emit("pause_job"))
        self._resume_btn.clicked.connect(lambda: self._emit("resume_job"))
        self._cancel_btn.clicked.connect(lambda: self._emit("cancel_job"))

    def _emit(self, action: str) -> None:
        if self._job_id:
            self.control_requested.emit(action, self._job_id)

    def _set_visual_state(self, state: str, badge_name: str, badge_text: str) -> None:
        self._state_badge.setText(badge_text)
        if self._state_badge.objectName() != badge_name:
            self._state_badge.setObjectName(badge_name)
            self._state_badge.style().unpolish(self._state_badge)
            self._state_badge.style().polish(self._state_badge)
        if self.property("state") != state:
            self.setProperty("state", state)
            self.style().unpolish(self)
            self.style().polish(self)
        if self._bar.property("state") != state:
            self._bar.setProperty("state", state)
            self._bar.style().unpolish(self._bar)
            self._bar.style().polish(self._bar)

    def update_jobs(self, jobs: list[dict]) -> dict | None:
        active = next(
            (j for j in jobs if j.get("status") in {"running", "pausing", "paused", "cancelling"}),
            None,
        )
        if not active:
            self._job_id = None
            self._pause_btn.setVisible(False)
            self._resume_btn.setVisible(False)
            self._cancel_btn.setVisible(False)
            self._detail.setText("")
            self._detail.setVisible(False)
            last_finished = next(
                (j for j in jobs if j.get("status") in {"done", "failed", "cancelled", "interrupted"}),
                None,
            )
            if not last_finished:
                self._set_visual_state("idle", "BadgeMuted", "待命")
                self._title.setText("暂无进行中的任务")
                self._eta.setText("")
                self._bar.setRange(0, 1)
                self._bar.setValue(0)
                return None

            lst = last_finished.get("status", "done")
            label = str(last_finished.get("label") or "").strip()
            msg = str(last_finished.get("message") or "").strip()
            if lst == "done":
                self._set_visual_state("done", "BadgeSuccess", "已完成")
                summary = f" · {msg}" if msg and msg not in {"完成", "已完成"} and len(msg) <= 28 else ""
                self._title.setText(f"上一个任务已完成{summary}")
                self._eta.setText(label)
                self._bar.setRange(0, 1)
                self._bar.setValue(1)
            elif lst == "failed":
                self._set_visual_state("failed", "BadgeWarn", "未完成")
                err = str(last_finished.get("error") or msg or "").strip()
                self._title.setText(f"上一个任务未完成{(' · ' + err[:36]) if err else ''}")
                self._eta.setText(label)
                self._bar.setRange(0, 1)
                self._bar.setValue(0)
            else:
                badge_txt = "已取消" if lst == "cancelled" else "已中断"
                self._set_visual_state("idle", "BadgeMuted", badge_txt)
                self._title.setText(f"上一个任务{badge_txt}")
                self._eta.setText(label)
                self._bar.setRange(0, 1)
                self._bar.setValue(0)
            return None

        self._job_id = active["id"]
        st = active.get("status", "running")
        labels = {"running": "正在进行", "pausing": "正在暂停", "paused": "已暂停", "cancelling": "正在结束"}
        badge_name = "BadgeWarn" if st == "paused" else "BadgeAccent"
        self._set_visual_state("paused" if st == "paused" else "running", badge_name, labels.get(st, st))
        lbl = str(active.get("label") or "任务").strip()
        msg = str(active.get("message") or "").strip()
        self._title.setText(f"{lbl} · {msg}" if msg else lbl)
        prog = active.get("progress") or {}
        total = int(prog.get("total") or 0)
        done = int(prog.get("completed") or 0)
        if total > 0:
            self._bar.setRange(0, total)
            self._bar.setValue(min(done, total))
            unit = prog.get("unit", "")
            eta_sec = (active.get("eta") or {}).get("remaining_seconds")
            eta_txt = f" · 约剩 {int(eta_sec)} 秒" if isinstance(eta_sec, (int, float)) and eta_sec > 1 else ""
            self._eta.setText(f"{done} / {total} {unit}{eta_txt}")
        else:
            self._bar.setRange(0, 0)
            self._eta.setText("")

        rt = active.get("runtime") or {}
        rt_parts = [rt.get("phase", ""), rt.get("detail", "")]
        self._detail.setText(" · ".join(p for p in rt_parts if p))
        self._detail.setVisible(bool(self._detail.text()))

        can_pause = st == "running" and bool(active.get("pausable"))
        can_resume = st == "paused"
        can_cancel = st in {"running", "pausing", "paused"}
        self._pause_btn.setVisible(can_pause)
        self._resume_btn.setVisible(can_resume)
        self._cancel_btn.setVisible(can_cancel)
        return active


# ---------------- Floating Animated Toast Popup ----------------


class _ToastProgressLine(QWidget):
    """Subtle animated bottom countdown hairline inside the floating notification popup."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setFixedHeight(2)
        self._ratio = 1.0
        self._error = False

    def set_state(self, ratio: float, *, error: bool) -> None:
        self._ratio = max(0.0, min(1.0, float(ratio)))
        self._error = error
        self.update()

    def paintEvent(self, event) -> None:
        w, h = self.width(), self.height()
        if w <= 0 or h <= 0 or self._ratio <= 0.002:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        col = QColor(255, 255, 255, 130 if self._error else 105)
        p.setBrush(col)
        p.drawRoundedRect(QRectF(0.0, 0.0, w * self._ratio, float(h)), 1.0, 1.0)
        p.end()


class ToastBanner(QWidget):
    """Floating animated notification popup anchored over the main window (zero layout shift)."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("ToastOverlay")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setVisible(False)

        self._error = False
        self._current_text = ""
        self._hovered = False
        self._remaining_ms = 0
        self._total_ms = 3600
        self._closing = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 10, 14, 18)
        outer.setSpacing(0)

        self._card = QFrame(self)
        self._card.setObjectName("NoticeBar")
        self._card.setMinimumWidth(240)
        self._card.setMaximumWidth(500)

        card_lay = QVBoxLayout(self._card)
        card_lay.setContentsMargins(14, 10, 10, 8)
        card_lay.setSpacing(7)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)

        self._icon_lbl = QLabel(self._card)
        self._icon_lbl.setFixedSize(22, 22)
        self._icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._icon_lbl.setObjectName("ToastIconBadge")

        self._label = QLabel("", self._card)
        self._label.setWordWrap(True)
        self._label.setMinimumWidth(140)

        self._close_btn = QPushButton(self._card)
        self._close_btn.setProperty("compact", True)
        self._close_btn.setFixedSize(26, 26)
        self._close_btn.setToolTip("关闭提示")
        self._close_btn.clicked.connect(self.dismiss)

        row.addWidget(self._icon_lbl, 0, Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(self._label, 1, Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(self._close_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        card_lay.addLayout(row)

        self._progress = _ToastProgressLine(self._card)
        card_lay.addWidget(self._progress)

        outer.addWidget(self._card)

        self._opacity = QGraphicsOpacityEffect(self)
        self._opacity.setOpacity(1.0)
        self._opacity.setEnabled(False)
        self.setGraphicsEffect(self._opacity)

        self._anim_group: QParallelAnimationGroup | None = None
        self._countdown_anim = QVariantAnimation(self)
        self._countdown_anim.setEasingCurve(QEasingCurve.Type.Linear)
        self._countdown_anim.valueChanged.connect(
            lambda v: self._progress.set_state(float(v), error=self._error)
        )

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.dismiss)

        if parent is not None:
            parent.installEventFilter(self)

        self.refresh_theme()

    def refresh_theme(self) -> None:
        t = tokens(current_theme())
        ink = t["toast_ink"]
        self._close_btn.setIcon(studio_icon("close", ink, 13))
        ic_name = "alert" if self._error else "check"
        self._icon_lbl.setPixmap(icon_pixmap(ic_name, ink, size=14, dpr=2.0))

    def _target_geometry(self) -> tuple[QPoint, QSize]:
        pw = self.parentWidget().width() if self.parentWidget() is not None else 1100
        max_card_w = max(280, min(560, pw - 64))
        max_label_w = max(180, max_card_w - 92)
        text = self._label.text() or ""
        fm = self._label.fontMetrics()
        lines = text.splitlines() or [text]
        natural_w = max((fm.horizontalAdvance(ln) for ln in lines), default=160) + 12
        ideal_label_w = max(160, min(max_label_w, natural_w))
        self._card.setMaximumWidth(max_card_w)
        self._label.setFixedWidth(ideal_label_w)
        self._card.adjustSize()
        self.adjustSize()
        sz = self.sizeHint()
        x = max(8, (pw - sz.width()) // 2)
        y = 54
        return QPoint(x, y), sz

    def _reposition(self) -> None:
        if not self.isVisible() or self._closing:
            return
        pos, sz = self._target_geometry()
        self.resize(sz)
        self.move(pos)
        self.raise_()

    def _start_countdown(self, duration_ms: int) -> None:
        self._remaining_ms = max(400, int(duration_ms))
        self._countdown_anim.stop()
        if self._hovered:
            self._timer.stop()
            return
        ratio_start = self._remaining_ms / max(1.0, float(self._total_ms))
        self._countdown_anim.setDuration(self._remaining_ms)
        self._countdown_anim.setStartValue(ratio_start)
        self._countdown_anim.setEndValue(0.0)
        self._countdown_anim.start()
        self._timer.start(self._remaining_ms)

    def show_message(self, text: str, *, error: bool = False) -> None:
        clean = str(text or "").strip()
        if not clean:
            return
        same_visible = self.isVisible() and not self._closing and clean == self._current_text and error == self._error
        self._current_text = clean
        self._error = bool(error)
        self._closing = False
        self._total_ms = 8000 if self._error else 3400

        self._card.setProperty("error", self._error)
        self._card.style().unpolish(self._card)
        self._card.style().polish(self._card)
        self._label.setText(clean)
        self.refresh_theme()

        target_pos, sz = self._target_geometry()
        self.resize(sz)

        if same_visible:
            self.move(target_pos)
            self.raise_()
            self._start_countdown(self._total_ms)
            return

        if self._anim_group is not None:
            self._anim_group.stop()
            self._anim_group = None

        already_shown = self.isVisible()
        self.show()
        self.raise_()

        if already_shown:
            self.move(target_pos)
            self._opacity.setOpacity(1.0)
            self._opacity.setEnabled(False)
        else:
            start_pos = QPoint(target_pos.x(), target_pos.y() - 16)
            self.move(start_pos)
            self._opacity.setOpacity(0.0)
            self._opacity.setEnabled(True)
            grp = QParallelAnimationGroup(self)
            op = QPropertyAnimation(self._opacity, b"opacity", grp)
            op.setDuration(210)
            op.setStartValue(0.0)
            op.setEndValue(1.0)
            op.setEasingCurve(QEasingCurve.Type.OutCubic)
            sl = QPropertyAnimation(self, b"pos", grp)
            sl.setDuration(230)
            sl.setStartValue(start_pos)
            sl.setEndValue(target_pos)
            sl.setEasingCurve(QEasingCurve.Type.OutCubic)
            grp.addAnimation(op)
            grp.addAnimation(sl)

            def _enter_done() -> None:
                if not self._closing:
                    self._opacity.setOpacity(1.0)
                    self._opacity.setEnabled(False)
                if self._anim_group is grp:
                    self._anim_group = None

            grp.finished.connect(_enter_done)
            self._anim_group = grp
            grp.start()

        self._progress.set_state(1.0, error=self._error)
        self._start_countdown(self._total_ms)

    def dismiss(self) -> None:
        self._timer.stop()
        self._countdown_anim.stop()
        if not self.isVisible() or self._closing:
            return
        self._closing = True
        if self._anim_group is not None:
            self._anim_group.stop()
            self._anim_group = None
        start_op = self._opacity.opacity() if self._opacity.isEnabled() else 1.0
        self._opacity.setOpacity(start_op)
        self._opacity.setEnabled(True)
        cur_pos = self.pos()
        end_pos = QPoint(cur_pos.x(), cur_pos.y() - 10)
        grp = QParallelAnimationGroup(self)
        op = QPropertyAnimation(self._opacity, b"opacity", grp)
        op.setDuration(170)
        op.setStartValue(start_op)
        op.setEndValue(0.0)
        op.setEasingCurve(QEasingCurve.Type.InCubic)
        sl = QPropertyAnimation(self, b"pos", grp)
        sl.setDuration(170)
        sl.setStartValue(cur_pos)
        sl.setEndValue(end_pos)
        sl.setEasingCurve(QEasingCurve.Type.InCubic)
        grp.addAnimation(op)
        grp.addAnimation(sl)

        def _finish() -> None:
            self._closing = False
            self._anim_group = None
            self._opacity.setEnabled(False)
            self._opacity.setOpacity(1.0)
            super(ToastBanner, self).hide()

        grp.finished.connect(_finish)
        self._anim_group = grp
        grp.start()

    def paintEvent(self, event) -> None:
        # Multi-pass soft elevation shadow painted directly (avoids nested QGraphicsEffect contention)
        cr = self._card.geometry()
        if cr.width() > 4 and cr.height() > 4:
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.setPen(Qt.PenStyle.NoPen)
            for spread, dy, alpha in ((10, 7, 10), (7, 5, 14), (4, 3, 20), (2, 2, 26)):
                r = QRectF(
                    cr.x() - spread,
                    cr.y() - spread * 0.4 + dy,
                    cr.width() + spread * 2,
                    cr.height() + spread * 1.6,
                )
                p.setBrush(QColor(22, 16, 12, alpha))
                p.drawRoundedRect(r, 12.0 + spread * 0.5, 12.0 + spread * 0.5)
            p.end()
        super().paintEvent(event)

    def enterEvent(self, event) -> None:
        self._hovered = True
        if self._timer.isActive():
            rem = self._timer.remainingTime()
            if rem > 0:
                self._remaining_ms = rem
            self._timer.stop()
        self._countdown_anim.stop()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hovered = False
        if self.isVisible() and not self._closing:
            self._start_countdown(max(900, self._remaining_ms))
        super().leaveEvent(event)

    def stop_animations(self) -> None:
        self._timer.stop()
        self._countdown_anim.stop()
        if self._anim_group is not None:
            self._anim_group.stop()
            self._anim_group = None
        self._closing = False
        self._opacity.setEnabled(False)
        self._opacity.setOpacity(1.0)
        super().hide()

    def eventFilter(self, obj, event):  # noqa: N802 (Qt API)
        if obj is self.parentWidget():
            et = event.type()
            if et == QEvent.Type.Resize:
                self._reposition()
            elif et in (QEvent.Type.Close, QEvent.Type.Hide):
                self.stop_animations()
        return super().eventFilter(obj, event)
