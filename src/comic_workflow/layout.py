"""Simple editable page layout, with separate art and lettering SVG layers."""

import base64
import io
import html
import math
import uuid
from PIL import Image, ImageDraw, ImageFont, ImageOps
from . import engine as e
from .comfyui import drawing_state
from .paths import ROOT


def default_layout(page, preset="story"):
    count = len(page["panels"])
    gap = 12
    margin = 24
    width = 1600
    height = 2264
    e.need(preset in ["story", "balanced", "hero", "cinema", "vertical"], "未知排版")
    panels = page["panels"]
    rows = page.get("layout_rows") if preset == "story" else None
    if not rows:
        groups = []
        if preset in ["story", "hero"]:
            groups = [panels[:1]] + [panels[i : i + 2] for i in range(1, count, 2)]
        elif preset == "vertical":
            groups = [[p] for p in panels]
        else:
            groups = [panels[i : i + 2] for i in range(0, count, 2)]
        rows = [
            {
                "weight": 1.7 if preset in ["story", "hero"] and i == 0 else 1,
                "panels": [
                    {
                        "panel_id": p["id"],
                        "weight": 1.6 if preset == "cinema" and j == 0 else 1,
                    }
                    for j, p in enumerate(group)
                ],
            }
            for i, group in enumerate(groups)
        ]
    e.need(
        [x["panel_id"] for row in rows for x in row["panels"]]
        == [p["id"] for p in panels],
        "排版引用须按顺序包含全部画格",
    )
    total = sum(row["weight"] for row in rows)
    available_h = height - margin * 2 - gap * (len(rows) - 1)
    rects = {}
    y = margin
    for row in rows:
        h = available_h * row["weight"] / total
        available_w = width - margin * 2 - gap * (len(row["panels"]) - 1)
        total_w = sum(x["weight"] for x in row["panels"])
        x = margin
        for panel in row["panels"]:
            w = available_w * panel["weight"] / total_w
            px = width - x - w if page["reading_direction"] == "RTL" else x
            rects[panel["panel_id"]] = {
                "x": int(px),
                "y": int(y),
                "w": int(w),
                "h": int(h),
                "fit": "cover",
                "focus": [0.5, 0.5],
            }
            x += w + gap
        y += h + gap
    result = {"width": width, "height": height, "panels": rects, "lettering": {}}
    validate_layout(result, page)
    return result


def validate_layout(layout, page):
    e.need(
        set(layout["panels"]) == set(x["id"] for x in page["panels"]),
        "排版必须包含全部画格，且不得多出画格",
    )
    W, H = layout["width"], layout["height"]
    e.need(400 <= W <= 6000 and 400 <= H <= 6000, "页面尺寸须在 400–6000 像素内")
    for r in layout["panels"].values():
        e.need(r.get("fit", "cover") in ["cover", "contain"], "未知画格填充方式")
        focus = r.get("focus", [0.5, 0.5])
        e.need(
            isinstance(focus, list)
            and len(focus) == 2
            and all(isinstance(v, (float, int)) and 0 <= v <= 1 for v in focus),
            "裁切中心须为两个 0 到 1 的数值",
        )
        e.need(
            all(isinstance(r.get(k), int) for k in ["x", "y", "w", "h"]),
            "格位置与大小须为整数",
        )
        e.need(
            r["x"] >= 0
            and r["y"] >= 0
            and r["w"] >= 60
            and r["h"] >= 60
            and r["x"] + r["w"] <= W
            and r["y"] + r["h"] <= H,
            "画格超出页面或过小",
        )
    e.need(
        set(layout.get("lettering", {})) <= set(x["id"] for x in page["lettering"]),
        "文字层引用未知文字",
    )
    for pos in layout.get("lettering", {}).values():
        e.need(
            isinstance(pos.get("x"), int)
            and isinstance(pos.get("y"), int)
            and 0 <= pos["x"] < W
            and 0 <= pos["y"] < H,
            "文字位置超出页面",
        )


