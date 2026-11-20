"""Audit and migrate active character drawing fields, with restorable originals."""

import argparse
import copy
import html
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from comic_workflow import engine as e
from comic_workflow.characters import CARD
from comic_workflow.character_prompt_cleanup import (
    BOILERPLATE, NON_ENGLISH, clean_tags, default_trigger, default_visual_prompt, visual_profile,
)
from comic_workflow.clothing_policy import VISUAL_FIELDS, resolve_prompt, parts, has_clothing

# Names transcribed from the local model's original_display_name, not guessed
# trigger words. They are used only as ordinary identity descriptions.
SOURCE_NAMES = {
    "苏菲·诺伊恩缪拉": "Sophie Neuenmuller (Atelier Sophie)",
    "贵妇人": "Gentildonna (umamusume)",
    "麻比奈百合奈": "Asahina Yurina (Super Real Mahjong P7)",
    "无光之影·阿巴奥阿库": "A-Bao A Qu, the Lightless Shadow (Yu-Gi-Oh!)",
    "美童巫女": "Miko Mido (La Blue Girl)",
    "秧秧": "Yangyang (Wuthering Waves)",
    "约·吉沃克": "Yeo Jiwok (God Bless You)",
    "卡珊德拉·该隐": "Batgirl, Cassandra Cain (DC Comics)",
    "露珠之虫惑魔": "Traptrix Sera (Yu-Gi-Oh!)",
    "黑木智子": "Kuroki Tomoko (Watamote)",
    "维纳斯兽": "Venusmon (Digimon)",
    "希尔法": "Sylpha (I Was Reincarnated as the 7th Prince)",
    "绫濑风香": "Ayase Fuuka (Yotsubato!)",
    "海老冢智": "Ebizuka Tomo (Girls Band Cry)",
    "云韵": "Yun Yun (Battle Through the Heavens)",
    "麻生优子": "Asou Yuuko (Mugen Senshi Valis)",
    "春日部紬": "Kasukabe Tsumugi (VOICEVOX)",
    "魔术女武神": "Magician's Valkyria (Yu-Gi-Oh!)",
    "圣殿的水遣": "Water Enchantress of the Temple (Yu-Gi-Oh!)",
    "薇欧拉": "Viola (BanG Dream! Yumemita)",
    "爱琳·永恒轮回": "Irem (Eternal Return)",
    "贝尔斯塔兽": "Beelstarmon (Digimon)",
    "码丽丝·兵·睡鼠": "Malice <P> Dormouse (Yu-Gi-Oh!)",
    "琉克·最终幻想": "Rikku (Final Fantasy X)",
    "苇月伊织": "Yoshizuki Iori (I's)",
    "雪菲": "Sheffy (Princess Connect)",
    "拉普拉斯·妮姬": "Laplace (Nikke)",
    "码丽丝·后·白色绑缚": "Malice <Q> White Binder (Yu-Gi-Oh!)",
    "魔女工艺掌门·玻璃匠薇儿": "Witchcrafter Madame Verre (Yu-Gi-Oh!)",
    "开膛手杰克·命运系列": "Jack the Ripper (Fate series)",
    "四季夏目": "Shiki Natsume (Cafe Stella to Shinigami no Chou)",
    "俱利伽罗天童": "Kurikara Divincarnate (Yu-Gi-Oh!)",
    "纳兰嫣然": "Nalan Yanran (Battle Through the Heavens)",
    "深度求索鲸娘": "DeepSeek whalemaid",
    "萧薰儿": "Xiao Xun'er (Battle Through the Heavens)",
    "科斯莫斯": "Kos-Mos Re: (Xenoblade Chronicles 2)",
    "码丽丝·后·红心密匿": "Malice <Q> Hearts of Crypter (Yu-Gi-Oh!)",
    "索乌·表妹": "sou (itoko_no_ko)",
    "纳瑞莎·雷文克罗夫特": "Nerissa Ravencroft (Hololive)",
    "诗织·诺薇拉": "Shiori Novella (Hololive)",
    "斯纳菲·虚拟主播": "Snuffy",
}


