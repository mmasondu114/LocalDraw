"""New-work consistency pilot using only the studio's existing public API.

Create is resumable; submission is single-panel and fails closed after an
unknown outcome. Technical validation never grants visual approval.
"""
import argparse
import copy
import hashlib
import json
import sys
from pathlib import Path

import prepare_liyue_english as h  # helpers only; never call its production main
from comic_workflow.characters import CharacterLibrary
from comic_workflow.prompt_exchange import export_storyboard
from comic_workflow.batch_drawing import story_fingerprint
from comic_workflow import drawing_settings

ROOT = h.ROOT
HOME = ROOT / 'data' / 'consistency-pilot-20261025'
STATE = HOME / 'state.json'
MODEL = 'animaDeltaMix38BQwen0_deltaMixV60.safetensors'
STYLE = ('Polished dimensional 3D anime game cinematic, sculpted facial planes, '
         'natural warm skin midtones, detailed cloth and metal materials, soft '
         'physically coherent shadows, restrained bloom, coherent proportions. '
         'Night in Liyue, warm amber practical light and cool blue ambient fill. '
         'One continuous full-bleed image, no graphic layout.')
NEGATIVE = ('text, letters, caption, logo, watermark, character sheet, collage, '
            'inset portrait, duplicate person, extra person, floating face, '
            'chalk-white skin, overexposed face, flat cartoon, chibi, animal ears, '
            'extra limbs, missing limbs, fused hands, deformed face, modern clothes')
K = ('Keqing (Genshin Impact), violet eyes, lavender long twin tails and two '
     'pointed hair buns made of hair, slim gold hair ornament, gold-edged blue '
     'diamond earrings, purple choker, pale lavender chest panel, dark purple '
     'gold-trimmed short layered dress, detached lavender sleeves, purple '
     'gloves, patterned dark tights and purple shoes. Natural fair warm skin.')
A = ('Aether (Genshin Impact), young adult man, amber eyes, golden blond hair '
     'with one long braid, human ears, black cropped top with gold details, '
     'white and gold scarf-cape, brown black trousers, dark gloves and dark '
     'gold-trimmed boots, exposed midriff. Natural fair warm skin.')
DOCK = ('A quiet stone dock outside one dark timber storehouse. One closed '
        'blue wooden door in the center of its back wall, a small fixed amber '
        'wall lamp to the left of the door, low stone parapet on the right '
        'beside the dark harbor. No signs or other people. Dry pale stone paving.')
BRIDGE = ('A narrow stone footbridge with low gray stone parapets on both '
          'sides, dark water below, distant tiled roofs. No other people, no signs.')
BOX = ('Exactly one small rectangular walnut-brown wooden keepsake box, '
       'flat lid, one centered small brass latch, no writing or decoration.')

# Each entry remains independent of prior generated pixels or conversation text.
SHOTS = [
 ('S001',['C001'],'medium full shot','front three-quarter',
  'Keqing stands at the left of the closed blue door, empty hands relaxed at her sides, looking toward the right edge.','刻晴在仓门前等候空。',False),
 ('S001',['C002'],'medium full shot','front three-quarter',
  'Aether stands at the right of the closed blue door, holding the closed small box at waist height with both hands, looking toward the left edge.','空带着拾到的小木匣来到码头。',True),
 ('S001',['C001','C002'],'wide two-person shot','eye level',
  'Keqing stands on the left and Aether stands on the right, separated by one arm length, looking at each other. Only Aether holds the closed box at waist height.','二人商定沿石桥寻找失主。',True),
 ('S001',['C001'],'waist-up shot','right-facing profile',
  'Keqing turns her head to the right, thoughtful expression, hands below the frame. The closed blue door and its left wall lamp remain behind her.','刻晴辨认出木匣来自桥对面的库房。',False),
 ('S002',['C002'],'waist-up shot','left-facing profile',
  'Aether looks left with a small attentive smile, holding the closed box at waist height. The stone bridge parapet is behind him.','空在石桥上停步确认方向。',True),
 ('S002',['C001','C002'],'wide two-person shot','side view',
  'Keqing walks on the left and Aether on the right across the bridge toward frame right. Aether carries the closed box in both hands at waist height. Full figures, no other people.','二人并肩穿过石桥。',True),
 ('S002',['C001'],'medium full shot','front three-quarter',
  'Keqing stands beside the bridge parapet, one open hand indicating the direction to frame left, her other arm relaxed.','库房无人，刻晴决定返回码头保管失物。',False),
 ('S002',['C002'],'medium full shot','front three-quarter',
  'Aether stands beside the parapet, still holding the same closed box at waist height with both hands, looking left.','空抱稳木匣准备原路返回。',True),
 ('S001',['C001'],'medium full shot','front three-quarter',
  'Keqing stands at the left of the closed blue door, empty hands relaxed at her sides, looking toward the right edge.','返回同一码头，刻晴在仓门前停下。',False),
 ('S001',['C002'],'medium full shot','front three-quarter',
  'Aether stands at the right of the closed blue door, holding the closed small box at waist height with both hands, looking toward the left edge.','空带着原封未动的木匣归来。',True),
 ('S001',['C001','C002'],'wide two-person shot','eye level',
  'Keqing stands on the left and Aether stands on the right, separated by one arm length, looking at each other with calm smiles. Only Aether holds the closed box at waist height.','二人约好天亮再寻找失主。',True),
 ('S001',['C001'],'head and shoulders close-up','front three-quarter',
  'Keqing looks toward the right edge with a gentle confident smile, shoulders relaxed. Softly blurred blue door behind her, no other person.','刻晴说：明早再来，总会找到的。',False),
]


