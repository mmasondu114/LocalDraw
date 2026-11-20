"""Observed review counts, separated by immutable drawing recipe."""

from collections import Counter
from . import comfyui, art_presets, drawing_settings


def review_statistics(repo, pid):
    index = comfyui.drawing_state(repo, pid)
    current = art_presets.signature(drawing_settings.load(repo, pid))
    grouped = {}
    for batch in index["batches"]:
        diagnostic = batch.get("render_options", {}).get("diagnostic", False)
        signature = batch.get("art_preset_hash") or "legacy"
        key = signature + ("-diagnostic" if diagnostic else "")
        group = grouped.setdefault(
            key,
            {
                "key": key,
                "current": signature == current and not diagnostic,
                "diagnostic": bool(diagnostic),
                "counts": Counter(),
                "panel_counts": {},
            },
        )
        for image in batch["images"]:
            verdict = image.get("review", {}).get("verdict", "unreviewed")
            group["counts"][verdict] += 1
            group["panel_counts"].setdefault(batch["panel_id"], Counter())[verdict] += 1
    result = []
    for group in grouped.values():
        counts = dict(group["counts"])
        passed = counts.get("pass", 0)
        rejected = counts.get("reject", 0)
        panels = [
            {
                "id": panel_id,
                "counts": dict(values),
                "rejections_per_pass": values.get("reject", 0) / values["pass"]
                if values.get("pass")
                else None,
            }
            for panel_id, values in sorted(group["panel_counts"].items())
        ]
        result.append(
            {
                "key": group["key"],
                "current": group["current"],
                "diagnostic": group["diagnostic"],
                "counts": counts,
                "panels": len(panels),
                "panel_details": panels,
                "panels_without_pass": sum(
                    not row["counts"].get("pass") for row in panels
                ),
                "rejections_per_pass": rejected / passed if passed else None,
                "reviewed": passed + rejected,
            }
        )
    return {
        "groups": result,
        "note": "仅统计 AI 审核结果，不代表人工满意度，也不预测下一张成功率。未确定、未审核和技术报错不算审核失败。",
    }
