"""Native PySide6 dialogs: Models, Downloads, Display, Updates, Characters, Queue, Imports, Board & Fusion."""

from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QDateTime, Qt, QTimer, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateTimeEdit,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .icons import studio_icon
from .scaling import APPLIED as SCALE_APPLIED
from .scaling import SCALE_CHOICES, auto_scale
from .theme import THEMES, tokens
from .widgets import (
    ROLE_LORA_FAV,
    attach_lora_star_delegate,
    character_matches_search,
    checkpoint_family_and_dir,
    lora_choice_name,
    lora_disambiguated_names,
    lora_display_name,
    lora_matches_search,
    run_async,
    sort_loras_favorites_first,
)

if TYPE_CHECKING:
    from ..app_core import StudioApp


# ==================== 1. DRAWING MODEL & KEEP-ALIVE ====================


class DrawingModelDialog(QDialog):
    job_started = Signal(str)

    def __init__(self, app: StudioApp, parent: QWidget | None = None):
        super().__init__(parent)
        self.app = app
        self.setWindowTitle("绘图模型与 ComfyUI 保活")
        self.resize(560, 320)

        lay = QVBoxLayout(self)
        lay.setSpacing(12)

        self._status_lbl = QLabel("")
        self._status_lbl.setObjectName("BadgeAccent")
        self._status_lbl.setWordWrap(True)
        lay.addWidget(self._status_lbl)

        self._keep_alive = QCheckBox("ComfyUI 常驻保活")
        self._keep_alive.setChecked(bool(app.comfy.keep_alive))
        self._keep_alive.toggled.connect(self._on_keep_alive)
        lay.addWidget(self._keep_alive)

        row = QHBoxLayout()
        row.addWidget(QLabel("预加载底模"))
        self._combo = QComboBox()
        self._combo.activated.connect(self._on_model_pick)
        row.addWidget(self._combo, 1)
        open_dir = QPushButton("打开目录")
        open_dir.clicked.connect(lambda: self._safe(lambda: self.app.action({"action": "open_drawing_model_folder", "checkpoint": self._combo.currentData()})))
        row.addWidget(open_dir)
        lay.addLayout(row)

        btns = QHBoxLayout()
        self._load_btn = QPushButton("预加载所选模型")
        self._load_btn.setProperty("primary", True)
        self._load_btn.clicked.connect(self._preload)
        self._unload_btn = QPushButton("卸载模型并释放显存")
        self._unload_btn.setProperty("danger", True)
        self._unload_btn.clicked.connect(self._unload)
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.accept)
        btns.addWidget(self._load_btn)
        btns.addWidget(self._unload_btn)
        btns.addStretch(1)
        btns.addWidget(close_btn)
        lay.addLayout(btns)

        self._msg = QLabel("")
        self._msg.setObjectName("Muted")
        self._msg.setWordWrap(True)
        lay.addWidget(self._msg)
        self._refresh()

    def _on_keep_alive(self, checked: bool) -> None:
        self.app.action({"action": "set_comfy_keep_alive", "enabled": bool(checked)})
        self._refresh()

    def _on_model_pick(self, _idx: int) -> None:
        cp = self._combo.currentData()
        if cp:
            self.app.remember_checkpoint(cp)

    def _refresh(self) -> None:
        snap = self.app.drawing_model_snapshot()
        cur = self._combo.currentData()
        self._combo.blockSignals(True)
        self._combo.clear()
        for m in snap.get("models") or []:
            self._combo.addItem(m["label"], m["value"])
        pick = cur or snap.get("loaded") or snap.get("preferred") or snap.get("default")
        idx = self._combo.findData(pick)
        if idx >= 0:
            self._combo.setCurrentIndex(idx)
        self._combo.blockSignals(False)
        loaded = snap.get("loaded")
        running = snap.get("comfy_running")
        if loaded:
            self._status_lbl.setText(f"已预加载常驻：{loaded}")
        elif running:
            self._status_lbl.setText("ComfyUI 引擎运行保活中")
        else:
            self._status_lbl.setText("ComfyUI 待命中")
        self._unload_btn.setEnabled(bool(loaded or running))

    def _preload(self) -> None:
        cp = self._combo.currentData()
        if not cp:
            return
        self.app.remember_checkpoint(cp)
        self._safe(lambda: self._emit_job(self.app.action({"action": "preload_drawing_model", "checkpoint": cp}), "已开始预加载模型"))

    def _unload(self) -> None:
        self._safe(lambda: self._emit_job(self.app.action({"action": "unload_drawing_model"}), "正在卸载绘图模型并释放显存"))

    def _emit_job(self, res: dict, msg: str) -> None:
        self._msg.setText(msg)
        if res.get("job"):
            self.job_started.emit(res["job"])
        self._refresh()

    def _safe(self, fn) -> None:
        try:
            fn()
        except Exception as exc:
            self._msg.setText(str(exc))


# ==================== 2. MODEL DOWNLOADS ====================


class ModelDownloadsDialog(QDialog):
    def __init__(self, app: StudioApp, parent: QWidget | None = None):
        super().__init__(parent)
        self.app = app
        self.setWindowTitle("模型下载与管理")
        self.resize(740, 520)

        lay = QVBoxLayout(self)
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._host = QWidget()
        self._list = QVBoxLayout(self._host)
        self._list.setSpacing(8)
        self._scroll.setWidget(self._host)
        lay.addWidget(self._scroll, 1)

        foot = QHBoxLayout()
        ref = QPushButton("刷新状态")
        ref.clicked.connect(self._refresh)
        close = QPushButton("关闭")
        close.clicked.connect(self.accept)
        foot.addWidget(ref)
        foot.addStretch(1)
        foot.addWidget(close)
        lay.addLayout(foot)

        self._timer = QTimer(self)
        self._timer.setInterval(2000)
        self._timer.timeout.connect(self._refresh)
        self._timer.start()
        self._refresh()

    def _refresh(self) -> None:
        while self._list.count():
            w = self._list.takeAt(0).widget()
            if w is not None:
                w.hide()
                w.setParent(None)
                w.deleteLater()
        snap = self.app.model_downloads.snapshot()
        for item in snap.get("items") or []:
            card = QFrame()
            card.setObjectName("Card")
            cl = QHBoxLayout(card)
            info = QVBoxLayout()
            title = QLabel(item.get("label") or item.get("id", ""))
            title.setStyleSheet("font-weight: 600;")
            st = item.get("status", "")
            msg = item.get("message") or ("已安装" if item.get("installed") else "未安装")
            sub = QLabel(f"{st} · {msg}")
            sub.setObjectName("Muted")
            info.addWidget(title)
            info.addWidget(sub)
            cl.addLayout(info, 1)

            open_b = QPushButton("打开目录")
            open_b.setProperty("compact", True)
            open_b.clicked.connect(lambda _=False, aid=item["id"]: self._act("open_model_folder", aid))
            cl.addWidget(open_b)
            if not item.get("installed"):
                dl_b = QPushButton("下载")
                dl_b.setProperty("compact", True)
                dl_b.setProperty("primary", True)
                dl_b.setEnabled(not item.get("busy"))
                dl_b.clicked.connect(lambda _=False, aid=item["id"]: self._act("download_model_asset", aid))
                cl.addWidget(dl_b)
            self._list.addWidget(card)
        self._list.addStretch(1)

    def _act(self, action: str, asset_id: str) -> None:
        try:
            self.app.action({"action": action, "asset": asset_id})
            self._refresh()
        except Exception as exc:
            QMessageBox.warning(self, "操作失败", str(exc))