def load():
    return h.e.read(STATE) if STATE.exists() else {}


def save(s):
    s['updated'] = h.e.stamp()
    h.e.save(STATE, s)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def project(s):
    return h.REPO.get(s['project'])


def create():
    s = load()
    lib = CharacterLibrary()
    chars = []
    for cid, name, appearance in [('C001','刻晴',K),('C002','空',A)]:
        chars.append(dict(id=cid,name=name,origin='existing_work',
            source_notes=['Local official artwork visually inspected before pilot creation.'],
            verified_facts=[appearance],adaptation_choices=['Original fan story: returning a lost box.'],
            unknowns=[],appearance=appearance,voice='Brief and considerate.',sample_lines=[],
            motivation='Keep the lost keepsake safe until its owner returns.',
            taboos=['No costume change, identity swap or duplicate body.']))
    brief=dict(title='夜巡归匣 · 长篇一致性试验',genre='温暖夜巡同人短篇',
        direction='Six-page controlled pilot before long-form production. Fixed identities, style, costumes, two recurring locations and one closed box.',
        page_count=6,panels_per_page=2,panel_mode='fixed',reading_direction='LTR',
        page_mode='single_page',first_story_page_side='right',
        must_include=['Same costumes throughout.','Return to the exact original dock.','One closed box remains with Aether.'],
        avoid=['Changing identity or materials between panels.','Reference pose copied instead of the requested action.'],
        character_material='/刻晴 /空 ',character_refs=[lib.get('刻晴'),lib.get('空')])
    canon=dict(characters=chars,scenes=[dict(id=sid,name=name,layout=layout,
        landmarks=marks,lighting='Amber practical light with cool blue night ambient.',
        axis_rule='Keqing left and Aether right for front views; the harbor is to the right of the storehouse.')
        for sid,name,layout,marks in [('S001','码头仓门',DOCK,['blue door','left wall lamp','right parapet']),
                                     ('S002','石桥',BRIDGE,['gray parapets','dark water'])]],
        props=[{'id':'O001','description':BOX}],world_rules=['No outfit, weather, time-of-night or prop redesign.'],
        initial_state={'box':'Closed, held by Aether throughout.','costumes':'Canonical default costumes throughout.'})
    beats=[r[5] for r in SHOTS]
    payload=dict(story='《夜巡归匣》\n\n'+'\n'.join(beats),brief=brief,canon=canon,
        outline=dict(selected_option='direct',premise='Two travelers try to return a lost keepsake without opening it.',
            beats=beats,ending=beats[-1],must_include_mapping=beats,unknowns=[]),
        assumptions=['User delegated story choice. Six pages test recurrence; they do not prove unlimited-length perfection.'],changes=[])
    settings=h.e.read(h.REPO.folder(h.PID)/'drawing-settings.json')
    keep={'anime/原神/刻晴_3014722.safetensors','anime/原神/空_3013733.safetensors','anime/edit/AnimeEditV2.safetensors'}
    settings={k:v for k,v in settings.items() if k in ('mode','checkpoint','items','resolution')}
    settings.update(mode='characters',checkpoint=MODEL,consistency_lora=True,hand_detail=False)
    settings['items']=[copy.deepcopy(x) for x in settings['items'] if x['file'] in keep]
    for x in settings['items']: x['selected']=True
    if not s.get('project'):
        h.idle()
        # Reconcile an interrupted create by its unique title instead of duplicating.
        matches=[p for p in h.get('/api/bootstrap')['projects'] if p['title']==brief['title']]
        h.e.need(len(matches)<=1,'Duplicate pilot titles; resolve before creating.')
        if matches:
            s['project']=matches[0]['id']
        else:
            h.e.save(HOME/'story-request.json',payload)
            s.update(phase='creating',title=brief['title']);save(s)
            result=h.act('create_story',result={'payload':payload,'mode':'authored','original':payload['story']},drawing_settings=settings)
            s['project']=result['project'];save(s)
    pid=s['project']
    pages=[];plan=[]
    for n in range(1,7):
        page_id=f'P{n:03}'
        rows=SHOTS[(n-1)*2:n*2]
        panels=[]
        for k,(scene,cast,shot,angle,instant,zh,box) in enumerate(rows,1):
            panels.append(dict(id=f'{page_id}-K{k:02}',page_id=page_id,number=k,purpose=zh,
                layout='One full-bleed frame.',aspect_ratio='4:3',shot=shot,angle=angle,
                composition=instant,scene_id=scene,
                characters=[dict(character_id=c,position='left' if c=='C001' else 'right',
                    gaze='As specified in the main instant.',action=instant,expression='Calm and natural.') for c in cast],
                main_instant=instant,background=DOCK if scene=='S001' else BRIDGE,
                props=[dict(prop_id='O001',position='Both of Aether’s hands at waist height.',state='Closed.')] if box else [],
                transition_in='Continuous journey, identical outfit and night lighting.',
                transition_out='Next beat of the same journey.',state_after=canon['initial_state'],drawing_description=instant))
        pages.append(dict(id=page_id,number=n,reading_direction='LTR',layout='Two stacked wide panels.',
            page_turn_note='Continue the same uninterrupted journey.',state_in=canon['initial_state'],
            panels=panels,lettering=[],state_out=canon['initial_state']))
        plan.append(dict(id=page_id,number=n,scene_ids=list(dict.fromkeys(r[0] for r in rows)),
            character_ids=list(dict.fromkeys(c for r in rows for c in r[1])),panel_count=2,
            purpose='Continuity test through a story beat.',beat=' '.join(r[5] for r in rows),
            hook='',reveal='',turn_after=True,constraints=['Preserve reference identities, costumes, geography and box.']))
    p=project(s)
    if not p.get('plan'): accept(pid,'plan','',{'pages':plan})
    for page in pages:
        if page['id'] not in project(s)['pages']: accept(pid,'page',page['id'],page)
    p=project(s)
    doc=export_storyboard(h.REPO,pid)
    for row,spec in zip(doc['panels'],SHOTS):
        scene,cast,shot,angle,instant,_,box=spec
        anchors=' '.join(K if c=='C001' else A for c in cast)
        row['positive']=f'{shot}, {angle}. Exactly {len(cast)} visible person'+('s. ' if len(cast)>1 else '. ')+instant+' '+anchors+' '+(BOX+' ' if box else '')+(DOCK if scene=='S001' else BRIDGE)+' '+STYLE
        row['negative']=NEGATIVE
    h.e.save(HOME/'prompts.json',doc)
    preview=h.act('preview_prompt_import',project=pid,document=doc)
    h.e.need(preview['valid'],str(preview['errors']))
    h.e.save(HOME/'preview.json',preview)
    reference_records={}
    for cid,name in [('C001','刻晴'),('C002','空')]:
        card=next(c for c in p['brief']['character_refs'] if c['name']==name)
        path=lib.image_path(card['image_id'])
        reference_records[cid]=dict(name=name,image_id=card['image_id'],path=str(path.resolve()),sha256=sha(path),card_hash=h.e.digest(card))
    pack=dict(schema='studio-consistency-pack/v1',project=pid,storyboard_hash=story_fingerprint(p),
        model=MODEL,style=STYLE,negative=NEGATIVE,characters=chars,scenes=canon['scenes'],props=canon['props'],
        references=reference_records,settings=settings,settings_hash=h.e.digest(settings),
        lora_files={x['file']:sha(ROOT/'models/loras'/x['file']) for x in settings['items']},
        candidate_count=4,steps=28,reference_policy='Every solo panel uses its immutable original anchor. Never chain previous outputs.',
        limitations=['Current API cannot independently bind two character images in one Anima group shot.',
                    'Scene/prop descriptions are fixed, but no independent scene/prop image conditioning is exposed.',
                    'Technical checks and matching hashes are not visual consistency approval.'],
        visual_gate=['Identity and facial proportions','Costume and accessories','Dimensional 3D style and skin',
                     'Character count and ownership','Requested pose and composition','Location geometry and light',
                     'Box shape, count and holder','Anatomy and unwanted text'],
        cross_panel_pairs=[['P001-K01','P005-K01'],['P001-K02','P005-K02'],['P002-K01','P006-K01']])
    if (HOME/'pack.json').exists():
        h.e.need(h.e.read(HOME/'pack.json')==pack,'Frozen pack differs; create an explicit new pack revision.')
    else: h.e.save(HOME/'pack.json',pack)
    s.update(phase=s.get('phase') if s.get('jobs') else 'ready_for_pilot',
        pack_hash=h.e.digest(pack),candidate_count=4,
        queue=s.get('queue',['P001-K01','P001-K02','P005-K01','P005-K02','P002-K01','P006-K01','P002-K02','P003-K01','P003-K02','P004-K01','P004-K02','P006-K02']),
        jobs=s.get('jobs',[]),visual_reviews=s.get('visual_reviews',{}),selected_images={})
    save(s)
    print(json.dumps({'project':pid,'panels':12,'pack_hash':s['pack_hash'],'phase':s['phase']},ensure_ascii=False))


