"""Freeze game-default official illustrations rehosted by the game wiki."""
import hashlib
import json
from pathlib import Path
import sys
import urllib.parse
import urllib.request
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from comic_workflow import engine as e


def fetch(url):
    req = urllib.request.Request(url, headers={'User-Agent': 'LocalDraw-OfficialReferenceAudit/1.0'})
    with urllib.request.urlopen(req, timeout=40) as response:
        return response.read()


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    folder = ROOT / 'data/lora-curation/research/gap-pack-official-art'
    folder.mkdir(parents=True, exist_ok=True)
    records = []
    for name in ('灵知', '圣约送葬人', '伺夜', 'Sharp', 'Stormeye', 'Pith'):
        title = '文件:Pack ' + name + ' skin 0 0.png'
        url = 'https://wiki.biligame.com/arknights/api.php?' + urllib.parse.urlencode({
            'action': 'query', 'format': 'json', 'titles': title, 'prop': 'imageinfo', 'iiprop': 'url|size|sha1'})
        record = {'name': name, 'source_page': 'https://wiki.biligame.com/arknights/' + urllib.parse.quote(name),
                  'source_type': 'official_game_illustration_rehosted_by_game_wiki',
                  'official_hosting_domain': False, 'asset_title': title, 'metadata_url': url}
        try:
            info = json.loads(fetch(url))
            e.save(folder / (name + '-asset.json'), info)
            page = next(p for p in info['query']['pages'].values() if p.get('imageinfo'))
            asset = page['imageinfo'][0]
            data = fetch(asset['url'])
            path = folder / (name + '.png')
            path.write_bytes(data)
            with Image.open(path) as image:
                image.verify()
            with Image.open(path) as image:
                record['dimensions'] = list(image.size)
            record.update(asset_url=asset['url'], file=path.relative_to(ROOT).as_posix(),
                          sha256=hashlib.sha256(data).hexdigest(), status='downloaded_pending_visual_inspection')
        except Exception as exc:
            record.update(status='missing_reference', reason=type(exc).__name__)
        records.append(record)
        e.save(folder / 'provenance.json', records)
        print(name + ': ' + record['status'], flush=True)


if __name__ == '__main__':
    main()