# ==================== 3. DISPLAY SETTINGS ====================


class DisplaySettingsDialog(QDialog):
    theme_changed = Signal(str, bool)

    def __init__(self, app: StudioApp, parent: QWidget | None = None):
        super().__init__(parent)
        self.app = app
        self.setWindowTitle("界面显示与分析模型")
        self.resize(560, 440)

        lay = QVBoxLayout(self)
        lay.setSpacing(14)

        lay.addWidget(QLabel("界面缩放"))
        sc_row = QHBoxLayout()
        self._scale_combo = QComboBox()
        for val, lbl in SCALE_CHOICES:
            self._scale_combo.addItem(lbl, val)
        sidx = self._scale_combo.findData(str(app.ui_state.get("ui_scale", "auto")))
        self._scale_combo.setCurrentIndex(max(0, sidx))
        self._scale_combo.currentIndexChanged.connect(self._on_scale_changed)
        self._restart_btn = QPushButton("立即重启生效")
        self._restart_btn.setVisible(False)
        self._restart_btn.clicked.connect(self._restart_app)
        sc_row.addWidget(self._scale_combo, 1)
        sc_row.addWidget(self._restart_btn)
        lay.addLayout(sc_row)
        self._scale_info = QLabel(self._scale_summary())
        self._scale_info.setObjectName("Muted")
        self._scale_info.setWordWrap(True)
        lay.addWidget(self._scale_info)

        lay.addWidget(QLabel("字号缩放"))
        sz_row = QHBoxLayout()
        cur_large = app.ui_state.get("text_size", "standard") == "large"
        self._std_btn = QPushButton("标准")
        self._std_btn.setProperty("pill", True)
        self._std_btn.setProperty("active", not cur_large)
        self._lg_btn = QPushButton("大号")
        self._lg_btn.setProperty("pill", True)
        self._lg_btn.setProperty("active", cur_large)
        self._std_btn.clicked.connect(lambda: self._set_size(False))
        self._lg_btn.clicked.connect(lambda: self._set_size(True))
        sz_row.addWidget(self._std_btn)
        sz_row.addWidget(self._lg_btn)
        lay.addLayout(sz_row)

        lay.addWidget(QLabel("配色主题"))
        th_row = QHBoxLayout()
        cur_theme = str(app.ui_state.get("theme", "linen"))
        self._theme_btns: dict[str, QPushButton] = {}
        for k, meta in THEMES.items():
            b = QPushButton(meta["name"])
            b.setProperty("pill", True)
            b.setProperty("active", k == cur_theme)
            b.clicked.connect(lambda _=False, name=k: self._set_theme(name))
            self._theme_btns[k] = b
            th_row.addWidget(b)
        lay.addLayout(th_row)

        lay.addWidget(QLabel("本地分析模型"))
        self._model_combo = QComboBox()
        self._model_info = QLabel("")
        self._model_info.setObjectName("Muted")
        self._model_info.setWordWrap(True)
        lay.addWidget(self._model_combo)
        lay.addWidget(self._model_info)

        try:
            data = app.action({"action": "analysis_model"})
            for opt in data.get("options") or []:
                self._model_combo.addItem(opt["label"], opt["id"])
            idx = self._model_combo.findData(data.get("selected"))
            if idx >= 0:
                self._model_combo.setCurrentIndex(idx)
        except Exception as exc:
            self._model_info.setText(str(exc))
        self._model_combo.currentIndexChanged.connect(self._on_analysis_model_changed)

        lay.addStretch(1)
        close = QPushButton("完成")
        close.setProperty("primary", True)
        close.clicked.connect(self.accept)
        lay.addWidget(close, 0, Qt.AlignmentFlag.AlignRight)

    def _scale_summary(self) -> str:
        factor = float(SCALE_APPLIED.get("factor") or 1.0)
        area = SCALE_APPLIED.get("area")
        if area:
            w, h = area
            return f"当前生效 {factor * 100:.0f}% · 主屏可用区域 {w}×{h}（系统缩放后的逻辑像素）· 自动档会选 {auto_scale(w, h) * 100:.0f}%"
        return f"当前生效 {factor * 100:.0f}%"

    def _on_scale_changed(self) -> None:
        val = str(self._scale_combo.currentData() or "auto")
        self.app.ui_state.set("ui_scale", val)
        changed = val != str(SCALE_APPLIED.get("setting") or "auto")
        self._restart_btn.setVisible(changed)
        self._scale_info.setText("已保存，重启 LocalDraw 后生效。" if changed else self._scale_summary())

    def _restart_app(self) -> None:
        from PySide6.QtWidgets import QApplication

        qt_app = QApplication.instance()
        win = self.parent().window() if self.parent() is not None else None
        self.accept()
        if qt_app is None or win is None:
            return
        qt_app.setProperty("restart_requested", True)
        if not win.close():  # user kept running jobs
            qt_app.setProperty("restart_requested", False)

    def _set_size(self, large: bool) -> None:
        self.app.ui_state.set("text_size", "large" if large else "standard")
        for btn, state in ((self._std_btn, not large), (self._lg_btn, large)):
            btn.setProperty("active", state)
            btn.style().unpolish(btn)
            btn.style().polish(btn)
        self.theme_changed.emit(str(self.app.ui_state.get("theme", "linen")), large)

    def _set_theme(self, name: str) -> None:
        self.app.ui_state.set("theme", name)
        for k, b in self._theme_btns.items():
            b.setProperty("active", k == name)
            b.style().unpolish(b)
            b.style().polish(b)
        self.theme_changed.emit(name, self.app.ui_state.get("text_size", "standard") == "large")

    def _on_analysis_model_changed(self) -> None:
        mid = self._model_combo.currentData()
        if not mid:
            return
        try:
            self.app.action({"action": "set_analysis_model", "model": mid})
            self._model_info.setText("已切换分析模型：" + self._model_combo.currentText())
        except Exception as exc:
            self._model_info.setText(str(exc))