def accept(pid,stage,target,payload):
    p=h.REPO.get(pid)
    c=h.act('propose',project=pid,stage=stage,target=target,payload=payload,base_hash=h.e.digest(p))
    h.act('accept',project=pid,candidate=c['id'])


def check_pack(s):
    pack=h.e.read(HOME/'pack.json')
    h.e.need(h.e.digest(pack)==s['pack_hash'],'Pack changed; do not silently continue.')
    h.e.need(story_fingerprint(project(s))==pack['storyboard_hash'],'Storyboard changed.')
    doc=h.e.read(HOME/'prompts.json')
    h.e.need(h.e.digest(doc)==h.e.read(HOME/'preview.json')['preview_hash'],
             'Prompt document changed; create an explicit reviewed revision.')
    current=copy.deepcopy(drawing_settings.load(h.REPO,s['project']))
    current.pop('reference_character_id',None)
    h.e.need(current==pack['settings'],'Drawing settings changed; do not silently overwrite them.')
    for ref in pack['references'].values():
        h.e.need(sha(ref['path'])==ref['sha256'],'Reference pixels changed.')
    for file,digest in pack['lora_files'].items():
        h.e.need(sha(ROOT/'models/loras'/file)==digest,'LoRA file changed.')
    return pack


def validate_visual_pass(review, manifest):
    """A group flag alone must never unlock more generation."""
    h.e.need(review.get('group_verdict')=='pass' and review.get('all_candidates_viewed') is True,
             'Previous group has not visually passed.')
    records=review.get('records',[])
    h.e.need(len(manifest)==4 and len(records)==4 and
             len({r.get('image_id') for r in records})==4 and
             {r.get('image_id') for r in records}=={m['image_id'] for m in manifest},
             'Visual review must cover the exact four actual candidates.')
    h.e.need(all(r.get('visual_verdict')=='pass' and not r.get('issues') and
                 r.get('inspection') and r.get('evidence') for r in records),
             'Missing inspection evidence or unresolved visual issue.')
    h.e.need(all(m.get('frontend_original_verified') for m in manifest),
             'Original-image access not verified.')


