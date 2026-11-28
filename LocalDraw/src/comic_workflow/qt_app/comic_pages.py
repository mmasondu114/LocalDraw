"""Native PySide6 Comic Workflow Stages: 01 Story, 02 Storyboard, 03 Drawing, 04 Problems, 05 Finished."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QIcon, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from .icons import studio_icon
from .theme import tokens
from .widgets import (
    ROLE_LORA_FAV,
    ImageLightboxDialog,
    ImageSizeWidget,
    ZoomableImageView,
    attach_lora_star_delegate,
    checkpoint_family_and_dir,
    image_cache,
    lora_choice_name,
    lora_disambiguated_names,
    lora_matches_search,
    sort_loras_favorites_first,
)

if TYPE_CHECKING:
    from ..app_core import StudioApp


# ==================== 01 STORY & CHARACTERS ====================


class StoryPage(QWidget):
    notify_requested = Signal(str, bool)
    project_created = Signal(str)
    job_started = Signal(str)

    def __init__(self, app: StudioApp, parent: QWidget | None = None):
        super().__init__(parent)
        self.app = app
        self._draft_result: dict | None = None
        self._pid: str | None = None

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        split = QSplitter(Qt.Orientation.Horizontal)
        root.addWidget(split)

        # Left: story input
        left = QFrame()
        left.setObjectName("Pane")
        ll = QVBoxLayout(left)
        ll.setContentsMargins(16, 14, 16, 14)
        ll.setSpacing(10)
        ll.addWidget(QLabel("写下故事或逐格构想"))
        self._story_text = QPlainTextEdit()
        self._story_text.setPlaceholderText("输入你想画的漫画故事、角色互动或场景梗概…")
        ll.addWidget(self._story_text, 1)

        starters = QHBoxLayout()
        for name, txt in [
            ("雨夜重逢", "雨夜东京街角，撑伞少女在便利店屋檐下偶遇许久未见的老友，第一人称 POV 视角。"),
            ("书店奇遇", "午后旧书店，从书架缝隙中望见有人正在翻阅一本夹着旧车票的画册。"),
        ]:
            b = QPushButton(f"示例 · {name}")
            b.setProperty("compact", True)
            b.clicked.connect(lambda _=False, s=txt: self._story_text.setPlainText(s))
            starters.addWidget(b)
        starters.addStretch(1)
        ll.addLayout(starters)

        mrow = QHBoxLayout()
        self._mode = QComboBox()
        for v, l in [("structure", "保留原意整理结构"), ("expand", "扩充细节与镜头"), ("continue", "续写并整理")]:
            self._mode.addItem(l, v)
        self._pref = QLineEdit()
        self._pref.setPlaceholderText("可选要求（如页数、节奏、氛围）")
        mrow.addWidget(QLabel("整理方式"))
        mrow.addWidget(self._mode)
        mrow.addWidget(self._pref, 1)
        ll.addLayout(mrow)

        ai_btn = QPushButton("让 AI 整理剧本草稿")
        ai_btn.setProperty("primary", True)
        ai_btn.setMinimumHeight(38)
        ai_btn.clicked.connect(self._run_story_draft)
        ll.addWidget(ai_btn)
        split.addWidget(left)

        # Right: editable draft / create project
        right = QFrame()
        right.setObjectName("Pane")
        rl = QVBoxLayout(right)
        rl.setContentsMargins(16, 14, 16, 14)
        rl.setSpacing(10)
        rt = QLabel("作品设定与确认创建")
        rt.setObjectName("SectionTitle")
        rl.addWidget(rt)

        grid = QGridLayout()
        self._title = QLineEdit()
        self._title.setPlaceholderText("作品名称")
        self._pages = QSpinBox()
        self._pages.setRange(1, 80)
        self._pages.setValue(4)
        self._pages.setSuffix(" 页")
        self._dir = QComboBox()
        self._dir.addItem("从左到右 (LTR)", "LTR")
        self._dir.addItem("从右到左 (RTL)", "RTL")
        grid.addWidget(QLabel("作品名称"), 0, 0)
        grid.addWidget(self._title, 0, 1, 1, 3)
        grid.addWidget(QLabel("页数"), 1, 0)
        grid.addWidget(self._pages, 1, 1)
        grid.addWidget(QLabel("阅读方向"), 1, 2)
        grid.addWidget(self._dir, 1, 3)
        rl.addLayout(grid)

        self._draft_summary = QPlainTextEdit()
        self._draft_summary.setReadOnly(True)
        self._draft_summary.setPlaceholderText("剧本大纲预览")
        rl.addWidget(self._draft_summary, 1)

        create_btn = QPushButton("创建作品并进入分镜")
        create_btn.setProperty("primary", True)
        create_btn.setMinimumHeight(40)
        create_btn.clicked.connect(self._create_project)
        rl.addWidget(create_btn)
        split.addWidget(right)

    def load_project(self, snap: dict | None, *, draft: dict | None = None) -> None:
        self._draft_result = draft
        if draft:
            self._pid = None
            self._story_text.setPlainText(draft.get("original") or "")
            brief = (draft.get("payload") or {}).get("brief") or {}
            self._title.setText(brief.get("title", ""))
            self._pages.setValue(int(brief.get("page_count") or 4))
            self._draft_summary.setPlainText(json.dumps(draft.get("payload") or {}, ensure_ascii=False, indent=2)[:4000])
            return
        if not snap:
            self._pid = None
            self._story_text.clear()
            self._title.clear()
            self._draft_summary.clear()
            return
        proj = snap["project"]
        self._pid = proj.get("id")
        self._story_text.setPlainText(proj.get("script") or "")
        brief = proj.get("brief") or {}
        self._title.setText(brief.get("title", ""))
        self._pages.setValue(int(brief.get("page_count") or 4))
        self._draft_summary.setPlainText(f"已创建作品：{brief.get('title','')}\n页数：{len(proj.get('pages', {}))} 页")

    def _run_story_draft(self) -> None:
        txt = self._story_text.toPlainText().strip()
        if not txt:
            self.notify_requested.emit("请先输入故事内容", True)
            return
        try:
            res = self.app.action({
                "action": "story_draft",
                "text": txt,
                "mode": self._mode.currentData(),
                "preferences": self._pref.text().strip(),
            })
            self.notify_requested.emit("已提交剧本整理任务", False)
            if res.get("job"):
                self.job_started.emit(res["job"])
        except Exception as exc:
            self.notify_requested.emit(str(exc), True)

    def _create_project(self) -> None:
        try:
            if self._draft_result:
                payload = self._draft_result
                if self._title.text().strip():
                    payload.setdefault("payload", {}).setdefault("brief", {})["title"] = self._title.text().strip()
                res = self.app.action({"action": "create_story", "result": payload})
                self.project_created.emit(res["project"])
                return
            title = self._title.text().strip() or "未命名漫画"
            brief = {
                "title": title,
                "genre": "彩色叙事漫画",
                "direction": self._story_text.toPlainText().strip() or title,
                "page_count": self._pages.value(),
                "panels_per_page": 4,
                "reading_direction": self._dir.currentData() or "LTR",
                "page_mode": "single_page",
                "first_story_page_side": "right",
                "must_include": [],
                "avoid": [],
                "character_material": self._story_text.toPlainText().strip() or title,
            }
            res = self.app.action({"action": "create", "brief": brief})
            self.project_created.emit(res["project"])
        except Exception as exc:
            self.notify_requested.emit(str(exc), True)


# ==================== 02 STORYBOARD ====================


class StoryboardPage(QWidget):
    notify_requested = Signal(str, bool)
    job_started = Signal(str)
    project_updated = Signal()
    go_drawing_requested = Signal()

    def __init__(self, app: StudioApp, parent: QWidget | None = None):
        super().__init__(parent)
        self.app = app
        self._pid: str | None = None
        self._snap: dict | None = None

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)

        bar = QHBoxLayout()
        gen_all = QPushButton("一键生成全部缺失分镜")
        gen_all.setProperty("primary", True)
        gen_all.clicked.connect(self._generate_all)
        gen_one = QPushButton("生成当前页分镜")
        gen_one.clicked.connect(self._generate_one)
        exp_b = QPushButton("导出分镜 JSON")
        exp_b.clicked.connect(self._export_sb)
        next_b = QPushButton("进入 03 绘图与排版")
        next_b.setProperty("primary", True)
        next_b.clicked.connect(self.go_drawing_requested.emit)
        bar.addWidget(gen_all)
        bar.addWidget(gen_one)
        bar.addWidget(exp_b)
        bar.addStretch(1)
        bar.addWidget(next_b)
        lay.addLayout(bar)

        split = QSplitter(Qt.Orientation.Horizontal)
        lay.addWidget(split, 1)

        # Left: pages & panels readable list
        left = QFrame()
        left.setObjectName("Pane")
        ll = QVBoxLayout(left)
        self._page_combo = QComboBox()
        self._page_combo.currentIndexChanged.connect(self._render_page)
        ll.addWidget(self._page_combo)
        self._panels_view = QPlainTextEdit()
        self._panels_view.setReadOnly(True)
        ll.addWidget(self._panels_view, 1)
        split.addWidget(left)

        # Right: pending candidates
        right = QFrame()
        right.setObjectName("Pane")
        rl = QVBoxLayout(right)
        rl.addWidget(QLabel("待确认的分镜候选"))
        self._cand_list = QListWidget()
        rl.addWidget(self._cand_list, 1)
        cbtns = QHBoxLayout()
        acc = QPushButton("应用选中候选")
        acc.setProperty("primary", True)
        acc.clicked.connect(self._accept_candidate)
        cbtns.addWidget(acc)
        cbtns.addStretch(1)
        rl.addLayout(cbtns)
        split.addWidget(right)

    def load_project(self, pid: str, snap: dict) -> None:
        self._pid = pid
        self._snap = snap
        proj = snap["project"]
        self._page_combo.blockSignals(True)
        self._page_combo.clear()
        for k in sorted(proj.get("pages", {})):
            self._page_combo.addItem(f"{k}（{len(proj['pages'][k].get('panels', []))} 格）", k)
        self._page_combo.blockSignals(False)
        self._render_page()

        self._cand_list.clear()
        for c in snap.get("candidates") or []:
            it = QListWidgetItem(f"{c.get('stage')} · {c.get('target', '')} · {c.get('note', '')}")
            it.setData(Qt.ItemDataRole.UserRole, c["id"])
            self._cand_list.addItem(it)

    def _render_page(self) -> None:
        if not self._snap:
            return
        pk = self._page_combo.currentData()
        page = (self._snap["project"].get("pages") or {}).get(pk)
        if not page:
            self._panels_view.setPlainText("尚未生成分镜页，点击上方「一键生成全部缺失分镜」开始。")
            return
        lines = []
        for p in page.get("panels") or []:
            lines.append(f"【{p['id']}】{p.get('shot','')} / {p.get('angle','')} ({p.get('aspect_ratio','')})")
            lines.append(f"  动作：{p.get('main_instant','')}")
            lines.append(f"  画面：{p.get('drawing_description','')}")
            lines.append("")
        self._panels_view.setPlainText("\n".join(lines))

    def _generate_all(self) -> None:
        if not self._pid or not self._snap:
            return
        try:
            res = self.app.action({"action": "storyboard_all", "project": self._pid, "base_hash": self._snap["hash"]})
            self.notify_requested.emit("已开始生成整部分镜", False)
            if res.get("job"):
                self.job_started.emit(res["job"])
        except Exception as exc:
            self.notify_requested.emit(str(exc), True)

    def _generate_one(self) -> None:
        if not self._pid or not self._snap:
            return
        try:
            res = self.app.action({
                "action": "storyboard",
                "project": self._pid,
                "base_hash": self._snap["hash"],
                "target": self._page_combo.currentData() or "",
            })
            self.notify_requested.emit("已开始生成单页分镜", False)
            if res.get("job"):
                self.job_started.emit(res["job"])
        except Exception as exc:
            self.notify_requested.emit(str(exc), True)

    def _accept_candidate(self) -> None:
        cur = self._cand_list.currentItem()
        if not cur or not self._pid:
            return
        try:
            self.app.action({"action": "accept", "project": self._pid, "candidate": cur.data(Qt.ItemDataRole.UserRole)})
            self.notify_requested.emit("已应用分镜候选", False)
            self.project_updated.emit()
        except Exception as exc:
            self.notify_requested.emit(str(exc), True)

    def _export_sb(self) -> None:
        if not self._pid:
            return
        try:
            doc = self.app.export_storyboard(self._pid)
            dest, _ = QFileDialog.getSaveFileName(self, "导出分镜 JSON", f"{self._pid}-storyboard-prompts.json", "JSON (*.json)")
            if dest:
                Path(dest).write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
                self.notify_requested.emit("已导出分镜文件：" + dest, False)
        except Exception as exc:
            self.notify_requested.emit(str(exc), True)


# ==================== 03 DRAWING & CANDIDATE SELECTION ====================


class _DrawingLoraDialog(QDialog):
    """Per-project LoRA & model settings dialog (checked LoRAs ALWAYS apply!)."""

    def __init__(self, app: StudioApp, pid: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.app = app
        self.pid = pid
        self.setWindowTitle("绘图设置 · 模型、尺寸与 LoRA")
        self.resize(740, 620)

        self._opts = app.drawing_options(project_id=pid)
        self._prefs = self._opts.get("drawing_settings") or {
            "mode": "characters",
            "checkpoint": self._opts.get("preferred_checkpoint") or (self._opts.get("comfy") or {}).get("configured"),
            "aspect_ratio": "storyboard",
            "resolution": "default",
            "items": [],
        }

        lay = QVBoxLayout(self)
        top = QGridLayout()
        self._model = QComboBox()
        diff = self._opts.get("diffusion_models") or {}
        for c in (self._opts.get("comfy") or {}).get("checkpoints") or []:
            self._model.addItem((diff.get(c) or {}).get("label") or c, c)
        idx = self._model.findData(self._prefs.get("checkpoint"))
        if idx >= 0:
            self._model.setCurrentIndex(idx)

        self._mode = QComboBox()
        for v, l in [("characters", "自动匹配出场角色 + 勾选项"), ("manual", "仅使用勾选项"), ("none", "不用 LoRA")]:
            self._mode.addItem(l, v)
        midx = self._mode.findData(self._prefs.get("mode", "characters"))
        if midx >= 0:
            self._mode.setCurrentIndex(midx)

        top.addWidget(QLabel("绘图模型"), 0, 0)
        top.addWidget(self._model, 0, 1)
        top.addWidget(QLabel("LoRA 模式"), 0, 2)
        top.addWidget(self._mode, 0, 3)
        lay.addLayout(top)

        self._size = ImageSizeWidget(allow_storyboard=True)
        self._size.apply(self._prefs)
        lay.addWidget(self._size)

        self._search = QLineEdit()
        self._search.setPlaceholderText("筛选 LoRA…")
        self._search.textChanged.connect(self._refresh_list)
        self._list = QListWidget()
        self._list.setUniformItemSizes(True)
        attach_lora_star_delegate(self._list, self._toggle_favorite_lora)
        lay.addWidget(self._search)
        lay.addWidget(self._list, 1)

        self._selected_map: dict[str, dict] = {
            str(it.get("file", "")).replace("\\", "/"): dict(it)
            for it in (self._prefs.get("items") or [])
            if it.get("selected")
        }
        self._list.itemChanged.connect(self._on_check)
        self._model.currentIndexChanged.connect(self._refresh_list)
        self._refresh_list()

        foot = QHBoxLayout()
        save_b = QPushButton("保存设置")
        save_b.setProperty("primary", True)
        save_b.clicked.connect(self._save)
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        foot.addStretch(1)
        foot.addWidget(cancel)
        foot.addWidget(save_b)
        lay.addLayout(foot)

    def _toggle_favorite_lora(self, file: str) -> None:
        self.app.ui_state.toggle_favorite_lora(file)
        self._refresh_list()

    def _refresh_list(self) -> None:
        cp = str(self._model.currentData() or "")
        diff = self._opts.get("diffusion_models") or {}
        fam, folder = checkpoint_family_and_dir(cp, diff)
        insp = self._opts.get("lora_inspection") or {}
        q = self._search.text().strip()
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

        self._list.blockSignals(True)
        self._list.setUpdatesEnabled(False)
        try:
            self._list.clear()
            for f in matches:
                it = QListWidgetItem(label_map.get(f, lora_choice_name(f)))
                it.setCheckState(Qt.CheckState.Checked if f in self._selected_map else Qt.CheckState.Unchecked)
                it.setData(Qt.ItemDataRole.UserRole, f)
                it.setData(ROLE_LORA_FAV, f in fav_set)
                it.setToolTip(f)
                self._list.addItem(it)
        finally:
            self._list.setUpdatesEnabled(True)
            self._list.blockSignals(False)

    def _on_check(self, item: QListWidgetItem) -> None:
        f = item.data(Qt.ItemDataRole.UserRole)
        if not f:
            return
        labels = self._opts.get("lora_labels") or {}
        lbl = labels.get(f) or {}
        if item.checkState() == Qt.CheckState.Checked:
            if self._mode.currentData() == "none":
                self._mode.setCurrentIndex(1)
            self._selected_map[f] = {
                "file": f,
                "name": lbl.get("name") or lora_choice_name(f),
                "character_id": lbl.get("character_id", ""),
                "character": lbl.get("character", ""),
                "characters": [lbl["character"]] if lbl.get("character") else [],
                "scope": "characters" if lbl.get("character_id") else "story",
                "selected": True,
                "weight": float(self.app.ui_state.lora_weights().get(f, lbl.get("weight", 0.7))),
                "trigger_words": str(lbl.get("trigger_words", "")),
                "confirmed": True,
            }
        else:
            self._selected_map.pop(f, None)

    def _save(self) -> None:
        settings = {
            **self._size.read(),
            "mode": self._mode.currentData() or "characters",
            "checkpoint": str(self._model.currentData() or ""),
            "hand_detail": False,
            "consistency_lora": bool(self._prefs.get("consistency_lora", False)),
            "reference_character_id": str(self._prefs.get("reference_character_id", "")),
            "items": list(self._selected_map.values()),
        }
        try:
            self.app.action({"action": "save_drawing_settings", "project": self.pid, "settings": settings})
            self.accept()
        except Exception as exc:
            QMessageBox.warning(self, "保存失败", str(exc))


class DrawingPage(QWidget):
    notify_requested = Signal(str, bool)
    job_started = Signal(str)
    go_problems_requested = Signal()
    open_import_setup_requested = Signal(str)

    def __init__(self, app: StudioApp, parent: QWidget | None = None):
        super().__init__(parent)
        self.app = app
        self._pid: str | None = None
        self._snap: dict | None = None
        self._drawing: dict = {}
        self._current_candidates: list[dict] = []

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        split = QSplitter(Qt.Orientation.Horizontal)
        root.addWidget(split)

        # Left control column
        left = QFrame()
        left.setObjectName("Pane")
        left.setMaximumWidth(360)
        ll = QVBoxLayout(left)
        ll.setContentsMargins(14, 14, 14, 14)
        ll.setSpacing(10)

        t = QLabel("绘制与选图")
        t.setObjectName("SectionTitle")
        ll.addWidget(t)
        self._summary = QLabel("")
        self._summary.setObjectName("Muted")
        self._summary.setWordWrap(True)
        ll.addWidget(self._summary)

        crow = QHBoxLayout()
        crow.addWidget(QLabel("每格张数"))
        self._count = QSpinBox()
        self._count.setRange(1, 16)
        self._count.setValue(2)
        crow.addWidget(self._count)
        ll.addLayout(crow)

        b_settings = QPushButton("绘图设置 · 模型、尺寸与 LoRA")
        b_settings.clicked.connect(self._open_settings)
        b_draw = QPushButton("绘制未完成画格")
        b_draw.setProperty("primary", True)
        b_draw.setMinimumHeight(38)
        b_draw.clicked.connect(lambda: self._start_draw(queued=False))
        b_queue = QPushButton("加入夜间任务列表")
        b_queue.clicked.connect(lambda: self._start_draw(queued=True))
        b_fill = QPushButton("Flash Next 自动填写提示词")
        b_fill.clicked.connect(self._fill_local_prompts)
        b_import_setup = QPushButton("逐格出图设置 / 导入")
        b_import_setup.clicked.connect(lambda: self.open_import_setup_requested.emit(self._pid or ""))
        b_clear = QPushButton("一键清除所有图片")
        b_clear.setProperty("danger", True)
        b_clear.clicked.connect(self._clear_all_images)

        for b in (b_settings, b_draw, b_queue, b_fill, b_import_setup, b_clear):
            ll.addWidget(b)

        # Inline page layout compose
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        ll.addWidget(sep)
        ll.addWidget(QLabel("页面排版"))
        self._preset = QComboBox()
        for k, lbl in [("story", "按分镜节奏"), ("hero", "首格大画面"), ("cinema", "宽窄交替"), ("balanced", "均衡分格"), ("vertical", "竖向条漫")]:
            self._preset.addItem(lbl, k)
        ll.addWidget(self._preset)
        b_compose = QPushButton("用已选图片排版当前页")
        b_compose.clicked.connect(self._compose_page)
        ll.addWidget(b_compose)

        ll.addStretch(1)
        b_next = QPushButton("进入 04 问题格子")
        b_next.setProperty("primary", True)
        b_next.clicked.connect(self.go_problems_requested.emit)
        ll.addWidget(b_next)
        split.addWidget(left)

        # Right: panel selector + candidate grid
        right = QFrame()
        right.setObjectName("GalleryPane")
        rl = QVBoxLayout(right)
        rl.setContentsMargins(14, 12, 14, 12)
        rl.setSpacing(8)

        nav = QHBoxLayout()
        self._panel_combo = QComboBox()
        self._panel_combo.currentIndexChanged.connect(self._render_candidates)
        prev_p = QPushButton("上一格")
        prev_p.setProperty("ghost", True)
        prev_p.setProperty("compact", True)
        prev_p.setIcon(studio_icon("chevron-left", tokens()["muted"], 14))
        prev_p.clicked.connect(lambda: self._step_panel(-1))
        next_p = QPushButton("下一格")
        next_p.setProperty("ghost", True)
        next_p.setProperty("compact", True)
        next_p.setIcon(studio_icon("chevron-right", tokens()["muted"], 14))
        next_p.clicked.connect(lambda: self._step_panel(1))
        mark_prob = QPushButton("标记问题")
        mark_prob.setProperty("compact", True)
        mark_prob.setProperty("danger", True)
        mark_prob.clicked.connect(self._mark_problem)
        nav.addWidget(self._panel_combo, 1)
        nav.addWidget(prev_p)
        nav.addWidget(next_p)
        nav.addWidget(mark_prob)
        rl.addLayout(nav)

        self._panel_desc = QLabel("")
        self._panel_desc.setObjectName("Muted")
        self._panel_desc.setWordWrap(True)
        rl.addWidget(self._panel_desc)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._grid_host = QWidget()
        self._grid = QGridLayout(self._grid_host)
        self._grid.setSpacing(10)
        scroll.setWidget(self._grid_host)
        rl.addWidget(scroll, 1)
        split.addWidget(right)

    def load_project(self, pid: str, snap: dict) -> None:
        self._pid = pid
        self._snap = snap
        self._drawing = self.app.drawing_state(pid)
        proj = snap["project"]
        self._panel_combo.blockSignals(True)
        cur = self._panel_combo.currentData()
        self._panel_combo.clear()
        sels = (self._drawing.get("images") or {}).get("selections") or {}
        total_panels = 0
        done_panels = 0
        done_ic = studio_icon("check", tokens()["success"], 13)
        todo_ic = studio_icon("circle", tokens()["caption"], 12)
        for pk in sorted(proj.get("pages", {})):
            for panel in proj["pages"][pk].get("panels") or []:
                total_panels += 1
                picked = panel["id"] in sels
                if picked:
                    done_panels += 1
                self._panel_combo.addItem(done_ic if picked else todo_ic, f"{panel['id']} · {panel.get('main_instant','')[:28]}", panel)
        if cur:
            for i in range(self._panel_combo.count()):
                if (self._panel_combo.itemData(i) or {}).get("id") == cur.get("id"):
                    self._panel_combo.setCurrentIndex(i)
                    break
        self._panel_combo.blockSignals(False)

        prefs = self._drawing.get("drawing_settings") or {}
        self._summary.setText(
            f"已选图：{done_panels} / {total_panels} 格\n"
            f"底模：{prefs.get('checkpoint') or (self._drawing.get('comfy') or {}).get('configured', '')}"
        )
        self._render_candidates()

    def _step_panel(self, delta: int) -> None:
        cnt = self._panel_combo.count()
        if cnt > 0:
            self._panel_combo.setCurrentIndex((self._panel_combo.currentIndex() + delta) % cnt)

    def _render_candidates(self) -> None:
        while self._grid.count():
            w = self._grid.takeAt(0).widget()
            if w is not None:
                w.hide()
                w.setParent(None)
                w.deleteLater()
        panel = self._panel_combo.currentData()
        if not panel or not self._pid:
            return
        pid_key = panel["id"]
        self._panel_desc.setText(panel.get("drawing_description") or panel.get("main_instant") or "")
        imgs_state = self._drawing.get("images") or {}
        selected_id = ((imgs_state.get("selections") or {}).get(pid_key) or {}).get("image", {}).get("id")

        cands = []
        for b in imgs_state.get("batches") or []:
            if b.get("panel_id") == pid_key:
                for img in b.get("images") or []:
                    cands.append({**img, "path": self.app.image_file_path(self._pid, img["file"]), "batch": b})
        self._current_candidates = cands

        if not cands:
            lbl = QLabel("暂无候选图")
            lbl.setObjectName("Muted")
            self._grid.addWidget(lbl, 0, 0)
            return

        for idx, c in enumerate(cands):
            card = QFrame()
            card.setObjectName("HeroCard" if c["id"] == selected_id else "Card")
            cl = QVBoxLayout(card)
            cl.setContentsMargins(8, 8, 8, 8)
            cl.setSpacing(6)
            pic = QLabel()
            pic.setAlignment(Qt.AlignmentFlag.AlignCenter)
            pic.setMinimumHeight(200)
            if c["path"].is_file():
                image_cache().request_thumbnail(
                    c["path"],
                    260,
                    260,
                    lambda _p, pm, lbl=pic: lbl.setPixmap(pm) if not pm.isNull() else None,
                )
            cl.addWidget(pic)

            brow = QHBoxLayout()
            pick_b = QPushButton("已选用" if c["id"] == selected_id else "选用此图")
            pick_b.setProperty("compact", True)
            pick_b.setProperty("primary", c["id"] != selected_id)
            pick_b.clicked.connect(lambda _=False, iid=c["id"]: self._select_candidate(pid_key, iid))
            zoom_b = QPushButton("查看大图")
            zoom_b.setProperty("compact", True)
            zoom_b.clicked.connect(lambda _=False, i=idx: self._open_zoom(i))
            brow.addWidget(pick_b, 1)
            brow.addWidget(zoom_b)
            cl.addLayout(brow)
            self._grid.addWidget(card, idx // 3, idx % 3)

    def _open_zoom(self, idx: int) -> None:
        paths = [c["path"] for c in self._current_candidates if c["path"].is_file()]
        if paths:
            ImageLightboxDialog(paths, min(idx, len(paths) - 1), self).exec()

    def _select_candidate(self, target: str, image_id: str) -> None:
        if not self._pid:
            return
        try:
            self.app.action({
                "action": "select_image",
                "project": self._pid,
                "target": target,
                "image": image_id,
                "allow_unreviewed": True,
                "allow_rejected": True,
            })
            self.notify_requested.emit(f"已选用 {target} 候选图", False)
            self.load_project(self._pid, self.app.snapshot(self._pid))
        except Exception as exc:
            self.notify_requested.emit(str(exc), True)

    def _open_settings(self) -> None:
        if not self._pid:
            return
        dlg = _DrawingLoraDialog(self.app, self._pid, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.load_project(self._pid, self.app.snapshot(self._pid))

    def _start_draw(self, *, queued: bool) -> None:
        if not self._pid or not self._snap:
            return
        try:
            prev = self.app.action({"action": "preview_drawing", "project": self._pid, "base_hash": self._snap["hash"]})
            rows = prev.get("panels") or []
            if not rows:
                self.notify_requested.emit("所有画格已选图", False)
                return
            missing = [r["panel_id"] for r in rows if not str(r.get("positive", "")).strip()]
            if missing:
                self.notify_requested.emit(f"{missing[0]} 等 {len(missing)} 格尚无提示词，请先「自动填写提示词」或导入提示词。", True)
                return
            res = self.app.action({
                "action": "queue_drawing" if queued else "draw_all",
                "project": self._pid,
                "base_hash": self._snap["hash"],
                "candidate_count": self._count.value(),
                "confirm_prompts": True,
                "prompt_rows": rows,
            })
            self.notify_requested.emit("已加入任务列表" if queued else "已开始绘制未完成画格", False)
            if not queued and res.get("job"):
                self.job_started.emit(res["job"])
        except Exception as exc:
            self.notify_requested.emit(str(exc), True)

    def _fill_local_prompts(self) -> None:
        if not self._pid:
            return
        try:
            res = self.app.action({"action": "fill_local_prompts", "project": self._pid})
            self.notify_requested.emit("已开始用本地 Qwen 逐格填写提示词", False)
            if res.get("job"):
                self.job_started.emit(res["job"])
        except Exception as exc:
            self.notify_requested.emit(str(exc), True)

    def _mark_problem(self) -> None:
        panel = self._panel_combo.currentData()
        if not panel or not self._pid:
            return
        from PySide6.QtWidgets import QInputDialog

        note, ok = QInputDialog.getMultiLineText(self, f"标记 {panel['id']} 问题", "填写修改说明（将汇总到「04 问题格子」）：")
        if ok and note.strip():
            try:
                self.app.action({"action": "save_problem", "project": self._pid, "panel_id": panel["id"], "note": note.strip()})
                self.notify_requested.emit(f"已标记 {panel['id']} 问题说明", False)
            except Exception as exc:
                self.notify_requested.emit(str(exc), True)

    def _compose_page(self) -> None:
        panel = self._panel_combo.currentData()
        if not panel or not self._pid:
            return
        page_id = panel["id"].split("-")[0]
        try:
            presets = self.app.layout_presets(self._pid, page_id)
            lay_obj = presets.get(self._preset.currentData() or "story")
            self.app.action({"action": "compose", "project": self._pid, "target": page_id, "layout": lay_obj})
            self.notify_requested.emit(f"已生成 {page_id} 整页排版", False)
        except Exception as exc:
            self.notify_requested.emit(str(exc), True)

    def _clear_all_images(self) -> None:
        if not self._pid or not self._snap:
            return
        if QMessageBox.question(self, "确认清除", "清除本作品所有已生成图片？（旧图片会自动备份）") != QMessageBox.StandardButton.Yes:
            return
        try:
            res = self.app.action({"action": "clear_generated_images", "project": self._pid, "base_hash": self._snap["hash"]})
            self.notify_requested.emit(f"已清除 {res.get('cleared', 0)} 张图片", False)
            self.load_project(self._pid, self.app.snapshot(self._pid))
        except Exception as exc:
            self.notify_requested.emit(str(exc), True)


# ==================== 04 PROBLEMS ====================


class ProblemsPage(QWidget):
    notify_requested = Signal(str, bool)
    job_started = Signal(str)
    go_finished_requested = Signal()

    def __init__(self, app: StudioApp, parent: QWidget | None = None):
        super().__init__(parent)
        self.app = app
        self._pid: str | None = None

        lay = QVBoxLayout(self)
        bar = QHBoxLayout()
        understand_redraw = QPushButton("AI 理解问题并统一重画")
        understand_redraw.setProperty("primary", True)
        understand_redraw.clicked.connect(self._understand_and_redraw)
        redraw_only = QPushButton("直接重画全部问题格子")
        redraw_only.clicked.connect(self._redraw_all)
        next_b = QPushButton("进入 05 成品查看")
        next_b.clicked.connect(self.go_finished_requested.emit)
        bar.addWidget(understand_redraw)
        bar.addWidget(redraw_only)
        bar.addStretch(1)
        bar.addWidget(next_b)
        lay.addLayout(bar)

        self._list = QListWidget()
        lay.addWidget(self._list, 1)

    def load_project(self, pid: str) -> None:
        self._pid = pid
        self._list.clear()
        state = self.app.problems_state(pid)
        items = state.get("items") or []
        if not items:
            self._list.addItem("暂无标记的问题格子")
            return
        for it in items:
            self._list.addItem(f"[{it.get('panel_id')}] {it.get('note', '')}")

    def _understand_and_redraw(self) -> None:
        if not self._pid:
            return
        try:
            res = self.app.action({"action": "understand_and_redraw", "project": self._pid})
            self.notify_requested.emit("已提交按问题说明统一重画任务", False)
            if res.get("job"):
                self.job_started.emit(res["job"])
        except Exception as exc:
            self.notify_requested.emit(str(exc), True)

    def _redraw_all(self) -> None:
        if not self._pid:
            return
        try:
            res = self.app.action({"action": "redraw_problems", "project": self._pid})
            self.notify_requested.emit("已提交重画全部问题格子任务", False)
            if res.get("job"):
                self.job_started.emit(res["job"])
        except Exception as exc:
            self.notify_requested.emit(str(exc), True)


# ==================== 05 FINISHED & BOARD LAYOUT ====================


class FinishedPage(QWidget):
    notify_requested = Signal(str, bool)

    def __init__(self, app: StudioApp, parent: QWidget | None = None):
        super().__init__(parent)
        self.app = app
        self._pid: str | None = None
        self._is_board = False
        self._page_paths: list[Path] = []

        lay = QVBoxLayout(self)
        bar = QHBoxLayout()
        self._page_combo = QComboBox()
        self._page_combo.currentIndexChanged.connect(self._show_page)
        self._preset = QComboBox()
        for k, lbl in [("story", "分镜节奏"), ("balanced", "均衡分格"), ("hero", "首格大画面"), ("cinema", "宽窄交替"), ("vertical", "竖向条漫")]:
            self._preset.addItem(lbl, k)
        compose_b = QPushButton("生成 / 更新整页")
        compose_b.setProperty("primary", True)
        compose_b.clicked.connect(self._compose)
        save_png = QPushButton("导出本页 PNG")
        save_png.clicked.connect(self._export_png)
        bar.addWidget(QLabel("页面"))
        bar.addWidget(self._page_combo)
        bar.addWidget(QLabel("版式"))
        bar.addWidget(self._preset)
        bar.addWidget(compose_b)
        bar.addStretch(1)
        bar.addWidget(save_png)
        lay.addLayout(bar)

        self._viewer = ZoomableImageView(self)
        self._viewer.clicked.connect(self._open_lightbox)
        lay.addWidget(self._viewer, 1)

    def load_project(self, pid: str, snap: dict) -> None:
        self._pid = pid
        proj = snap["project"]
        self._is_board = bool(proj.get("prompt_board"))
        self._page_combo.blockSignals(True)
        self._page_combo.clear()
        for pk in sorted(proj.get("pages", {})):
            self._page_combo.addItem(pk, pk)
        self._page_combo.blockSignals(False)
        self._show_page()

    def _latest_page_png(self, page_id: str) -> Path | None:
        if not self._pid:
            return None
        folder = self.app.repo.folder(self._pid) / "layouts"
        if not folder.is_dir():
            return None
        candidates = sorted(folder.glob("*/page.png"), key=lambda p: p.stat().st_mtime, reverse=True)
        for p in candidates:
            mf = p.parent / "manifest.json"
            if mf.is_file():
                try:
                    if json.loads(mf.read_text(encoding="utf-8")).get("page_id") == page_id:
                        return p
                except Exception:
                    pass
        return candidates[0] if candidates else None

    def _show_page(self) -> None:
        pk = self._page_combo.currentData()
        if not pk:
            self._viewer.set_image(None)
            return
        png = self._latest_page_png(pk)
        self._viewer.set_image(png)

    def _compose(self) -> None:
        pk = self._page_combo.currentData()
        if not pk or not self._pid:
            return
        try:
            if self._is_board:
                self.app.action({
                    "action": "compose_board_page",
                    "project": self._pid,
                    "page": pk,
                    "preset": self._preset.currentData() or "story",
                    "fit": "cover",
                })
            else:
                presets = self.app.layout_presets(self._pid, pk)
                self.app.action({"action": "compose", "project": self._pid, "target": pk, "layout": presets[self._preset.currentData() or "story"]})
            self.notify_requested.emit(f"已生成 {pk} 成品页", False)
            self._show_page()
        except Exception as exc:
            self.notify_requested.emit(str(exc), True)

    def _export_png(self) -> None:
        p = self._viewer.current_path()
        if not p or not p.is_file():
            self.notify_requested.emit("请先点击「生成 / 更新整页」", True)
            return
        dest, _ = QFileDialog.getSaveFileName(self, "导出成品页 PNG", f"{self._pid}-{self._page_combo.currentData()}.png", "PNG (*.png)")
        if dest:
            shutil.copy2(p, dest)
            self.notify_requested.emit("已导出：" + dest, False)

    def _open_lightbox(self) -> None:
        p = self._viewer.current_path()
        if p and p.is_file():
            ImageLightboxDialog([p], 0, self).exec()