# ==================== 4. UPDATE DIALOG ====================


class UpdateDialog(QDialog):
    def __init__(self, app: StudioApp, parent: QWidget | None = None):
        super().__init__(parent)
        self.app = app
        self.setWindowTitle("检测版本更新")
        self.resize(500, 250)

        lay = QVBoxLayout(self)
        self._status = QLabel("正在检测最新版本…")
        self._status.setWordWrap(True)
        lay.addWidget(self._status)
        self._token = QLineEdit()
        self._token.setEchoMode(QLineEdit.EchoMode.Password)
        self._token.setPlaceholderText("可选：GitHub 只读令牌（私有仓库未登录 gh 时使用）")
        lay.addWidget(self._token)

        row = QHBoxLayout()
        check_b = QPushButton("重新检测")
        check_b.setProperty("primary", True)
        check_b.clicked.connect(self._check)
        rel_b = QPushButton("打开下载页")
        rel_b.clicked.connect(lambda: self.app.action({"action": "open_releases"}))
        close_b = QPushButton("关闭")
        close_b.clicked.connect(self.accept)
        row.addWidget(check_b)
        row.addWidget(rel_b)
        row.addStretch(1)
        row.addWidget(close_b)
        lay.addLayout(row)
        self._check()

    def _check(self) -> None:
        self._status.setText("正在查询版本信息…")
        tok = self._token.text().strip()
        run_async(
            lambda: self.app.action({"action": "check_update", "token": tok}),
            lambda r: self._status.setText(json.dumps(r, ensure_ascii=False, indent=2) if not r.get("message") else f"当前：{r.get('current', '')} · 最新：{r.get('latest', '')}\n{r.get('message', '')}"),
            lambda e: self._status.setText("检测失败：" + e),
        )


# ==================== 5. CHARACTER LIBRARY ====================