def submit():
    s=load();pack=check_pack(s);h.idle()
    h.e.need(not s.get('pending_submission'),'Reconcile unknown submission before trying again.')
    latest={a['panel_id']:a for a in s['jobs']}
    for prior in latest.values():
        job=h.get('/api/job?id='+prior['job'])
        h.e.need(job['status']=='done','Earlier pilot job is not done; inspect/recover it first.')
        prefix=prior.get('artifact_prefix',prior['panel_id'])
        h.e.need((HOME/(prefix+'-review.json')).exists(),'Review the previous group before continuing.')
        validate_visual_pass(h.e.read(HOME/(prefix+'-review.json')),h.e.read(HOME/prior['manifest']))
    h.e.need(s['queue'],'No unsubmitted pilot panels remain.')
    target=s['queue'][0]
    panel=next(r for pg in project(s)['pages'].values() for r in pg['panels'] if r['id']==target)
    cast=[c['character_id'] for c in panel['characters']]
    h.e.need(len(cast)==1,'Group-shot reference control needs a separately documented method; no blind group batch.')
    settings=copy.deepcopy(pack['settings']);settings['reference_character_id']=cast[0]
    h.act('save_drawing_settings',project=s['project'],settings=settings)
    doc=h.e.read(HOME/'prompts.json');doc['panels']=[r for r in doc['panels'] if r['panel_id']==target]
    pv=h.act('preview_prompt_import',project=s['project'],document=doc)
    h.e.need(pv['valid'],str(pv['errors']))
    bundle=h.act('save_prompt_import',project=s['project'],document=doc,preview_hash=pv['preview_hash'],confirm=True)
    attempt=dict(panel_id=target,bundle=bundle['id'],phase='submitting',expected_reference=pack['references'][cast[0]],
        parent_round=None,candidate_count=4)
    s['pending_submission']=attempt;save(s)
    result=h.act('draw_imported',project=s['project'],bundle=bundle['id'],targets=[target],candidate_count=4,steps=pack['steps'],checkpoint=pack['model'])
    attempt.update(job=result['job'],phase='submitted')
    s['jobs'].append(attempt);s['queue'].pop(0);s.pop('pending_submission');s['phase']='pilot_running';save(s)
    job=h.get('/api/job?id='+result['job'])
    h.e.save(HOME/(target+'-job.json'),job)
    req=job['request'];attempt['run_key']=req['run_key'];save(s)
    frozen=req['references_by_panel'][target]
    h.e.need(frozen['reference_sha256']==attempt['expected_reference']['sha256'],'Frozen reference mismatch; inspect job immediately.')
    print(json.dumps({'project':s['project'],**attempt},ensure_ascii=False))


