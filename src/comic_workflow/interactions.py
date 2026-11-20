"""Keep a pair's relationship in the shared scene, separate from each identity.

This follows base-versus-regional prompt conditioning in the published
regional prompting workflows; it does not create or repair body pixels.
"""
import re
import copy
import math
from . import engine as e
from .paths import ROOT


def kind(text, count):
    if count != 2:
        return None
    text = re.sub(r'\b(?:not|no|without|avoid)\s+(?:mutual(?:ly)?\s+)?(?:hugs?|hugging|embracing|pointing(?: at each other)?)\b|'
                  r'(?:不要|没有|不)(?:互相|彼此)?(?:拥抱|相拥|互抱|互指|指着对方|指向对方|抱在一起)', '', text, flags=re.I)
    if re.search(r'\b(?:hugs?|hugging|embrace|embracing)\b|拥抱|相拥|互抱|抱在一起', text, re.I):
        return 'hugging'
    if (re.search(r'\bpointing\b|指着|指向|互指', text, re.I)
            and re.search(r'\beach other\b|\bone another\b|\bmutual(?:ly)?\b|互相|彼此|互指', text, re.I)):
        return 'mutual_pointing'
    return None


def regional_context(text):
    """Preserve scene/style clauses; relationship clauses belong to the base."""
    relation = re.compile(r'\b(?:hugs?|hugging|embrace|embracing|mutual|each other|one another|'
                          r'arms wrapped|bodies touching|face to face|pointing|index finger|'
                          r'other person|standing behind the other)\b|拥抱|相拥|互抱|互相|指着|指向|对方|彼此|面对面', re.I)
    return ', '.join(clause.strip() for clause in re.split(r'[,，;；]', text)
                     if clause.strip() and not relation.search(clause))


def local_action(action, role, roles):
    """A singular gesture supports the local hand without repeating the pair."""
    if action != 'mutual_pointing':
        return ''
    center = sum(role['region'][::2])
    other = next(r for r in roles if r is not role)
    direction = 'right' if sum(other['region'][::2]) > center else 'left'
    return ', pointing ' + direction + ', (one extended index finger:1.2), other fingers curled, other hand relaxed'


def validate_head_ownership(people):
    """Reject duplicate BODY_18 face anchors without inventing hidden faces.

    Removing face_keypoints_2d alone leaves the body's nose and eyes active.
    A detector can assign those same visible points to an occluded partner.
    Nearby noses in an embrace are valid when the two eye pairs are distinct.
    """
    heads = []
    for person in people:
        body = person['pose_keypoints_2d']
        points = [body[j*3:j*3+3] for j in (0, 14, 15)]
        if not all(len(p) == 3 and p[2] > 0 for p in points):
            continue
        span = math.dist(points[1][:2], points[2][:2])
        if span > 0:
            heads.append((points, span))
    for index, (first, first_span) in enumerate(heads):
        for second, second_span in heads[index+1:]:
            tolerance = min(first_span, second_span) * .25
            repeated = all(math.dist(a[:2], b[:2]) <= tolerance
                           for a, b in zip(first, second))
            e.need(not repeated, '参考姿势把同一组鼻眼位置重复分配给两个人，请核对人物归属；被遮挡或归属不明的脸部点应留空')


