"""Historical 28/40/52 floor map for Delta Mix LoRA rewrite. Not Remap-as-merge."""

from __future__ import annotations

import re
from typing import Dict, Iterable, List, Optional, Tuple

BASE28_TO_52: Tuple[int, ...] = (
    0, 1, 4, 5, 8, 9, 12, 13, 16, 17, 20, 21, 24, 25,
    26, 29, 30, 33, 34, 37, 38, 41, 42, 45, 46, 49, 50, 51,
)

BASE28_TO_40: Tuple[int, ...] = (
    0, 1, 3, 4, 6, 7, 9, 10, 12, 13, 15, 16, 18, 19,
    20, 22, 23, 25, 26, 28, 29, 31, 32, 34, 35, 37, 38, 39,
)

GEN1_40_TO_52: Dict[int, int] = {
    2: 2, 5: 6, 8: 10, 11: 14, 14: 18, 17: 22,
    21: 28, 24: 32, 27: 36, 30: 40, 33: 44, 36: 48,
}

GEN2_52 = frozenset((3, 7, 11, 15, 19, 23, 27, 31, 35, 39, 43, 47))
WATCH_52 = frozenset((7, 15, 23, 31))

_BLOCK_RE = re.compile(r"blocks([._])(\d+)")
_SKIP_RE = re.compile(
    r"llm_adapter|expanded_adapter|anima_v2_connector|anima_connector",
    re.I,
)


def parse_dit_block(key: str) -> Optional[int]:
    if _SKIP_RE.search(key):
        return None
    match = _BLOCK_RE.search(key)
    if not match:
        return None
    return int(match.group(2))


def rewrite_block(key: str, new_idx: int) -> str:
    return _BLOCK_RE.sub(lambda m: f"blocks{m.group(1)}{new_idx}", key, count=1)


def collect_block_indices(keys: Iterable[str]) -> List[int]:
    return sorted({i for k in keys if (i := parse_dit_block(k)) is not None})


def detect_lora_family(keys: Iterable[str]) -> str:
    blocks = collect_block_indices(keys)
    if not blocks:
        return "none"
    hi = max(blocks)
    if hi <= 27:
        return "28"
    if hi <= 39:
        return "40"
    return "52"


def map_28_to_52(old: int) -> Optional[int]:
    if 0 <= old < len(BASE28_TO_52):
        return BASE28_TO_52[old]
    return None


def map_40_to_52(idx40: int) -> Optional[int]:
    if idx40 in GEN1_40_TO_52:
        return GEN1_40_TO_52[idx40]
    try:
        old = BASE28_TO_40.index(idx40)
    except ValueError:
        return None
    return BASE28_TO_52[old]


def remap_lora_keys(
    raw: dict,
    family: str,
    skip_gen2: bool = True,
    skip_watch: bool = True,
) -> tuple[dict, dict]:
    out = {}
    stats = {
        "kept": 0,
        "mapped": 0,
        "dropped_gen2": 0,
        "dropped_watch": 0,
        "dropped_unknown": 0,
        "non_dit": 0,
        "dropped_interface": 0,
    }
    for key, tensor in raw.items():
        if _SKIP_RE.search(key):
            stats["dropped_interface"] += 1
            continue
        idx = parse_dit_block(key)
        if idx is None:
            out[key] = tensor
            stats["non_dit"] += 1
            continue
        if family == "52":
            new_idx = idx
        elif family == "40":
            new_idx = map_40_to_52(idx)
        elif family == "28":
            new_idx = map_28_to_52(idx)
        else:
            new_idx = None
        if new_idx is None:
            stats["dropped_unknown"] += 1
            continue
        if skip_watch and new_idx in WATCH_52:
            stats["dropped_watch"] += 1
            continue
        if skip_gen2 and new_idx in GEN2_52:
            stats["dropped_gen2"] += 1
            continue
        new_key = rewrite_block(key, new_idx) if new_idx != idx else key
        out[new_key] = tensor
        stats["kept" if new_key == key else "mapped"] += 1
    return out, stats