def audit():
    """Verify actual image workflows and original-image access, never visual pass."""
    s=load();pack=check_pack(s)
    if not s['jobs']:
        print('No submitted pilot job.');return
    a=s['jobs'][-1];j=h.get('/api/job?id='+a['job'])
    prefix=a.get('artifact_prefix',a['panel_id'])
    h.e.save(HOME/(prefix+'-job.json'),j)
    root=h.REPO.folder(s['project'])
    rp=root/'runs'/a['job']/'imported-drawing.json'
    errors=h.e.read(rp).get('errors',[]) if rp.exists() else []
    batches=[b for b in h.get('/api/images?id='+s['project'])['images']['batches']
             if b.get('render_options',{}).get('import_run')==a['run_key']]
    s['last_monitor']=dict(job=a['job'],status=j['status'],errors=errors,
        saved=sum(len(b.get('images',[])) for b in batches))
    save(s)
    if j['status']!='done':
        print(json.dumps(s['last_monitor'],ensure_ascii=False));return
    h.e.need(not errors and not j.get('result',{}).get('errors'),'Generation has errors; preserve run for diagnosis.')
    req=j['request'];target=a['panel_id']
    h.e.need(req['candidate_count']==4 and len(batches)==4,'Expected exactly four candidate batches.')
    expected={x['file'].replace('\\','/'):x['weight'] for x in req['loras_by_panel'][target]}
    h.e.need(expected.get('anime/edit/AnimeEditV2.safetensors')==.8 and len(expected)<=5,'Unexpected LoRA routing.')
    h.e.need(req['checkpoint']==MODEL and req['steps']==pack['steps'],'Model/steps differ.')
    ref=req['references_by_panel'].get(target)
    if a.get('expected_reference'):
        h.e.need(ref and ref['reference_sha256']==a['expected_reference']['sha256'],'Frozen reference changed.')
        h.e.need(sha(ROOT/'data/comfy-input'/ref['reference'])==ref['reference_sha256'],'Reference input pixels changed.')
    else:
        h.e.need(ref is None and a.get('method')=='clean_anchor_calibration','Unexpected reference-free run.')
    row=next(r for r in h.e.read(HOME/a.get('prompt_file','prompts.json'))['panels'] if r['panel_id']==target)
    manifest=[]
    for b in batches:
        h.e.need(b['status']=='done' and len(b['images'])==1,'Incomplete batch.')
        im=b['images'][0];path=root/'images'/im['file']
        w=h.e.read(path.parent/'workflow-1.json')
        actual={n['inputs']['lora_name'].replace('\\','/'):n['inputs']['strength_model']
                for n in w.values() if n.get('class_type')=='DeltaMixQ35LoraLoader'}
        h.e.need(actual==expected,'Actual workflow LoRAs differ.')
        h.e.need(w['1']['inputs']['unet_name']==MODEL,'Actual checkpoint differs.')
        h.e.need(w['2']['inputs']['text']==row['positive'] and w['3']['inputs']['text']==row['negative'],'Actual prompt differs.')
        h.e.need(w['5']['inputs']['seed']==im['seed'],'Seed differs.')
        if ref:
            h.e.need(w['consistency-image']['inputs']['image']==ref['reference'] and
                     w['consistency-reference']['class_type']=='ApplyCosmosReferenceLatent' and
                     w['5']['inputs']['model']==['consistency-reference',0],'Reference is not connected to sampler.')
        else:
            h.e.need(not any(n.get('class_type')=='ApplyCosmosReferenceLatent' for n in w.values()),'Unexpected reference node in calibration.')
        from PIL import Image
        with Image.open(path) as image: image.verify()
        url=h.BASE+'/api/image?id='+s['project']+'&bundle='+b['id']+'&file='+path.name
        with h.OPENER.open(url,timeout=20) as response:
            h.e.need(response.status==200 and response.read()==path.read_bytes(),'Frontend original-image mismatch.')
        manifest.append(dict(image_id=im['id'],panel_id=target,round=a.get('round',1),file=str(path.resolve()),
            seed=im['seed'],image_sha256=sha(path),workflow=str(path.parent/'workflow-1.json'),
            reference=ref,loras=actual,url=url,frontend_original_verified=True,visual_verdict='unreviewed'))
    h.e.need(len({m['seed'] for m in manifest})==4,'Candidate seeds must be unique.')
    h.e.save(HOME/(prefix+'-manifest.json'),manifest)
    a.update(phase='pending_visual_review',manifest=prefix+'-manifest.json')
    s['phase']='pending_visual_review';save(s)
    print(json.dumps({'panel':target,'technical_verified':4,'visual_verified':0,'manifest':str(HOME/a['manifest'])},ensure_ascii=False))


