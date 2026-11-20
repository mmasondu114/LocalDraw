"""Reference-grounded Doctor and penguin sequence requested by the user."""
import argparse
import hashlib
import json
import shutil
import verify_delta_edit_sequence as trial

ROOT = trial.ROOT
trial.OUT = trial.SERVER_ROOT / 'doctor-penguin'
SOURCE = ROOT / 'data/characters/images/706d6ea88ec7418fa2c49db5d31e9a01.png'
trial.IDENTITY = (
    r'doctor \(arknights\), arknights. Exactly one adult human: the Doctor from Arknights, '
    'a tall slender figure wearing the same black hooded tactical long coat as the reference, '
    'hood up, entire face concealed by a dark angular faceplate and deep hood shadow, '
    'a bright turquoise band on the upper arm, a small orange sleeve emblem, '
    'an orange diagonal accent on the coat pocket, gray lower coat panels, black gloves, '
    'dark trousers and black boots. Exactly one small black-and-white penguin with a round white belly, '
    'short orange beak, orange feet, and a plain red scarf. The penguin is about one quarter of the Doctor\'s height.'
)
trial.SCENE = (
    'A quiet Rhodes Island lounge, pale gray wall, one wooden bench with teal cushions, '
    'large window on the left, soft afternoon daylight. Anime illustration, clean detailed line art, '
    'subtle cel shading, masterpiece, best quality.'
)
trial.NEGATIVE = (
    'worst quality, low quality, blurry, extra people, duplicate person, multiple penguins, '
    'extra limbs, malformed hands, extra fingers, visible face, visible eyes, exposed hair, '
    'white lab coat, animal ears on human, text, watermark, comic panels, split screen'
)
trial.PANELS = [
    {'id':'01','title':'博士站立抱着企鹅','action':
     'Full-body three-quarter front view. The Doctor stands upright beside the bench, '
     'gently cradling the small penguin against the chest with both gloved arms. '
     'The penguin faces the viewer with its red scarf visible. Show both whole bodies. '
     'Preserve the Doctor\'s exact hood, faceplate and coat design from the reference image.'},
    {'id':'02','title':'转为侧面，抱着企鹅行走','action':
     'Full-body side view. The Doctor walks toward the right, in profile, taking a clear step '
     'while cradling the same penguin in both arms against the chest. The penguin faces right. '
     'The bench is behind them. Keep the same coat, faceplate, penguin markings and red scarf.'},
    {'id':'03','title':'坐在长椅，企鹅坐在腿上','action':
     'Medium full-body frontal view. The Doctor sits on the bench, knees bent, both boots '
     'on the floor. The same penguin sits upright on the Doctor\'s lap, supported gently by '
     'both gloved hands. Keep the same coat, faceplate, penguin markings and red scarf.'},
    {'id':'04','title':'蹲下，把企鹅放到地面','action':
     'Full-body three-quarter side view from a slightly high camera. The Doctor crouches low '
     'beside the bench with both knees bent, gently setting the same penguin on the floor. '
     'Both gloved hands support the penguin\'s sides, its orange feet touching the floor. '
     'Keep the same coat, faceplate, penguin markings and red scarf.'},
]


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('mode',choices=['baseline','anchor','chain','all'])
    args=parser.parse_args()
    trial.OUT.mkdir(parents=True,exist_ok=True)
    shutil.copy2(SOURCE,trial.OUT/'doctor-source.png')
    trial.save(trial.OUT/'experiment.json',{
        'model':trial.MODEL,'edit_lora':trial.LORA,'character_lora':None,
        'reference_source':str(SOURCE),'source_sha256':hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
        'character_card':'57b0ec91ae0e4049b368e5d2f5bb735e','source_kind':'existing user character-library image',
        'panels':trial.PANELS,'identity':trial.IDENTITY,'scene':trial.SCENE,'config':trial.CFG,
        'design':'Four matched-seed independent controls; first edited panel uses the existing Doctor reference, subsequent panels use the previous edited panel. Character LoRA unavailable for Anima; none used in either arm. Original Doctor reference remains the visual evaluation standard.',
    })
    if args.mode in {'baseline','all'}:
        for panel in trial.PANELS:
            trial.render(panel,'doctor-baseline-'+panel['id'])
    if args.mode in {'anchor','chain','all'}:
        assert (ROOT/'models/loras'/trial.LORA).exists(), 'Edit LoRA not yet ready'
        previous=trial.render(trial.PANELS[0],'doctor-chain-01',SOURCE)
        if args.mode != 'anchor':
            for panel in trial.PANELS[1:]:
                previous=trial.render(panel,'doctor-chain-'+panel['id'],previous)


if __name__=='__main__':main()