class CharacterLibraryDialog(QDialog):
    characters_updated = Signal()
    job_started = Signal(str)

    def __init__(self, app: StudioApp, parent: QWidget | None = None):
        super().__init__(parent)
        self.app = app
        self.setWindowTitle("角色库管理")
        self.resize(1020, 680)
        self._cards: list[dict] = []
        self._current: dict | None = None
        self._uploaded_image_id: str | None = None

        root = QHBoxLayout(self)
        # Left list
        left = QVBoxLayout()
        self._search = QLineEdit()
        self._search.setPlaceholderText("搜索角色（中文、拼音、英文）…")
        self._search.textChanged.connect(self._filter_list)
        new_btn = QPushButton("新建角色")
        new_btn.setIcon(studio_icon("plus", tokens()["accent_ink"], 14))
        new_btn.setProperty("primary", True)
        new_btn.clicked.connect(self._new_character)
        self._list = QListWidget()
        self._list.setUniformItemSizes(True)
        self._list.currentItemChanged.connect(self._on_select)
        left.addWidget(self._search)
        left.addWidget(new_btn)
        left.addWidget(self._list, 1)

        # Right editor
        right = QVBoxLayout()
        top = QHBoxLayout()
        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText("角色名称")
        upload_btn = QPushButton("上传参考图")
        upload_btn.clicked.connect(self._upload_image)
        ai_btn = QPushButton("AI 识别 / 整理角色卡")
        ai_btn.clicked.connect(self._ai_draft)
        top.addWidget(QLabel("角色名"))
        top.addWidget(self._name_edit, 1)
        top.addWidget(upload_btn)
        top.addWidget(ai_btn)
        right.addLayout(top)

        mid = QHBoxLayout()
        self._img_lbl = QLabel("暂无参考图")
        self._img_lbl.setFixedSize(160, 210)
        self._img_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._img_lbl.setObjectName("Card")
        mid.addWidget(self._img_lbl, 0, Qt.AlignmentFlag.AlignTop)

        self._profile_edit = QPlainTextEdit()
        self._profile_edit.setPlaceholderText("使用 ## 字段名 分节编辑（如 ## identity / ## hair / ## eyes / ## clothing / ## accessories / ## marks / ## personality）…")
        mid.addWidget(self._profile_edit, 1)
        right.addLayout(mid, 1)

        foot = QHBoxLayout()
        self._msg = QLabel("")
        self._msg.setObjectName("Muted")
        save_btn = QPushButton("保存角色卡")
        save_btn.setProperty("primary", True)
        save_btn.clicked.connect(self._save)
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.accept)
        foot.addWidget(self._msg, 1)
        foot.addWidget(save_btn)
        foot.addWidget(close_btn)
        right.addLayout(foot)

        root.addLayout(left, 1)
        root.addLayout(right, 3)
        self._reload()

    def _reload(self) -> None:
        self._cards = self.app.characters_data(compact=True).get("characters") or []
        self._filter_list()

    def _filter_list(self) -> None:
        q = self._search.text().strip()
        self._list.setUpdatesEnabled(False)
        try:
            self._list.clear()
            for c in self._cards:
                if character_matches_search(c, q):
                    it = QListWidgetItem(c["name"])
                    it.setData(Qt.ItemDataRole.UserRole, c)
                    self._list.addItem(it)
        finally:
            self._list.setUpdatesEnabled(True)

    def _new_character(self) -> None:
        self._current = None
        self._uploaded_image_id = None
        self._name_edit.setReadOnly(False)
        self._name_edit.clear()
        self._img_lbl.setPixmap(QPixmap())
        self._img_lbl.setText("暂无参考图")
        self._profile_edit.setPlainText(
            "## identity\n\n## hair\n\n## eyes\n\n## body\n\n## clothing\n\n## accessories\n\n## marks\n\n## personality\n"
        )
        self._msg.setText("新建角色模式")

    def _on_select(self, cur: QListWidgetItem | None, _prev) -> None:
        if not cur:
            return
        card = cur.data(Qt.ItemDataRole.UserRole)
        if not card:
            return
        self._current = card
        self._uploaded_image_id = card.get("image_id")
        self._name_edit.setText(card.get("name", ""))
        self._name_edit.setReadOnly(True)
        prof = card.get("profile") or {}
        lines = []
        for k in ("identity", "face", "hair", "eyes", "body", "clothing", "accessories", "palette", "marks", "personality", "voice", "background"):
            v = prof.get(k)
            if v is not None and v != "":
                lines.append(f"## {k}\n{v}")
        self._profile_edit.setPlainText("\n\n".join(lines))
        if self._uploaded_image_id:
            try:
                p = self.app.character_image_path(self._uploaded_image_id)
                pm = QPixmap(str(p)).scaled(152, 202, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
                self._img_lbl.setPixmap(pm)
                self._img_lbl.setText("")
            except Exception:
                self._img_lbl.setPixmap(QPixmap())
                self._img_lbl.setText("参考图缺失")
        else:
            self._img_lbl.setPixmap(QPixmap())
            self._img_lbl.setText("暂无参考图")

    def _upload_image(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "选择角色参考图", "", "图片 (*.png *.jpg *.jpeg *.webp)")
        if not path:
            return
        raw = Path(path).read_bytes()
        ext = Path(path).suffix.lower().lstrip(".") or "png"
        mime = "jpeg" if ext == "jpg" else ext
        data_url = f"data:image/{mime};base64," + base64.b64encode(raw).decode("ascii")
        try:
            res = self.app.action({"action": "character_upload", "data_url": data_url})
            self._uploaded_image_id = res["image_id"]
            pm = QPixmap(path).scaled(152, 202, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            self._img_lbl.setPixmap(pm)
            self._img_lbl.setText("")
            self._msg.setText("已上传参考图")
        except Exception as exc:
            self._msg.setText(str(exc))

    def _parse_profile(self) -> dict:
        base = dict((self._current or {}).get("profile") or {})
        cur_k = None
        buf: list[str] = []
        for line in self._profile_edit.toPlainText().splitlines():
            if line.startswith("## "):
                if cur_k:
                    base[cur_k] = "\n".join(buf).strip()
                cur_k = line[3:].strip()
                buf = []
            else:
                buf.append(line)
        if cur_k:
            base[cur_k] = "\n".join(buf).strip()
        for k in ("identity", "face", "hair", "eyes", "body", "clothing", "accessories", "palette", "marks", "personality", "voice", "motivation", "background", "relationships", "constraints"):
            base.setdefault(k, "unknown")
        base.setdefault("observed", [])
        base.setdefault("suggested", [])
        base.setdefault("unknowns", [])
        return base

    def _save(self) -> None:
        name = self._name_edit.text().strip()
        if not name:
            self._msg.setText("请填写角色名称")
            return
        try:
            self.app.action({
                "action": "character_save",
                "name": name,
                "profile": self._parse_profile(),
                "source": (self._current or {}).get("source") or {"mode": "text", "text": name},
                "image_id": self._uploaded_image_id,
                "base_revision": (self._current or {}).get("revision", 0),
            })
            self._msg.setText(f"已保存角色：{name}")
            self._reload()
            self.characters_updated.emit()
        except Exception as exc:
            self._msg.setText(str(exc))

    def _ai_draft(self) -> None:
        name = self._name_edit.text().strip()
        if not name:
            self._msg.setText("请先填角色名再调用 AI 识别/整理")
            return
        try:
            res = self.app.action({
                "action": "character_draft",
                "name": name,
                "mode": "image" if self._uploaded_image_id else "text",
                "image_id": self._uploaded_image_id,
                "text": self._profile_edit.toPlainText(),
            })
            self._msg.setText("已提交建卡任务")
            if res.get("job"):
                self.job_started.emit(res["job"])
        except Exception as exc:
            self._msg.setText(str(exc))


# ==================== 6. NIGHT QUEUE TIMELINE ====================


class NightQueueDialog(QDialog):
    open_project_requested = Signal(str)
    add_prompt_task_requested = Signal()
    open_fusion_requested = Signal()

    def __init__(self, app: StudioApp, parent: QWidget | None = None):
        super().__init__(parent)
        self.app = app
        self.setWindowTitle("创作任务时间轴（夜间任务列表）")
        self.resize(880, 680)

        lay = QVBoxLayout(self)
        lay.setSpacing(10)

        head = QHBoxLayout()
        t = QLabel("创作任务时间轴")
        t.setObjectName("StageTitle")
        add_p = QPushButton("添加提示词任务")
        add_p.setIcon(studio_icon("plus", tokens()["muted"], 14))
        add_p.setProperty("primary", True)
        add_p.clicked.connect(lambda: (self.accept(), self.add_prompt_task_requested.emit()))
        fus_b = QPushButton("融合任务")
        fus_b.clicked.connect(lambda: (self.accept(), self.open_fusion_requested.emit()))
        head.addWidget(t)
        head.addStretch(1)
        head.addWidget(add_p)
        head.addWidget(fus_b)
        lay.addLayout(head)

        # Controls
        ctrl = QFrame()
        ctrl.setObjectName("Card")
        cl = QHBoxLayout(ctrl)
        self._stop_err = QCheckBox("遇错暂停")
        self._stop_err.setChecked(True)
        self._use_sched = QCheckBox("预约开始")
        self._sched_dt = QDateTimeEdit(QDateTime.currentDateTime().addSecs(3600))
        self._sched_dt.setDisplayFormat("yyyy-MM-dd HH:mm")
        self._sched_dt.setEnabled(False)
        self._use_sched.toggled.connect(self._sched_dt.setEnabled)
        one_h = QPushButton("1小时后")
        one_h.setProperty("compact", True)
        one_h.clicked.connect(lambda: (self._use_sched.setChecked(True), self._sched_dt.setDateTime(QDateTime.currentDateTime().addSecs(3600))))
        start_b = QPushButton("开始按顺序执行")
        start_b.setProperty("primary", True)
        start_b.clicked.connect(self._start_queue)
        pause_b = QPushButton("做完当前项暂停")
        pause_b.clicked.connect(lambda: self._act({"action": "pause_queue"}))
        clear_h = QPushButton("清理已结束")
        clear_h.setProperty("compact", True)
        clear_h.clicked.connect(lambda: self._act({"action": "clear_queue_history"}))
        cl.addWidget(self._stop_err)
        cl.addWidget(self._use_sched)
        cl.addWidget(self._sched_dt)
        cl.addWidget(one_h)
        cl.addStretch(1)
        cl.addWidget(start_b)
        cl.addWidget(pause_b)
        cl.addWidget(clear_h)
        lay.addWidget(ctrl)

        self._summary = QLabel("")
        self._summary.setObjectName("Muted")
        lay.addWidget(self._summary)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self._host = QWidget()
        self._items_lay = QVBoxLayout(self._host)
        self._items_lay.setSpacing(8)
        scroll.setWidget(self._host)
        lay.addWidget(scroll, 1)

        close_b = QPushButton("关闭")
        close_b.clicked.connect(self.accept)
        lay.addWidget(close_b, 0, Qt.AlignmentFlag.AlignRight)

        self._timer = QTimer(self)
        self._timer.setInterval(1500)
        self._timer.timeout.connect(self._refresh)
        self._timer.start()
        self._refresh()

    def _start_queue(self) -> None:
        start_at = self._sched_dt.dateTime().toSecsSinceEpoch() if self._use_sched.isChecked() else None
        self._act({"action": "start_queue", "stop_on_error": self._stop_err.isChecked(), "start_at": start_at})

    def _act(self, payload: dict) -> None:
        try:
            self.app.action(payload)
            self._refresh()
        except Exception as exc:
            QMessageBox.warning(self, "队列操作失败", str(exc))

    def _refresh(self) -> None:
        while self._items_lay.count():
            w = self._items_lay.takeAt(0).widget()
            if w is not None:
                w.hide()
                w.setParent(None)
                w.deleteLater()
        snap = self.app.night_queue.snapshot()
        items = snap.get("items") or []
        state = snap.get("status", "idle")
        self._summary.setText(f"队列状态：{state} · 共 {len(items)} 项任务")
        if not items:
            empty = QLabel("队列为空")
            empty.setObjectName("Muted")
            self._items_lay.addWidget(empty)
            self._items_lay.addStretch(1)
            return

        for idx, it in enumerate(items, 1):
            card = QFrame()
            card.setObjectName("JobBanner" if it.get("status") == "running" else "Card")
            hl = QHBoxLayout(card)
            num = QLabel(f"{idx:02d}")
            num.setStyleSheet("font-size: 16px; font-weight: 700; min-width: 30px;")
            info = QVBoxLayout()
            title = QLabel(f"[{it.get('status')}] {it.get('label', it.get('id'))}")
            title.setStyleSheet("font-weight: 600;")
            req = it.get("request") or {}
            sub = QLabel(f"模型：{req.get('checkpoint', '默认')} · 每格 {req.get('candidate_count', '')} 张 · {it.get('message', '')}")
            sub.setObjectName("Muted")
            info.addWidget(title)
            info.addWidget(sub)
            hl.addWidget(num)
            hl.addLayout(info, 1)

            iid = it["id"]
            if it.get("status") == "waiting":
                up = QPushButton("上移")
                up.setProperty("compact", True)
                up.clicked.connect(lambda _=False, x=iid: self._act({"action": "reorder_queue", "item": x, "direction": "up"}))
                dn = QPushButton("下移")
                dn.setProperty("compact", True)
                dn.clicked.connect(lambda _=False, x=iid: self._act({"action": "reorder_queue", "item": x, "direction": "down"}))
                hl.addWidget(up)
                hl.addWidget(dn)
            if it.get("status") in ("failed", "partial", "interrupted"):
                rt = QPushButton("重新排队")
                rt.setProperty("compact", True)
                rt.setProperty("primary", True)
                rt.clicked.connect(lambda _=False, x=iid: self._act({"action": "retry_queue_item", "item": x}))
                hl.addWidget(rt)
            op = QPushButton("打开作品")
            op.setProperty("compact", True)
            op.clicked.connect(lambda _=False, pid=it.get("project", ""): (self.accept(), self.open_project_requested.emit(pid)))
            rm = QPushButton("移除")
            rm.setProperty("compact", True)
            rm.setProperty("danger", True)
            rm.clicked.connect(lambda _=False, x=iid: self._act({"action": "remove_queue_item", "item": x}))
            hl.addWidget(op)
            hl.addWidget(rm)
            self._items_lay.addWidget(card)
        self._items_lay.addStretch(1)


# ==================== 7. PROJECT MANAGER & TRASH ====================


class ProjectManagerDialog(QDialog):
    projects_changed = Signal()

    def __init__(self, app: StudioApp, parent: QWidget | None = None):
        super().__init__(parent)
        self.app = app
        self.setWindowTitle("作品管理与回收站")
        self.resize(700, 520)

        lay = QVBoxLayout(self)
        tabs = QTabWidget()
        # Active projects tab
        t1 = QWidget()
        l1 = QVBoxLayout(t1)
        self._search = QLineEdit()
        self._search.setPlaceholderText("搜索作品…")
        self._search.textChanged.connect(self._load_active)
        self._active_list = QListWidget()
        del_btn = QPushButton("删除所选作品（移入回收站）")
        del_btn.setProperty("danger", True)
        del_btn.clicked.connect(self._delete_selected)
        l1.addWidget(self._search)
        l1.addWidget(self._active_list, 1)
        l1.addWidget(del_btn)
        tabs.addTab(t1, "已保存作品")

        # Trash tab
        t2 = QWidget()
        l2 = QVBoxLayout(t2)
        self._trash_list = QListWidget()
        res_btn = QPushButton("恢复选中作品")
        res_btn.setProperty("primary", True)
        res_btn.clicked.connect(self._restore_selected)
        l2.addWidget(self._trash_list, 1)
        l2.addWidget(res_btn)
        tabs.addTab(t2, "回收站")

        lay.addWidget(tabs, 1)
        close = QPushButton("关闭")
        close.clicked.connect(self.accept)
        lay.addWidget(close, 0, Qt.AlignmentFlag.AlignRight)

        self._load_active()
        self._load_trash()

    def _load_active(self) -> None:
        q = self._search.text().strip().lower()
        self._active_list.clear()
        for p in self.app.repo.listing():
            if q and q not in str(p.get("title", "")).lower():
                continue
            it = QListWidgetItem(f"{p.get('title', p['id'])}  （{'提示词作品' if p.get('quick_sample') else str(p.get('pages',0))+' 页分镜'}）")
            it.setCheckState(Qt.CheckState.Unchecked)
            it.setData(Qt.ItemDataRole.UserRole, p)
            self._active_list.addItem(it)

    def _load_trash(self) -> None:
        self._trash_list.clear()
        for t in self.app.trash_state():
            it = QListWidgetItem(f"{t.get('title', t['id'])} · {t.get('deleted_at', '')}")
            it.setData(Qt.ItemDataRole.UserRole, t["id"])
            self._trash_list.addItem(it)

    def _delete_selected(self) -> None:
        items = []
        for i in range(self._active_list.count()):
            it = self._active_list.item(i)
            if it.checkState() == Qt.CheckState.Checked:
                p = it.data(Qt.ItemDataRole.UserRole)
                items.append({"id": p["id"], "base_hash": p["hash"]})
        if not items:
            return
        if QMessageBox.question(self, "确认删除", f"确定将选中的 {len(items)} 个作品移入回收站？") != QMessageBox.StandardButton.Yes:
            return
        try:
            self.app.action({"action": "delete_projects", "confirm": True, "items": items})
            self._load_active()
            self._load_trash()
            self.projects_changed.emit()
        except Exception as exc:
            QMessageBox.warning(self, "删除失败", str(exc))

    def _restore_selected(self) -> None:
        cur = self._trash_list.currentItem()
        if not cur:
            return
        try:
            self.app.action({"action": "restore_deleted_project", "project": cur.data(Qt.ItemDataRole.UserRole)})
            self._load_active()
            self._load_trash()
            self.projects_changed.emit()
        except Exception as exc:
            QMessageBox.warning(self, "恢复失败", str(exc))


# ==================== 8. DIRECT PROMPT IMPORT & SETUP ====================


class ImportedSetupDialog(QDialog):
    drawing_started = Signal(str, str)  # pid, job_id

    def __init__(self, app: StudioApp, pid: str, bundle_id: str, panels: list[dict], parent: QWidget | None = None):
        super().__init__(parent)
        self.app = app
        self.pid = pid
        self.bundle_id = bundle_id
        self.panels = panels
        self.setWindowTitle(f"逐格出图设置 · 共 {len(panels)} 格")
        self.resize(960, 700)

        self._opts = app.drawing_options(for_import=True)
        self._chars = app.characters_data(compact=True).get("characters") or []
        self._sel_loras: dict[str, float] = {}

        lay = QVBoxLayout(self)
        top = QGridLayout()
        self._model = QComboBox()
        diff = self._opts.get("diffusion_models") or {}
        for c in (self._opts.get("comfy") or {}).get("checkpoints") or []:
            if (diff.get(c) or {}).get("family") != "qwen_edit":
                self._model.addItem((diff.get(c) or {}).get("label") or c, c)
        pref_cp = (
            self._opts.get("selected_checkpoint")
            or self._opts.get("preloaded_checkpoint")
            or self._opts.get("preferred_checkpoint")
            or (self._opts.get("comfy") or {}).get("configured")
        )
        idx = self._model.findData(pref_cp)
        if idx >= 0:
            self._model.setCurrentIndex(idx)
        self._model.currentIndexChanged.connect(self._on_model_changed)

        self._count = QSpinBox()
        self._count.setRange(1, 16)
        self._count.setValue(2)
        self._count.setSuffix(" 张/格")
        self._common_neg = QLineEdit()
        self._common_neg.setPlaceholderText("通用负面提示词（可选）")
        self._ref_prev = QCheckBox("自动参考上一格（Anima Delta v6）")

        top.addWidget(QLabel("底模"), 0, 0)
        top.addWidget(self._model, 0, 1)
        top.addWidget(QLabel("张数"), 0, 2)
        top.addWidget(self._count, 0, 3)
        top.addWidget(QLabel("通用负面词"), 1, 0)
        top.addWidget(self._common_neg, 1, 1, 1, 3)
        top.addWidget(self._ref_prev, 2, 0, 1, 4)
        lay.addLayout(top)

        # Split: Chars | LoRAs
        split = QSplitter(Qt.Orientation.Horizontal)
        cbox = QWidget()
        cl = QVBoxLayout(cbox)
        cl.addWidget(QLabel("角色（最多 4 个）"))
        self._char_search = QLineEdit()
        self._char_search.setPlaceholderText("筛选角色…")
        self._char_search.textChanged.connect(self._refresh_chars)
        self._char_list = QListWidget()
        self._char_list.setUniformItemSizes(True)
        cl.addWidget(self._char_search)
        cl.addWidget(self._char_list)
        split.addWidget(cbox)

        lbox = QWidget()
        ll = QVBoxLayout(lbox)
        ll.addWidget(QLabel("LoRA（最多 5 个）"))
        self._lora_search = QLineEdit()
        self._lora_search.setPlaceholderText("筛选 LoRA…")
        self._lora_search.textChanged.connect(self._refresh_loras)
        self._lora_list = QListWidget()
        self._lora_list.setUniformItemSizes(True)
        self._lora_list.itemChanged.connect(self._on_lora_check)
        attach_lora_star_delegate(self._lora_list, self._toggle_favorite_lora)
        ll.addWidget(self._lora_search)
        ll.addWidget(self._lora_list)
        split.addWidget(lbox)
        lay.addWidget(split, 1)

        foot = QHBoxLayout()
        start_b = QPushButton("开始批量生成")
        start_b.setProperty("primary", True)
        start_b.clicked.connect(lambda: self._submit(queued=False))
        q_b = QPushButton("加入夜间任务列表")
        q_b.clicked.connect(lambda: self._submit(queued=True))
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        foot.addStretch(1)
        foot.addWidget(cancel)
        foot.addWidget(q_b)
        foot.addWidget(start_b)
        lay.addLayout(foot)

        self._refresh_chars()
        self._refresh_loras()

    def _refresh_chars(self) -> None:
        q = self._char_search.text().strip()
        checked = {self._char_list.item(i).data(Qt.ItemDataRole.UserRole)["name"] for i in range(self._char_list.count()) if self._char_list.item(i).checkState() == Qt.CheckState.Checked}
        self._char_list.setUpdatesEnabled(False)
        try:
            self._char_list.clear()
            for c in self._chars:
                if character_matches_search(c, q):
                    it = QListWidgetItem(c["name"])
                    it.setCheckState(Qt.CheckState.Checked if c["name"] in checked else Qt.CheckState.Unchecked)
                    it.setData(Qt.ItemDataRole.UserRole, c)
                    self._char_list.addItem(it)
        finally:
            self._char_list.setUpdatesEnabled(True)

    def _on_model_changed(self) -> None:
        cp = str(self._model.currentData() or "")
        if cp:
            self.app.remember_checkpoint(cp)
        self._refresh_loras()

    def _toggle_favorite_lora(self, file: str) -> None:
        self.app.ui_state.toggle_favorite_lora(file)
        self._refresh_loras()

    def _refresh_loras(self) -> None:
        cp = str(self._model.currentData() or "")
        diff = self._opts.get("diffusion_models") or {}
        fam, folder = checkpoint_family_and_dir(cp, diff)
        insp = self._opts.get("lora_inspection") or {}
        q = self._lora_search.text().strip()
        family_loras = [
            f
            for f in ((self._opts.get("comfy") or {}).get("loras") or [])
            if f.startswith(folder + "/") and (insp.get(f) or {}).get("family") == fam
        ]
        label_map = lora_disambiguated_names(family_loras)
        favs = self.app.ui_state.favorite_loras()
        fav_set = set(favs)
        matches = [f for f in family_loras if lora_matches_search(f, insp.get(f) or {}, q)]
        matches = sort_loras_favorites_first(matches, favs)

        self._lora_list.blockSignals(True)
        self._lora_list.setUpdatesEnabled(False)
        try:
            self._lora_list.clear()
            for f in matches:
                w = self._sel_loras.get(f, self.app.ui_state.lora_weights().get(f, 0.7))
                it = QListWidgetItem(f"{label_map.get(f, lora_choice_name(f))}  (权重 {w:.2f})")
                it.setCheckState(Qt.CheckState.Checked if f in self._sel_loras else Qt.CheckState.Unchecked)
                it.setData(Qt.ItemDataRole.UserRole, f)
                it.setData(ROLE_LORA_FAV, f in fav_set)
                it.setToolTip(f)
                self._lora_list.addItem(it)
        finally:
            self._lora_list.setUpdatesEnabled(True)
            self._lora_list.blockSignals(False)

    def _on_lora_check(self, item: QListWidgetItem) -> None:
        f = item.data(Qt.ItemDataRole.UserRole)
        if not f:
            return
        if item.checkState() == Qt.CheckState.Checked:
            self._sel_loras[f] = float(self.app.ui_state.lora_weights().get(f, 0.7))
        else:
            self._sel_loras.pop(f, None)

    def _submit(self, *, queued: bool) -> None:
        cp = str(self._model.currentData() or "")
        chars = []
        for i in range(self._char_list.count()):
            it = self._char_list.item(i)
            if it.checkState() == Qt.CheckState.Checked:
                c = it.data(Qt.ItemDataRole.UserRole)
                chars.append({"name": c["name"], "revision": c["revision"]})
        loras = [{"file": f, "weight": w} for f, w in self._sel_loras.items()]
        setup = {
            "characters": chars[:4],
            "loras": loras[:5],
            "common_negative": self._common_neg.text(),
            "reference_previous_panel": self._ref_prev.isChecked(),
            "counts": {p["panel_id"]: self._count.value() for p in self.panels},
            "positive_by_panel": {},
            "negative_by_panel": {},
        }
        try:
            res = self.app.action({
                "action": "queue_imported" if queued else "draw_imported",
                "project": self.pid,
                "bundle": self.bundle_id,
                "checkpoint": cp,
                "candidate_count": self._count.value(),
                "import_setup": setup,
            })
            self.accept()
            if not queued and res.get("job"):
                self.drawing_started.emit(self.pid, res["job"])
        except Exception as exc:
            QMessageBox.warning(self, "提交失败", str(exc))


class DirectImportDialog(QDialog):
    imported = Signal(str, str, list)  # pid, bundle_id, panels

    def __init__(self, app: StudioApp, *, storyboard_mode: bool = False, parent: QWidget | None = None):
        super().__init__(parent)
        self.app = app
        self._sb_mode = storyboard_mode
        self.setWindowTitle("直接导入分镜" if storyboard_mode else "直接导入每格提示词")
        self.resize(780, 560)
        self._preview: dict | None = None

        lay = QVBoxLayout(self)
        top = QHBoxLayout()
        self._title = QLineEdit()
        self._title.setPlaceholderText("作品标题（可选）")
        load_file = QPushButton("从文件加载 (.json / .txt)")
        load_file.clicked.connect(self._load_file)
        top.addWidget(QLabel("标题"))
        top.addWidget(self._title, 1)
        top.addWidget(load_file)
        lay.addLayout(top)

        self._text = QPlainTextEdit()
        self._text.setPlaceholderText(
            '粘贴每行一格提示词，或 JSON 数组：\n[{"id":"P001-K01","visual_prompt":"...","negative":"..."}]'
        )
        lay.addWidget(self._text, 1)

        self._info = QLabel("")
        self._info.setObjectName("Muted")
        self._info.setWordWrap(True)
        lay.addWidget(self._info)

        foot = QHBoxLayout()
        prev_b = QPushButton("预览并检查")
        prev_b.clicked.connect(self._do_preview)
        self._save_b = QPushButton("确认导入并设置出图" if not storyboard_mode else "确认导入分镜")
        self._save_b.setProperty("primary", True)
        self._save_b.setEnabled(False)
        self._save_b.clicked.connect(self._do_save)
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        foot.addWidget(prev_b)
        foot.addStretch(1)
        foot.addWidget(cancel)
        foot.addWidget(self._save_b)
        lay.addLayout(foot)

    def _load_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "选择导入文件", "", "提示词文件 (*.json *.txt);;所有文件 (*)")
        if path:
            self._text.setPlainText(Path(path).read_text(encoding="utf-8-sig", errors="replace"))
            if not self._title.text().strip():
                self._title.setText(Path(path).stem)
            self._do_preview()

    def _do_preview(self) -> None:
        act = "preview_direct_story" if self._sb_mode else "preview_direct_prompts"
        try:
            res = self.app.action({"action": act, "text": self._text.toPlainText(), "title": self._title.text().strip()})
            self._preview = res
            cnt = len(res.get("panels") or res.get("pages") or [])
            self._info.setText(f"校验通过：共识别 {cnt} 项内容。")
            self._save_b.setEnabled(True)
        except Exception as exc:
            self._preview = None
            self._save_b.setEnabled(False)
            self._info.setText(f"校验未通过：{exc}")

    def _do_save(self) -> None:
        if not self._preview:
            return
        act = "save_direct_story" if self._sb_mode else "save_direct_prompts"
        try:
            res = self.app.action({
                "action": act,
                "text": self._text.toPlainText(),
                "title": self._title.text().strip(),
                "confirm": True,
                "preview_hash": self._preview.get("preview_hash"),
            })
            pid = res["project"]
            bundle_id = res.get("bundle", "")
            panels = res.get("panels") or []
            self.accept()
            self.imported.emit(pid, bundle_id, panels)
        except Exception as exc:
            QMessageBox.warning(self, "导入失败", str(exc))


# ==================== 9. PROMPT BOARD START DIALOG ====================


class PromptBoardStartDialog(QDialog):
    board_ready = Signal(str)

    def __init__(self, app: StudioApp, parent: QWidget | None = None):
        super().__init__(parent)
        self.app = app
        self.setWindowTitle("创建分镜出图作品")
        self.resize(620, 400)
        self._job_id: str | None = None

        lay = QVBoxLayout(self)
        self._title = QLineEdit()
        self._title.setPlaceholderText("作品标题（必填）")
        self._idea = QPlainTextEdit()
        self._idea.setPlaceholderText("输入故事构想或剧情梗概（可选）…")
        self._pages = QSpinBox()
        self._pages.setRange(1, 20)
        self._pages.setValue(2)
        self._pages.setSuffix(" 页")

        lay.addWidget(QLabel("作品标题"))
        lay.addWidget(self._title)
        lay.addWidget(QLabel("故事构想"))
        lay.addWidget(self._idea, 1)
        prow = QHBoxLayout()
        prow.addWidget(QLabel("期望页数"))
        prow.addWidget(self._pages)
        prow.addStretch(1)
        lay.addLayout(prow)

        self._status = QLabel("")
        self._status.setObjectName("Muted")
        lay.addWidget(self._status)

        foot = QHBoxLayout()
        self._gen_btn = QPushButton("AI 生成分镜")
        self._gen_btn.setProperty("primary", True)
        self._gen_btn.clicked.connect(self._generate)
        self._confirm_btn = QPushButton("确认并进入分镜出图")
        self._confirm_btn.setProperty("primary", True)
        self._confirm_btn.setVisible(False)
        self._confirm_btn.clicked.connect(self._confirm)
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        foot.addStretch(1)
        foot.addWidget(cancel)
        foot.addWidget(self._gen_btn)
        foot.addWidget(self._confirm_btn)
        lay.addLayout(foot)

        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._poll)

    def _generate(self) -> None:
        title = self._title.text().strip()
        if not title:
            self._status.setText("请先填写作品标题")
            return
        try:
            res = self.app.action({
                "action": "generate_prompt_board",
                "title": title,
                "idea": self._idea.toPlainText().strip(),
                "page_count": self._pages.value(),
            })
            self._job_id = res["job"]
            self._gen_btn.setEnabled(False)
            self._status.setText("正在生成分镜…")
            self._timer.start()
        except Exception as exc:
            self._status.setText(str(exc))

    def _poll(self) -> None:
        if not self._job_id:
            return
        job = self.app.jobs.get(self._job_id)
        self._status.setText(f"{job.get('status')} · {job.get('message', '')}")
        if job.get("status") == "done":
            self._timer.stop()
            self._confirm_btn.setVisible(True)
            self._status.setText("分镜已生成完毕，点击「确认并进入分镜出图」。")
        elif job.get("status") in ("failed", "cancelled"):
            self._timer.stop()
            self._gen_btn.setEnabled(True)

    def _confirm(self) -> None:
        try:
            res = self.app.action({"action": "confirm_prompt_board", "job": self._job_id})
            self.accept()
            self.board_ready.emit(res["project"])
        except Exception as exc:
            self._status.setText(str(exc))