def calibrate():
    """One documented reference-ablation experiment; not an open reroll loop."""
    s=load();pack=check_pack(s);h.idle()
    h.e.need(not s.get('pending_submission'),'Reconcile unknown submission first.')
    h.e.need(len(s['jobs'])==1 and s['jobs'][0]['panel_id']=='P001-K01',
             'Calibration already submitted or pilot advanced; do not repeat.')
    h.e.need(h.get('/api/job?id='+s['jobs'][0]['job'])['status']=='done','Earlier job not done.')
    r=h.e.read(HOME/'P001-K01-review.json')
    h.e.need(r['group_verdict']=='fail' and r['all_candidates_viewed'] and len(r['records'])==4,
             'Requires the recorded four-image diagnosis.')
    settings=copy.deepcopy(pack['settings'])
    h.act('save_drawing_settings',project=s['project'],settings=settings)
    doc=h.e.read(HOME/'prompts.json');doc['panels']=[doc['panels'][0]]
    row=doc['panels'][0]
    row['positive']=row['positive'].replace('empty hands relaxed at her sides',
        'both empty hands visibly hanging below her waist, one hand beside each hip, elbows straight and shoulders relaxed')
    row['positive']+=' Dark midnight sky visible above the roof. The entire background is the real dock environment. No costume display pose.'
    row['negative']+=', poster backdrop, circular emblem, white studio backdrop, promotional illustration, daylight, sunny shadows, hand on shoulder, hand on chest'
    prefix='P001-K01-round2';prompt_file=prefix+'-prompts.json'
    h.e.save(HOME/prompt_file,doc)
    pv=h.act('preview_prompt_import',project=s['project'],document=doc)
    h.e.need(pv['valid'],str(pv['errors']));h.e.save(HOME/(prefix+'-preview.json'),pv)
    bundle=h.act('save_prompt_import',project=s['project'],document=doc,preview_hash=pv['preview_hash'],confirm=True)
    a=dict(panel_id='P001-K01',round=2,artifact_prefix=prefix,prompt_file=prompt_file,
        bundle=bundle['id'],phase='submitting',expected_reference=None,method='clean_anchor_calibration',
        parent_run_key=s['jobs'][0]['run_key'],candidate_count=4)
    s['pending_submission']=a;save(s)
    result=h.act('draw_imported',project=s['project'],bundle=bundle['id'],targets=['P001-K01'],
                 candidate_count=4,steps=pack['steps'],checkpoint=MODEL)
    a.update(job=result['job'],phase='submitted')
    s['jobs'].append(a);s.pop('pending_submission');s['phase']='clean_anchor_calibration_running';save(s)
    j=h.get('/api/job?id='+result['job']);a['run_key']=j['request']['run_key'];save(s)
    h.e.save(HOME/(prefix+'-job.json'),j)
    h.e.need(not j['request']['references_by_panel'],'Calibration unexpectedly retained poster conditioning.')
    print(json.dumps(a,ensure_ascii=False))


