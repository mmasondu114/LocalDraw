"""Find Anima character LoRAs, retain source evidence, download and verify hashes."""
import concurrent.futures
import hashlib
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from comic_workflow import engine as e
from comic_workflow.anima import CHECKPOINT
from comic_workflow.lora_inspection import inspect_lora
from comic_workflow.characters import CharacterLibrary
from curate_character_loras import matches

OUT = ROOT / "data/anima-migration"
TOKEN = (ROOT / "Chenkin_LoRA/civitai-token.txt").read_text().strip()


def fetch(url):
    headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
    if urllib.parse.urlsplit(url).hostname == "civitai.com":
        headers["Authorization"] = "Bearer " + TOKEN
    for attempt in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=25) as response:
                return json.load(response)
        except Exception:
            if attempt == 2:
                raise RuntimeError("API request failed") from None
            time.sleep(attempt + 1)


def search(character):
    path = OUT / "search" / (character["game"] + "-" + character["id"] + ".json")
    if path.exists():
        return e.read(path)
    models, errors = {}, []
    queries = list(dict.fromkeys([character["english"], character["name"], *character["aliases"]]))
    for query in queries:
        url = "https://civitai.com/api/v1/models?" + urllib.parse.urlencode({
            "types": "LORA", "baseModels": "Anima", "limit": 100, "query": query, "nsfw": "true"})
        seen = set()
        for _ in range(30):
            if not url or url in seen:
                break
            seen.add(url)
            try:
                data = fetch(url)
                models.update({m["id"]: m for m in data.get("items", [])})
                url = data.get("metadata", {}).get("nextPage")
            except Exception:
                errors.append(query)
                break
    result = {"character": character, "models": list(models.values()), "errors": errors}
    e.save(path, result)
    print("searched", character["name"], len(models), "errors", len(errors), flush=True)
    return result


def select(roster, models):
    rows = []
    for character in roster:
        if not character["target"]:
            continue
        candidates = []
        for model in models.values():
            # A character mentioned in a style example or a relative's name is not
            # a character identity LoRA. Keep canon appearance rather than age variants.
            title = model["name"].lower()
            if re.search(r"artist style|_style|\bmother\b|羊妈|母女|\badult\b|mature female|大人の姿", title):
                continue
            for version in model.get("modelVersions", []):
                if "anima" not in version.get("baseModel", "").lower():
                    continue
                if version.get("availability", "Public") != "Public" or not matches(model, character, roster, version):
                    continue
                for file in version.get("files", []):
                    if file.get("type") != "Model" or not file["name"].endswith(".safetensors"):
                        continue
                    sha = file.get("hashes", {}).get("SHA256", "").lower()
                    if len(sha) != 64:
                        continue
                    candidates.append({"model_id": model["id"], "model": model["name"],
                        "version_id": version["id"], "version": version["name"], "base_model": version["baseModel"],
                        "file": file, "sha256": sha, "triggers": version.get("trainedWords", []),
                        "score": version.get("stats", {}).get("downloadCount", 0),
                        "page": f"https://civitai.com/models/{model['id']}?modelVersionId={version['id']}"})
        candidates.sort(key=lambda x: (not re.search(r"[&＆]|二合一|2\.9b", x["model"] + x["version"], re.I), x["score"]), reverse=True)
        rows.append({"character": character, "candidates": candidates,
                     "selected": candidates[0] if candidates else None})
    return rows


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected and urllib.parse.urlsplit(newurl).hostname != urllib.parse.urlsplit(req.full_url).hostname:
            redirected.remove_header("Authorization")
        return redirected


