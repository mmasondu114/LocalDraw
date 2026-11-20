"""Review the completed draft against saved storyboard and local character artwork."""
import copy
import re
import prepare_liyue_english as h
from comic_workflow import prompt_exchange as px, drawing_settings


APPEARANCE = {
    'Xiangling': 'Xiangling has dark blue hair in paired buns, golden eyes, and a black, yellow and red chef outfit; her scene-specific apron remains visible.',
    'Paimon': 'Paimon is a small floating companion with short white hair, blue eyes, a floating gold halo, a white outfit with peach-gold details and a dark blue star-patterned cape.',
    'Yaoyao': 'Yaoyao has light brown hair in two braided buns, reddish-brown eyes, a green capelet and a brown-and-yellow outfit.',
    'Qiqi': 'Qiqi has pale lavender hair with a long braid, pink-purple eyes, a purple hat with a paper talisman, a purple jacket over a light violet outfit and white stockings.',
    'Keqing': 'Keqing has long purple twin tails, purple eyes, a purple dress with detached sleeves and dark stockings.',
    'Hu Tao': 'Hu Tao has long brown twin tails, red eyes, a black hat with a red flower, a dark brown long coat with wide sleeves and black shorts.',
    'Zhongli': 'Zhongli has dark brown hair with amber tips and a low ponytail, amber eyes, a long dark brown-and-gold coat and dark gloves.',
    'Xiao': 'Xiao is a slender young man with short dark teal hair with lighter teal streaks, amber eyes, a white sleeveless top, a purple sash, dark trousers, dark gloves and a green tattoo on his right arm.',
    'Ganyu': 'Ganyu has long light blue hair, purple eyes, curved black-and-red horns, a dark bodysuit with white-and-blue detached sleeves and a red neck cord with a bell.',
    'Ningguang': 'Ningguang has long white hair, red eyes, a white-and-gold high-collared dress with black panels and long dark gloves.',
    'Beidou': 'Beidou has long dark brown hair, a red visible eye and a black eyepatch, a red-and-black dress with gold details and a fur-trimmed shoulder cape.',
    'Xingqiu': 'Xingqiu has short asymmetrical dark blue hair, golden eyes, an ornate dark blue-and-gold coat over a white shirt and black shorts.',
    'Chongyun': 'Chongyun has short pale blue hair, blue eyes and a white-and-blue exorcist outfit with dark gloves.',
    'Shenhe': 'Shenhe has long silver-white hair in a braid, a dark fitted bodysuit with white upper panels and red cord ornaments.',
    'Xianyun': 'Xianyun has long dark hair fading to teal, red-rimmed glasses, a black-and-teal outfit and long white sleeves.',
    'Madame Ping': 'Madame Ping is an elderly woman with a gentle expression.',
}