def compose(repo, pid, page_id, layout):
    p = repo.get(pid)
    page = p["pages"][page_id]
    validate_layout(layout, page)
    selected = drawing_state(repo, pid)["selections"]
    W, H = layout["width"], layout["height"]
    canvas = Image.new("RGB", (W, H), "white")
    draw = ImageDraw.Draw(canvas)
    art = []
    texts = []
    warnings = []
    fonts = [
        "C:/Windows/Fonts/simkai.ttf",
        "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/simhei.ttf",
    ]
    font = None
    font_family = "Microsoft YaHei, sans-serif"
    for f in fonts:
        try:
            font = ImageFont.truetype(f, 30)
            font_family = (
                "KaiTi, serif" if "simkai" in f else "Microsoft YaHei, sans-serif"
            )
            break
        except OSError:
            pass
    e.need(font is not None, "未找到中文字体；请安装微软雅黑或黑体")
    for panel in page["panels"]:
        s = selected.get(panel["id"])
        e.need(
            s and s["panel_hash"] == e.digest(panel),
            panel["id"] + " 尚未选择当前分镜版本的图片",
        )
        r = layout["panels"][panel["id"]]
        x, y, w, h = [r[k] for k in ["x", "y", "w", "h"]]
        image_path = repo.folder(pid) / "images" / s["image"]["file"]
        with Image.open(image_path) as original:
            image = (
                ImageOps.fit(
                    original.convert("RGB"),
                    (w, h),
                    method=Image.Resampling.LANCZOS,
                    centering=tuple(r.get("focus", [0.5, 0.5])),
                )
                if r.get("fit", "cover") == "cover"
                else ImageOps.contain(original.convert("RGB"), (w, h))
            )
            px = x + (w - image.width) // 2
            py = y + (h - image.height) // 2
            canvas.paste(image, (px, py))
        draw.rectangle((x, y, x + w, y + h), outline="black", width=3)
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        data = base64.b64encode(buffer.getvalue()).decode()
        art.append(
            f'<image x="{x}" y="{y}" width="{w}" height="{h}" preserveAspectRatio="xMidYMid meet" href="data:image/png;base64,{data}"/><rect x="{x}" y="{y}" width="{w}" height="{h}" fill="none" stroke="black" stroke-width="3"/>'
        )
        lines = [t for t in page["lettering"] if t["panel_id"] == panel["id"]]
        next_y = y + 18
        for i, t in enumerate(sorted(lines, key=lambda a: a["order"])):
            # Default positions follow order; exact bubble placement remains editable in layout data.
            tx = x + 18
            ty = next_y
            if "右" in t["bubble_position"] or (
                page["reading_direction"] == "RTL" and "左" not in t["bubble_position"]
            ):
                tx = x + w // 2
            pos = layout.get("lettering", {}).get(t["id"], {"x": tx, "y": ty})
            tx, ty = pos["x"], pos["y"]
            max_width = max(90, min(w // 2 - 30, W - tx - 20))
            wrapped = []
            line = ""
            for ch in t["text"]:
                if ch == "\n" or (line and font.getlength(line + ch) > max_width):
                    wrapped.append(line)
                    line = ""
                if ch != "\n":
                    line += ch
            if line:
                wrapped.append(line)
            speech = t["kind"] == "dialogue"
            padding = 24 if speech else 12
            bw = min(
                W - tx - 4,
                max(
                    50,
                    int(max((font.getlength(l) for l in wrapped), default=30))
                    + padding * 2,
                ),
            )
            bh = len(wrapped) * 40 + padding * 2
            if speech:
                bw = min(W - tx - 4, int(bw * 1.2))
                bh = int(bh * 1.2)
            e.need(ty + bh < H, "文字气泡超出页面，请调整位置")
            if ty + bh > y + h:
                warnings.append(t["id"] + " 气泡超出所属格，需人工调整")
            texts.append(f'<g id="{html.escape(t["id"])}">')
            if speech:
                draw.ellipse(
                    (tx, ty, tx + bw, ty + bh), fill="white", outline="black", width=2
                )
                texts.append(
                    f'<ellipse cx="{tx + bw / 2}" cy="{ty + bh / 2}" rx="{bw / 2}" ry="{bh / 2}" fill="white" stroke="black" stroke-width="2"/>'
                )
                # A short quiet tail; position remains editable in the lettering layer.
                tail = [
                    (tx + bw * 0.62, ty + bh * 0.9),
                    (tx + bw * 0.69, ty + bh + 13),
                    (tx + bw * 0.76, ty + bh * 0.84),
                ]
                draw.polygon(tail, fill="white")
                draw.line(tail, fill="black", width=2)
                points = " ".join(f"{a},{b}" for a, b in tail)
                texts.append(
                    f'<polyline points="{points}" fill="white" stroke="black" stroke-width="2"/>'
                )
            elif t["kind"] == "narration":
                draw.rectangle(
                    (tx, ty, tx + bw, ty + bh), fill="white", outline="black", width=2
                )
                texts.append(
                    f'<rect x="{tx}" y="{ty}" width="{bw}" height="{bh}" fill="white" stroke="black" stroke-width="2"/>'
                )
            text_y = ty + (bh - len(wrapped) * 40) / 2
            for j, line in enumerate(wrapped):
                text_x = tx + (bw - font.getlength(line)) / 2
                draw.text(
                    (text_x, text_y + j * 40),
                    line,
                    font=font,
                    fill="black",
                    stroke_width=2 if t["kind"] == "sfx" else 0,
                    stroke_fill="white",
                )
                outline = (
                    ' stroke="white" stroke-width="4" paint-order="stroke fill"'
                    if t["kind"] == "sfx"
                    else ""
                )
                texts.append(
                    f'<text x="{text_x}" y="{text_y + 30 + j * 40}" font-family="{font_family}" font-size="30"{outline}>{html.escape(line)}</text>'
                )
            texts.append("</g>")
            next_y = ty + bh + 20
    bid = uuid.uuid4().hex
    folder = repo.folder(pid) / "layouts" / bid
    folder.mkdir(parents=True)
    canvas.save(folder / "page.png")
    opening = f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">'
    (folder / "page.svg").write_text(
        opening
        + '<rect width="100%" height="100%" fill="white"/><g id="art">'
        + "".join(art)
        + '</g><g id="lettering">'
        + "".join(texts)
        + "</g></svg>",
        encoding="utf-8",
    )
    (folder / "lettering.svg").write_text(
        opening + "".join(texts) + "</svg>", encoding="utf-8"
    )
    e.save(folder / "layout.json", layout)
    e.save(folder / "lettering.json", page["lettering"])
    e.save(
        folder / "manifest.json",
        {
            "page_id": page_id,
            "project_hash": e.digest(p),
            "version": p["version"],
            "selections": selected,
            "status": "用户选图后的排版草稿，需检查气泡遮挡与位置",
            "warnings": warnings,
        },
    )
    return {
        "bundle": bid,
        "warnings": warnings,
        "files": [
            "page.png",
            "page.svg",
            "lettering.svg",
            "layout.json",
            "lettering.json",
            "manifest.json",
        ],
    }