def legacy_default(words):
    if not words:
        return ""
    first = [w.split(",", 1)[0].strip() for w in words]
    if len(words) > 1 and len(set(first)) == 1:
        return words[0]
    if len(words) > 1 and all("-" in t for t in first) and len({t.split("-", 1)[0].lower() for t in first}) == 1:
        return words[0]
    if all(" | " in w for w in words):
        return words[0].split(" | ", 1)[1]
    return ", ".join(words)


def clean_label(label):
    result = copy.deepcopy(label)
    raw = label.get("source_trained_words", [])
    previous = legacy_default(raw)
    for field in ("trigger_words", "appearance_prompt"):
        value = label.get(field, "")
        if raw and value == previous:
            value = default_trigger(raw)
        result[field] = clean_tags(value, appearance=field == "appearance_prompt")
    return result


def prepare(root=ROOT):
    original_labels = e.read(root / "data/lora-labels.json")
    labels = {k: clean_label(v) for k, v in original_labels.items()}
    # Recover documented triggers that were lost when Krea files were renamed.
    for path in (root / 'data/krea2-downloads/verified').glob('*.json'):
        record = e.read(path)
        version = record.get('version_id')
        for filename, label in labels.items():
            if version and filename.startswith('krea2/') and f'_版本{version}.' in filename and not label['trigger_words']:
                label['source_trained_words'] = record.get('triggers', [])
                label['trigger_words'] = default_trigger(record.get('triggers', []))
                label['appearance_prompt'] = clean_tags(label['trigger_words'], appearance=True)
    originals = [e.read(p) for p in sorted((root / "data/characters/cards").glob("*/current.json"))]
    roster_path = root / "data/character-roster.json"
    roster = {c['name']: c for c in e.read(roster_path).get('characters', [])} if roster_path.exists() else {}
    owners = {}
    for filename, label in labels.items():
        owners.setdefault(label.get('character_id'), []).append((filename, label))
    cards, rows = [], []
    for original in originals:
        card = copy.deepcopy(original)
        profile, source = card['profile'], card['source']
        placeholder = '本地LoRA来源档案' in profile['identity']
        for key in VISUAL_FIELDS:
            value = profile[key].replace('160cm左右', 'approximately 160cm').replace(', height 未录入', '')
            profile[key] = clean_tags(value, appearance=True) or 'unknown'
        files = owners.get(card['id'], [])
        preferred = source.get('primary_loras', {})
        ordered = sorted(files, key=lambda item: (
            item[0] != preferred.get('anime'), item[0] not in preferred.values(),
            not item[1].get('preferred', False), item[0]))
        selected = next(((f, v) for f, v in ordered if v.get('appearance_prompt') or v.get('trigger_words')), None)
        basis = 'Existing visual fields'
        if placeholder:
            profile.update({k: 'unknown' for k in VISUAL_FIELDS})
            if selected and source.get('asset_kind', 'character') == 'character':
                profile.update(visual_profile(selected[1].get('appearance_prompt') or selected[1]['trigger_words']))
                basis = selected[0]
            if profile['identity'] == 'unknown' and source.get('asset_kind', 'character') == 'character':
                name = SOURCE_NAMES.get(card['name']) or roster.get(card['name'], {}).get('english')
                if name and not NON_ENGLISH.search(name):
                    profile['identity'] = name
                    basis = 'Local source name only; appearance is not documented'
                elif selected and len(selected[1].get('source_trained_words', [])) == 1 and len(selected[1]['trigger_words'].split(',')) == 1:
                    # A single author-specified activation token can include an
                    # outfit name; retain it exactly as an identity token.
                    profile['identity'] = selected[1]['trigger_words']
                    profile['clothing'] = 'unknown'
            # These were duplicated import instructions, not authored biography.
            for key, value in list(profile.items()):
                if isinstance(value, str) and '来源资料未核实' in value:
                    profile[key] = 'unknown'
            profile['constraints'] = 'Use only the selected model version. Keep unverified appearance and biography unspecified.'
            profile['observed'] = []
            profile['suggested'] = []
            profile['unknowns'] = ['Visual accuracy has not been verified by rendering.']
        e.jsonschema.validate(profile, CARD)
        base = default_visual_prompt(profile)
        old_profile = original['profile']
        old_tags = [t for k in ('identity','face','hair','eyes','body','accessories','palette','marks')
                    if old_profile.get(k) and not re.match(r'^(?:未知|unknown\b)', old_profile[k], re.I)
                    for t in parts(old_profile[k])]
        old_base = ', '.join(t for t in old_tags if t in parts(old_profile['identity']) or not has_clothing(t))
        if old_base != base and BOILERPLATE.search(old_base):
            # Let the UI upgrade only exact former defaults, preserving custom
            # edits and scene text in browser drafts.
            source['drawing_prompt_cleanup'] = {'previous_defaults': [old_base], 'language': 'en'}
        effective = resolve_prompt('', base, card, [card])
        assert not NON_ENGLISH.search(effective) and not BOILERPLATE.search(effective), card['name']
        warnings = []
        kind = source.get('asset_kind', 'character')
        if kind != 'character':
            warnings.append('资源卡，不是单个人物')
        elif not base:
            warnings.append('缺少可靠英文身份或外观，未猜写提示词')
        elif all(profile[k] == 'unknown' for k in VISUAL_FIELDS if k != 'identity'):
            warnings.append('仅有英文姓名，缺少已确认的外观描述')
        if any(not v.get('trigger_words') for _, v in files):
            warnings.append('部分版本未提供可用触发词')
        variants = []
        for filename, label in files:
            raw = label.get('source_trained_words', [])
            variants.append({'file': filename, 'trigger': label.get('trigger_words', ''),
                             'appearance': label.get('appearance_prompt', ''),
                             'source_variants': raw,
                             'alternatives': [clean_tags(w, appearance=True) for w in raw],
                             'changed': label != original_labels[filename]})
        rows.append({'id': card['id'], 'name': card['name'], 'kind': kind, 'prompt': base,
                     'default_clothing': '' if profile['clothing'] == 'unknown' else profile['clothing'],
                     'effective_prompt': effective, 'basis': basis, 'warnings': warnings,
                     'changed': card != original, 'versions': variants})
        cards.append(card)
    return originals, original_labels, cards, labels, rows