def main():
    h.idle()
    h.state.update(h.e.read(h.STATUS))
    h.e.need(h.state['phase'] == 'prompt_complete', 'Review already advanced; inspect saved status')
    original = h.REPO.get(h.PID)
    draft = h.e.read(h.REPO.folder(h.PID)/'local-prompts/63e31129181d45bc879c91ccaa690b14/draft.json')
    h.e.need(draft['status']=='complete' and draft['completed']==70, 'Incomplete draft')
    p = copy.deepcopy(original)
    by_id = {panel['id']:panel for page in p['pages'].values() for panel in page['panels']}
    cast = {c['name']:c['id'] for c in p['canon']['characters']}
    # These background figures already appear explicitly in the approved prose.
    # Synchronize structured presence so LoRA routing agrees with the picture.
    additions = {
        'P002-K03': [('Yaoyao','left','down','Continuing preparations in the distant background.','focused'),
                      ('Qiqi','left','down','Continuing preparations in the distant background.','focused')],
        'P010-K04': [('Paimon','right','down','Floating in the background, reacting to the sound from the box.','startled')],
        'P017-K03': [('Hu Tao','right','left','Seated in the background, listening.','smiling')],
    }
    for panel_id, roles in additions.items():
        for name,pos,gaze,action,expression in roles:
            h.e.need(name in by_id[panel_id]['drawing_description'], 'Addition must have explicit prose evidence')
            if not any(r['character_id']==cast[name] for r in by_id[panel_id]['characters']):
                by_id[panel_id]['characters'].append(dict(character_id=cast[name],position=pos,gaze=gaze,action=action,expression=expression))
    h.e.need(p['script']==original['script'] and p['script_source']==original['script_source'], 'Original script changed')
    h.e.validate_project(p, complete=True)
    if p != original:
        p=h.REPO.commit(h.PID,p,h.e.digest(original),{'source':'production-review','reason':'Synchronize background cast already present in approved panel descriptions; preserve script and page actions.'})
    doc=px.export_storyboard(h.REPO,h.PID)
    for row in doc['panels']:
        board=row['storyboard']; key=row['panel_id']
        names=[c['character']['name'] for c in board['characters']]
        # Keep the exact approved visual staging, with verified appearance corrections.
        text=re.sub(r'\s*\([CO]\d{3}\)', '', board['description'])
        text=text.replace('Guoba, a small fox-like creature','Guoba, a small creature')
        text=text.replace('His green eyes','His amber eyes')
        text=text.replace('white hair and green accents','pale lavender hair and a purple outfit')
        text=text.replace('dark blue armor and cloak','a white sleeveless top and dark trousers')
        text=text.replace('intricate details of his armor','details of his glove and sleeve-free arm')
        # Mentioning an off-screen name can cause an extra rendered person.
        text=text.replace('where Hu Tao is implied to be','towards an off-screen speaker')
        text=text.replace('where Qiqi is standing (off-screen)','towards an off-screen speaker')
        text=text.replace('presumably at Hu Tao','towards an off-screen speaker')
        text=text.replace('where Xiao is implied to be','towards an off-screen speaker')
        text=text.replace('where Ganyu is presumably standing','towards an off-screen speaker')
        text=text.replace('towards Xingqiu who is off-screen','towards an off-screen speaker')
        text=text.replace('calling out to Xiao','calling out towards the roof')
        text=text.replace("at Xiao's arrival",'at the arrival of their guest')
        text=text.replace('In his other hand, he still holds the unlit sky lantern.', 'The unlit sky lantern hangs by its handle from the same left hand that holds the paper, leaving his right hand free to gesture.')
        text=text.replace("The composition emphasizes the Traveler's internal decision to invite Xiao.",'')
        text=text.replace('consistent with the previous panel, warm and festive','warm and festive')
        text=text.replace('from the previous page','')
        # Avoid full-body identity descriptions in cropped hands-only shots.
        if key in {'P007-K03','P008-K02','P020-K02'}:
            appearance='Only the hands described in this crop are visible; keep faces and bodies outside the frame.'
        else:
            appearance=' '.join(APPEARANCE[n] for n in names if n in APPEARANCE)
        row['positive']=f"Anime illustration, {board['angle'].replace('_',' ')} view. "+text.strip()+' '+appearance
        row['negative']='text, watermark, speech bubbles, comic borders'
        h.e.need(row['positive'].isascii(), 'Non-English punctuation or text: '+key)
    h.e.need(len(doc['panels'])==70 and len({r['positive'] for r in doc['panels']})==70,'Panel coverage')
    h.e.save(h.FOLDER/'liyue-english-prompts.json',doc)
    h.e.save(h.FOLDER/'review-notes.json',{
        'draft_id':draft['id'],'project_version':p['version'],
        'evidence':'Saved local default character illustrations (appearance-review.jpg); approved panel drawing_description.',
        'cast_sync':additions,'original_script_unchanged':True,
        'appearance_policy':'Replace mixed training-tag appearances with local artwork; preserve shot, action, prop and dialogue; retain original draft.',
        'remaining':'Characters without Anima-compatible LoRAs use visible appearance descriptions.'})
    prefs=drawing_settings.load(h.REPO,h.PID)
    routing={panel['id']:drawing_settings.loras_for_panel(prefs,p,panel) for panel in by_id.values()}
    h.e.need(all(sum(l['file']==h.LORA for l in ls)==1 for ls in routing.values()),'Every panel must have exactly one consistency LoRA')
    h.e.save(h.FOLDER/'lora-routing.json',routing)
    check=h.act('preview_prompt_import',project=h.PID,document=doc)
    h.e.save(h.FOLDER/'prompt-preview.json',check)
    h.e.need(check['valid'] and not check['missing'],'Import validation failed')
    h.e.need(not any(w['code']=='absent_character' for w in check.get('warnings',[])), 'Unresolved cast mismatch')
    h.save(phase='prompts_reviewed',project_version=p['version'],prompt_file=str(h.FOLDER/'liyue-english-prompts.json'))
    print({'phase':'prompts_reviewed','panels':70,'warnings':[(w['code'],len(w.get('panels',[]))) for w in check.get('warnings',[])]})


if __name__=='__main__':
    main()