def published_pose(action, roles, cfg, *, upper_body=False):
    """Fit published poses without separating a hugging pair's contact points.

    Uniform scaling and the author's horizontal mirror operation are standard
    OpenPose Studio transforms. No missing body/hand joints are synthesized.
    """
    folder = ROOT / 'runtime/custom_nodes/ComfyUI-OpenPose-Studio/poses'
    if action == 'hugging':
        templates = e.read(folder / 'hugging.json')
        preset = cfg.get('regional_hug_preset', 'face to face hug')
        e.need(preset in templates, '作者姿势库中没有这个拥抱姿势')
        pose = copy.deepcopy(e.read(ROOT / cfg['regional_hug_pose']) if cfg.get('regional_hug_pose') else templates[preset])
        e.need(len(pose['people']) == 2, '拥抱姿势必须恰好对应两个人')
        validate_head_ownership(pose['people'])
        people = sorted(pose['people'], key=lambda p: p['pose_keypoints_2d'][3])
        points = [p for person in people for p in valid_points(person, upper_body=upper_body)]
        left, top = min(p[0] for p in points), min(p[1] for p in points)
        right, bottom = max(p[0] for p in points), max(p[1] for p in points)
        x1 = min(r['region'][0] for r in roles); y1 = min(r['region'][1] for r in roles)
        x2 = max(r['region'][2] for r in roles); y2 = max(r['region'][3] for r in roles)
        width, height = (x2-x1)*cfg['width'], (y2-y1)*cfg['height']
        margin = cfg.get('regional_pose_margin', .18)
        # A half-body camera ends at the hips. Fitting knees/ankles into the
        # same canvas counteracts that camera and makes the hands much smaller.
        visible_height = height * (1-margin if upper_body else 1-2*margin)
        scale = min(width*.82/(right-left), visible_height/(bottom-top))
        xoff = (x1+x2)*cfg['width']/2 - (left+right)*scale/2
        yoff = y1*cfg['height'] + height*margin - top*scale
        for person in people:
            transform(person, scale, xoff, yoff)
        ordered = sorted(range(len(roles)), key=lambda i: sum(roles[i]['region'][::2]))
        mapped = [None] * len(roles)
        for index, person in zip(ordered, people):
            mapped[index] = person
        return {'canvas_width': cfg['width'], 'canvas_height': cfg['height'], 'people': mapped}
    if action == 'mutual_pointing':
        original = e.read(folder / 'misc/pointing.json')['people'][0]
        ordered = sorted(range(len(roles)), key=lambda i: sum(roles[i]['region'][::2]))
        people = [None] * len(roles)
        body = original['pose_keypoints_2d']
        body_x = [body[i] for i in range(0, len(body), 3) if body[i+2] > 0]
        mirror_axis = (min(body_x)+max(body_x))/2
        for side, index in enumerate(ordered):
            person = copy.deepcopy(original)
            if side:
                # OpenPose Studio canvas2d.js applyMirrorToPose: x = 2*cx-x.
                for key, values in person.items():
                    if key.endswith('keypoints_2d'):
                        for i in range(0, len(values), 3):
                            if values[i+2] > 0:
                                values[i] = 2*mirror_axis-values[i]
            points = valid_points(person)
            left, top = min(p[0] for p in points), min(p[1] for p in points)
            right, bottom = max(p[0] for p in points), max(p[1] for p in points)
            x1, y1, x2, y2 = roles[index]['region']
            width, height = (x2-x1)*cfg['width'], (y2-y1)*cfg['height']
            margin = cfg.get('regional_pose_margin', .18)
            scale = min(width*.82/(right-left), height*(1-2*margin)/(bottom-top))
            transform(person, scale, (x1+x2)*cfg['width']/2-(left+right)*scale/2,
                      y1*cfg['height']+height*margin-top*scale)
            people[index] = person
        for index, person in enumerate(people):
            hand = person['hand_left_keypoints_2d']
            target = people[1-index]['pose_keypoints_2d'][3:5]
            anchor = hand[:2]
            direction = (hand[8*3]-hand[5*3], hand[8*3+1]-hand[5*3+1])
            angle = math.atan2(target[1]-anchor[1], target[0]-anchor[0]) - math.atan2(direction[1], direction[0])
            cosine, sine = math.cos(angle), math.sin(angle)
            # Standard whole-hand rotation around the locked wrist anchor.
            # Retain the author's finger geometry, confidence and joint count.
            for i in range(0, len(hand), 3):
                if hand[i+2] > 0:
                    dx, dy = hand[i]-anchor[0], hand[i+1]-anchor[1]
                    hand[i] = round(anchor[0]+cosine*dx-sine*dy, 4)
                    hand[i+1] = round(anchor[1]+sine*dx+cosine*dy, 4)
        return {'canvas_width': cfg['width'], 'canvas_height': cfg['height'], 'people': people}
    raise ValueError('尚未验证的互动姿势：' + str(action))


def valid_points(person, *, upper_body=False):
    return [values[i:i+3] for key, values in person.items() if key.endswith('keypoints_2d')
            for i in range(0, len(values), 3) if values[i+2] > 0
            and not (upper_body and key=='pose_keypoints_2d' and i//3 in (9,10,12,13))]


def transform(person, scale, xoff, yoff):
    for key, values in person.items():
        if not key.endswith('keypoints_2d'):
            continue
        for i in range(0, len(values), 3):
            if values[i+2] > 0:
                values[i] = round(values[i]*scale+xoff, 4)
                values[i+1] = round(values[i+1]*scale+yoff, 4)