# ==================== 10. TASK FUSION ====================


class TaskFusionDialog(QDialog):
    fused = Signal(str, str, list)

    def __init__(self, app: StudioApp, parent: QWidget | None = None):
        super().__init__(parent)
        self.app = app
        self.setWindowTitle("融合任务（拼接多部作品）")
        self.resize(740, 520)

        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("勾选至少 2 部已有作品，按顺序融合为独立新作品后统一设置出图："))
        self._title = QLineEdit("融合任务")
        lay.addWidget(self._title)
        self._list = QListWidget()
        for p in app.repo.listing():
            it = QListWidgetItem(f"{p.get('title', p['id'])} ({p.get('pages', 0)} 页)")
            it.setCheckState(Qt.CheckState.Unchecked)
            it.setData(Qt.ItemDataRole.UserRole, p)
            self._list.addItem(it)
        lay.addWidget(self._list, 1)

        foot = QHBoxLayout()
        run_b = QPushButton("保存融合并设置出图")
        run_b.setProperty("primary", True)
        run_b.clicked.connect(self._fuse)
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        foot.addStretch(1)
        foot.addWidget(cancel)
        foot.addWidget(run_b)
        lay.addLayout(foot)

    def _fuse(self) -> None:
        chosen = []
        for i in range(self._list.count()):
            it = self._list.item(i)
            if it.checkState() == Qt.CheckState.Checked:
                chosen.append(it.data(Qt.ItemDataRole.UserRole))
        if len(chosen) < 2:
            QMessageBox.information(self, "提示", "请至少勾选 2 部作品进行融合。")
            return
        try:
            panels_out = []
            for p in chosen:
                src = self.app.action({"action": "fusion_source", "kind": "project", "source_id": p["id"]})
                for row in src.get("panels") or []:
                    panels_out.append({
                        "positive": row.get("positive", ""),
                        "negative": row.get("negative", ""),
                        "aspect_ratio": row.get("aspect_ratio", "3:2"),
                        "summary": row.get("summary", ""),
                    })
            res = self.app.action({
                "action": "save_prompt_fusion",
                "title": self._title.text().strip() or "融合任务",
                "panels": panels_out,
            })
            self.accept()
            self.fused.emit(res["project"], res.get("bundle", ""), res.get("panels") or [])
        except Exception as exc:
            QMessageBox.warning(self, "融合失败", str(exc))