def report_html(rows, summary):
    payload = json.dumps(rows, ensure_ascii=False).replace('<', '\\u003c')
    return '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>全部角色英文提示词检查</title>
<style>body{font:16px/1.6 system-ui;margin:32px auto;max-width:1200px;padding:0 24px;background:#f6f7fb;color:#222}header{position:sticky;top:0;background:#f6f7fb;padding:12px 0}input{width:60%;padding:12px}article{background:white;padding:22px;margin:16px 0;border:1px solid #ddd;border-radius:12px}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f4f5f7;padding:12px;font:14px/1.7 monospace}.warn{color:#934300}summary{cursor:pointer}small{color:#666}button{padding:8px;margin:8px}</style>
<h1>全部角色英文提示词检查</h1><p>''' + html.escape(json.dumps(summary, ensure_ascii=False)) + '''</p>
<p>默认提示词仅包含可见外观；服装可被当前场景覆盖。姓名与界面保留中文。原始来源保留供追溯，不能直接全部拼接。这里只完成文本检查，未声称逐个验证出图相似度。</p>
<header><input id="q" placeholder="搜索角色、英文提示词或文件名"><label><input id="issues" type="checkbox" style="width:auto"> 仅显示待核对项</label><span id="count"></span></header><main></main>
<script>const rows=''' + payload + ''';
const el=(tag,text,parent)=>{const n=document.createElement(tag);n.textContent=text;if(parent)parent.append(n);return n};
function draw(){const q=document.querySelector('#q').value.toLowerCase(),issues=document.querySelector('#issues').checked;
const filtered=rows.filter(r=>(!issues||r.warnings.length)&&JSON.stringify(r).toLowerCase().includes(q));document.querySelector('#count').textContent=' '+filtered.length+' 项';
const root=document.querySelector('main');root.replaceChildren();for(const r of filtered){const a=el('article','',root);el('h2',r.name,a);if(r.warnings.length)el('p',r.warnings.join(' · '),a).className='warn';el('small','依据：'+r.basis,a);el('h3','默认完整提示词',a);el('pre',r.effective_prompt||'（未填写：没有可靠来源）',a);
const d=el('details','',a);el('summary',r.versions.length+' 个 LoRA 版本',d);d.addEventListener('toggle',()=>{if(!d.open||d.dataset.loaded)return;d.dataset.loaded='1';for(const v of r.versions){el('h4',v.file,d);el('small','有效触发词',d);el('pre',v.trigger||'（来源未提供）',d);el('small','该版本外观',d);el('pre',v.appearance||'（未填写）',d);if(v.source_variants.length){const s=el('details','',d);el('summary','来源原始条目（各方案独立，不全部叠加）',s);for(let i=0;i<v.source_variants.length;i++){el('pre',v.source_variants[i],s)}}}})}}
document.querySelector('#q').addEventListener('input',draw);document.querySelector('#issues').addEventListener('change',draw);draw();</script></html>'''


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--out', type=Path)
    args = parser.parse_args()
    originals, original_labels, cards, labels, rows = prepare()
    out = args.out or ROOT / 'output' / ('character-prompts-english-' + e.stamp())
    if args.apply and (out / 'backup').exists():
        raise ValueError('This output already contains an original backup; choose a new --out directory.')
    out.mkdir(parents=True, exist_ok=True)
    summary = {'角色及资源卡': len(cards), 'LoRA版本': len(labels),
               '修改角色卡': sum(a != b for a, b in zip(originals, cards)),
               '修改LoRA版本': sum(v != original_labels[k] for k, v in labels.items()),
               '待核对条目': sum(bool(r['warnings']) for r in rows), '已应用': args.apply}
    if args.apply:
        # Check for concurrent edits before writing anything; preserve originals.
        assert e.read(ROOT / 'data/lora-labels.json') == original_labels, 'LoRA labels changed during audit'
        for original in originals:
            assert e.read(ROOT / 'data/characters/cards' / original['id'] / 'current.json') == original
        e.save(out / 'backup/characters.json', originals)
        e.save(out / 'backup/lora-labels.json', original_labels)
        for original, card in zip(originals, cards):
            if original == card:
                continue
            folder = ROOT / 'data/characters/cards' / card['id']
            card['revision'] += 1
            card['saved_at'] = e.stamp()
            e.save(folder / 'versions' / f"{card['revision']:06}.json", card)
            e.save(folder / 'current.json', card)
        e.save(ROOT / 'data/lora-labels.json', labels)
    e.save(out / 'all-prompts.json', {'summary': summary, 'characters': rows})
    (out / 'all-prompts.html').write_text(report_html(rows, summary), encoding='utf-8')
    (out / 'all-prompts.txt').write_text('\n\n'.join(
        r['name'] + '\n' + r['effective_prompt'] + '\n' + '\n'.join(
            v['file'] + '\nTrigger: ' + v['trigger'] + '\nAppearance: ' + v['appearance'] for v in r['versions'])
        for r in rows), encoding='utf-8')
    print(json.dumps({'summary': summary, 'output': str(out)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