def probe():
    """Bounded new-reference/new-angle diagnostic; failed originals stay failed."""
    s=load();pack=check_pack(s);h.idle()
    h.e.need(not s.get('pending_submission') and len(s['jobs'])==2,'Probe already submitted or submission unresolved.')
    h.e.need(all(h.get('/api/job?id='+a['job'])['status']=='done' for a in s['jobs']),'Earlier job still active.')
    review=h.e.read(HOME/'P001-K01-round2-review.json')
    h.e.need(review['all_candidates_viewed'] and len(review['records'])==4,'Inspect all calibration candidates first.')
    basis=h.e.read(HOME/'angle-probe-basis.json');target=basis['target']
    source=next(m for m in h.e.read(HOME/'P001-K01-round2-manifest.json') if m['image_id']==basis['source_image_id'])
    settings=copy.deepcopy(pack['settings']);h.act('save_drawing_settings',project=s['project'],settings=settings)
    doc=h.e.read(HOME/'prompts.json');doc['panels']=[r for r in doc['panels'] if r['panel_id']==target]
    prefix=target+'-angle-probe';prompt_file=prefix+'-prompts.json';h.e.save(HOME/prompt_file,doc)
    pv=h.act('preview_prompt_import',project=s['project'],document=doc)
    h.e.need(pv['valid'],str(pv['errors']));h.e.save(HOME/(prefix+'-preview.json'),pv)
    bundle=h.act('save_prompt_import',project=s['project'],document=doc,preview_hash=pv['preview_hash'],confirm=True)
    a=dict(panel_id=target,round=1,artifact_prefix=prefix,prompt_file=prompt_file,bundle=bundle['id'],phase='submitting',
        expected_reference={'image_id':source['image_id'],'sha256':source['image_sha256']},method='bounded_angle_probe',
        parent_run_key=s['jobs'][-1]['run_key'],candidate_count=4,production_approved=False)
    s['pending_submission']=a;save(s)
    result=h.act('draw_imported',project=s['project'],bundle=bundle['id'],targets=[target],candidate_count=4,
        steps=pack['steps'],checkpoint=MODEL,references_by_panel={target:source['image_id']})
    a.update(job=result['job'],phase='submitted');s['jobs'].append(a);s.pop('pending_submission')
    s['phase']='angle_probe_running';save(s)
    j=h.get('/api/job?id='+result['job']);a['run_key']=j['request']['run_key'];save(s)
    h.e.save(HOME/(prefix+'-job.json'),j)
    h.e.need(j['request']['references_by_panel'][target]['reference_sha256']==source['image_sha256'],'Probe anchor mismatch.')
    print(json.dumps(a,ensure_ascii=False))


if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['create','submit','calibrate','probe','audit','status'])
    args=parser.parse_args()
    if args.action=='create': create()
    elif args.action=='submit': submit()
    elif args.action=='audit': audit()
    elif args.action=='calibrate': calibrate()
    elif args.action=='probe': probe()
    else: print(json.dumps(load(),ensure_ascii=False,indent=2))