# ==================== 11. STORY IMPORT ====================


class StoryImportDialog(QDialog):
    story_imported = Signal(str)

    def __init__(self, app: StudioApp, parent: QWidget | None = None):
        super().__init__(parent)
        self.app = app
        self.setWindowTitle("导入完整剧本 JSON")
        self.resize(700, 480)
        self._doc: dict | None = None

        lay = QVBoxLayout(self)
        load_b = QPushButton("选择剧本 JSON 文件")
        load_b.clicked.connect(self._load)
        lay.addWidget(load_b, 0, Qt.AlignmentFlag.AlignLeft)
        self._text = QPlainTextEdit()
        self._text.setPlaceholderText("粘贴完整剧本 JSON…")
        lay.addWidget(self._text, 1)
        self._status = QLabel("")
        self._status.setObjectName("Muted")
        lay.addWidget(self._status)

        foot = QHBoxLayout()
        imp_b = QPushButton("导入剧本")
        imp_b.setProperty("primary", True)
        imp_b.clicked.connect(self._import)
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        foot.addStretch(1)
        foot.addWidget(cancel)
        foot.addWidget(imp_b)
        lay.addLayout(foot)

    def _load(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "选择剧本 JSON", "", "JSON (*.json)")
        if path:
            self._text.setPlainText(Path(path).read_text(encoding="utf-8-sig", errors="replace"))

    def _import(self) -> None:
        try:
            doc = json.loads(self._text.toPlainText())
            prev = self.app.action({"action": "preview_story_import", "document": doc})
            res = self.app.action({"action": "save_story_import", "document": doc, "confirm": True, "preview_hash": prev.get("preview_hash")})
            self.accept()
            self.story_imported.emit(res["project"])
        except Exception as exc:
            self._status.setText(str(exc))