def download_one(row):
    c = row["character"]
    errors = []
    for candidate in row["candidates"][:3]:
        f = candidate["file"]
        safe_name = re.sub(r'[<>:"/\\|?*]', '_', c["name"])
        relative = f"anime/{c['game']}/{safe_name}_{candidate['version_id']}.safetensors"
        path = ROOT / "models/loras" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".safetensors.partial")
        try:
            if not path.exists():
                opener = urllib.request.build_opener(SafeRedirect())
                headers = {"User-Agent": "Mozilla/5.0"}
                if urllib.parse.urlsplit(f["downloadUrl"]).hostname == "civitai.com":
                    headers["Authorization"] = "Bearer " + TOKEN
                offset = temporary.stat().st_size if temporary.exists() else 0
                if offset:
                    headers["Range"] = f"bytes={offset}-"
                with opener.open(urllib.request.Request(f["downloadUrl"], headers=headers), timeout=60) as response:
                    resumed = response.status == 206 and response.headers.get("Content-Range", "").startswith(f"bytes {offset}-")
                    with temporary.open("ab" if resumed else "wb") as stream:
                        while block := response.read(1024 * 1024):
                            stream.write(block)
                with temporary.open("rb") as stream:
                    actual = hashlib.file_digest(stream, "sha256").hexdigest()
                if actual != candidate["sha256"]:
                    raise ValueError("SHA256 mismatch")
                temporary.replace(path)
            else:
                with path.open("rb") as stream:
                    if hashlib.file_digest(stream, "sha256").hexdigest() != candidate["sha256"]:
                        raise ValueError("Existing file hash mismatch")
            inspection = inspect_lora(path, checkpoint=CHECKPOINT)
            if inspection["family"] != "Anima":
                raise ValueError("Anima architecture not confirmed in file metadata")
            result = {"character": c, "status": "downloaded_verified", "selected": candidate,
                      "relative_path": relative, "inspection": inspection}
            e.save(OUT / "downloads" / (c["game"] + "-" + c["id"] + ".json"), result)
            print("verified", c["name"], path.stat().st_size, flush=True)
            return result
        except Exception as exc:
            # URLs and credentials never enter error logs.
            errors.append({"version_id": candidate["version_id"], "error": type(exc).__name__})
    result = {"character": c, "status": "download_failed" if row["candidates"] else "no_public_anima_found", "errors": errors}
    e.save(OUT / "downloads" / (c["game"] + "-" + c["id"] + ".json"), result)
    return result


def default_trigger(words):
    from comic_workflow.character_prompt_cleanup import default_trigger as clean_default
    return clean_default(words)


def run():
    roster = e.read(ROOT / "data/character-roster.json")["characters"]
    current = OUT / "arknights-current.json"
    if current.exists():
        from curate_character_loras import ALIASES
        from pypinyin import lazy_pinyin
        known = {c["id"] for c in roster}
        for key, c in e.read(current).items():
            if key not in known and key.startswith("char_") and c["rarity"] == "TIER_6" and c["profession"] not in ("TOKEN", "TRAP"):
                roster.append({"id": key, "name": c["name"], "english": c["appellation"], "game": "明日方舟",
                    "aliases": ALIASES.get(c["name"], []), "target": True, "six_star": True,
                    "temporary": bool(c["isNotObtainable"]), "pinyin": " ".join(lazy_pinyin(c["name"]))})
    e.save(OUT / "roster.json", {"as_of": "2026-10-15", "characters": roster})
    models = {m["id"]: m for p in OUT.glob("*-catalog.json") for m in e.read(p)}
    rows = select(roster, models)
    missing = [row["character"] for row in rows if not row["selected"]]
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        for result in pool.map(search, missing):
            models.update({m["id"]: m for m in result["models"]})
    e.save(OUT / "all-models.json", list(models.values()))
    rows = select(roster, models)
    e.save(OUT / "selection.json", rows)
    print("selected", sum(bool(r["selected"]) for r in rows), "of", len(rows), flush=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(download_one, rows))
    labels_path = ROOT / "data/lora-labels.json"
    labels = e.read(labels_path)
    if not (OUT / "lora-labels-before.json").exists():
        e.save(OUT / "lora-labels-before.json", labels)
    cards = {c["name"]: c for c in CharacterLibrary().listing()}
    override_path = ROOT / "config/anima-lora-trigger-overrides.json"
    overrides = e.read(override_path) if override_path.exists() else {}
    for result in results:
        if result["status"] != "downloaded_verified":
            continue
        c, selected = result["character"], result["selected"]
        override = overrides.get(str(selected["version_id"]), {})
        words = selected["triggers"] or (override.get("words", []) if override.get("sha256") == selected["sha256"] else [])
        labels[result["relative_path"]] = {
            "name": c["name"], "character": c["name"], "character_id": cards.get(c["name"], {}).get("id", ""),
            "trigger_words": default_trigger(words), "weight": 0.7,
            "appearance_prompt": default_trigger(words), "source_trained_words": selected["triggers"],
            "checkpoint": CHECKPOINT, "confirmed": True, "base_model": selected["base_model"],
            "source": selected["page"], "sha256": selected["sha256"], "tested_load": False}
    e.save(labels_path, labels)
    e.save(OUT / "coverage.json", {"checkpoint": CHECKPOINT, "characters": results,
        "verified": sum(r["status"] == "downloaded_verified" for r in results), "total": len(results)})
    print("DONE", sum(r["status"] == "downloaded_verified" for r in results), "/", len(results), flush=True)


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    with (OUT / "loras.log").open("a", encoding="utf-8", buffering=1) as log:
        sys.stdout = log
        sys.stderr = log
        run()
