"""Collect public wiki artwork metadata and character stories in Chrome."""
import json
import re
import sys
import urllib.parse
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from comic_workflow import engine as e

DEST = ROOT / "data/character-import"
groups = e.read(DEST / "plan.json")["groups"]
names = [g["name"] for g in groups if g["game"] == "原神"]


def run():
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page()
        page.goto("https://wiki.biligame.com/ys/" + urllib.parse.quote("爱诺"), wait_until="domcontentloaded")

        def query(parameters, path):
            if path.exists():
                return e.read(path)
            url = "https://wiki.biligame.com/ys/api.php?" + urllib.parse.urlencode(parameters)
            result = page.evaluate("async url=>{const response=await fetch(url); if(!response.ok)throw Error(response.status);return await response.json()}", url)
            e.save(path, result)
            return result

        for start in range(0, len(names), 8):
            batch = names[start:start + 8]
            titles = ["文件:" + n + "立绘.png" for n in batch]
            if "罗诺瓦" in batch:
                titles += ["文件:若娜瓦.png"]
            titles += ["文件:" + n + ".png" for n in batch if n in ("伊斯塔露", "留云借风真君", "派蒙")]
            data = query({"action": "query", "format": "json", "prop": "imageinfo", "iiprop": "url|size|sha1",
                          "titles": "|".join(titles)}, DEST / f"research/genshin-art-{start}.json")
            print("原神立绘索引", min(start + 8, len(names)), "/", len(names), flush=True)

        stories = {}
        for start in range(0, len(names), 15):
            batch = names[start:start + 15]
            titles = ["若娜瓦" if n == "罗诺瓦" else n for n in batch]
            data = query({"action": "query", "format": "json", "titles": "|".join(titles),
                          "prop": "revisions", "rvprop": "content", "rvslots": "main", "redirects": "1"},
                         DEST / f"research/genshin-stories-{start}.json")
            for record in data["query"]["pages"].values():
                if not record.get("revisions"):
                    continue
                text = record["revisions"][0]["slots"]["main"]["*"]
                name = "罗诺瓦" if record["title"] == "若娜瓦" else record["title"]
                fields = {}
                for key in ("介绍", "所属", "角色详细", "角色故事1", "角色故事2", "初次见面", "闲聊·习惯", "闲聊·工作"):
                    match = re.search(r"(?:^|\n)\|" + re.escape(key) + r"=(.*?)(?=\n\||\n}})", text, re.S)
                    if match:
                        fields[key] = re.sub(r"<[^>]+>", "\n", match[1]).strip()
                if fields:
                    stories[name] = fields
            print("角色设定来源", min(start + 15, len(names)), "/", len(names), flush=True)
        e.save(DEST / "research/genshin-stories.json", stories)
        browser.close()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    run()
