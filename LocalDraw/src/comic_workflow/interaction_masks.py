"""Prepare pose-guided ownership masks for the native masked-LoRA workflow.

These are conditioning masks, never cropped/generated character pixels.
https://blog.comfy.org/p/masking-and-scheduling-lora-and-model-weights
"""
from PIL import Image, ImageDraw, ImageFilter, ImageChops
import math

from . import engine as e
from .paths import DATA


def pair_masks(roles, pose, width, height, *, arm_padding=0):
    e.need(len(roles) == len(pose['people']) == 2, '互动蒙版需要两个对应角色')
    e.need(type(arm_padding) is int and 0 <= arm_padding <= max(width, height)//8,
           '手臂控制区域的扩展距离无效')
    ordered = sorted(range(2), key=lambda i: sum(roles[i]['region'][::2]))
    union = Image.new('L', (width, height), 0)
    left = Image.new('L', (width, height), 0)
    for index, role in enumerate(roles):
        x1, y1, x2, y2 = role['region']
        box = (round(x1*width), round(y1*height), round(x2*width)-1, round(y2*height)-1)
        ImageDraw.Draw(union).rectangle(box, fill=255)
        if index == ordered[0]:
            ImageDraw.Draw(left).rectangle(box, fill=255)
    # Transfer the whole visible arm and palm to its owner, including portions
    # crossing the old rectangular split. Missing/occluded joints stay absent.
    arms, hands = [None, None], [None, None]
    for index in ordered:
        person = pose['people'][index]
        body = person['pose_keypoints_2d']
        arm = Image.new('L', (width, height), 0)
        draw = ImageDraw.Draw(arm)
        hand_mask = Image.new('L', (width, height), 0)
        hand_draw = ImageDraw.Draw(hand_mask)
        neck = body[3:6]
        hips = [body[j*3:j*3+3] for j in (8, 11) if body[j*3+2] > 0]
        torso = max((abs(hip[1]-neck[1]) for hip in hips), default=height*.25)
        radius = max(6, round(torso*.065))
        arm_radius = radius + arm_padding
        for joints, hand_key in (((2, 3, 4), 'hand_right_keypoints_2d'),
                                 ((5, 6, 7), 'hand_left_keypoints_2d')):
            points = [body[j*3:j*3+3] for j in joints]
            # Occlusion can hide an elbow while its hand remains visible.
            # Preserve known adjacent segments only; never bridge a missing
            # joint, and never discard an independently detected whole hand.
            for first, last in zip(points, points[1:]):
                if first[2] > 0 and last[2] > 0:
                    xy = [(first[0], first[1]), (last[0], last[1])]
                    draw.line(xy, fill=255, width=arm_radius*2, joint='curve')
                    for x, y in xy:
                        draw.ellipse((x-arm_radius, y-arm_radius, x+arm_radius, y+arm_radius), fill=255)
            hand = person.get(hand_key, [])
            has_fingers = len(hand) == 63 and any(hand[j*3+2] > 0 for j in range(1, 21))
            elbow, wrist = points[1:]
            if not has_fingers and elbow[2] > 0 and wrist[2] > 0:
                # Body-only author templates still have a hand beyond the
                # wrist. Extend its conditioning footprint along the known
                # forearm; this never adds pose or finger keypoints.
                dx, dy = wrist[0]-elbow[0], wrist[1]-elbow[1]
                if dx or dy:
                    tip = (wrist[0]+dx*.45, wrist[1]+dy*.45)
                    padding = round(radius*1.4)
                    xy = [(wrist[0], wrist[1]), tip]
                    hand_draw.line(xy, fill=255, width=padding*2, joint='curve')
                    for x, y in xy:
                        hand_draw.ellipse((x-padding, y-padding, x+padding, y+padding), fill=255)
            # Finger segments plus padding fill the palm without generating
            # any missing hand joints or interpreting the source photograph.
            for first in (1, 5, 9, 13, 17):
                ids = [0] + list(range(first, first+4))
                if len(hand) == 63 and all(hand[j*3+2] > 0 for j in ids):
                    xy = [(hand[j*3], hand[j*3+1]) for j in ids]
                    hand_draw.line(xy, fill=255, width=radius*2, joint='curve')
                    # Round caps keep a known fingertip inside its owner's
                    # feathered footprint, rather than on a half-weight edge.
                    for x, y in (xy[0], xy[-1]):
                        hand_draw.ellipse((x-radius, y-radius, x+radius, y+radius), fill=255)
        arms[index], hands[index] = arm, hand_mask
    # A visible hand can rest on the other person's forearm. Apply all arm
    # footprints before hands so a later arm cannot erase the known palm or
    # thumb belonging to the first person. Pose coordinates remain unchanged.
    for layer in (arms, hands):
        for index in ordered:
            footprint = ImageChops.multiply(layer[index], union)
            left = (ImageChops.lighter(left, footprint) if index == ordered[0]
                    else ImageChops.subtract(left, footprint))
    # Complementary feathered masks keep the total character contribution at
    # one across the contact seam; the existing scene share remains available.
    left = ImageChops.multiply(left.filter(ImageFilter.GaussianBlur(4)), union)
    right = ImageChops.subtract(union, left)
    result = [None, None]
    result[ordered[0]], result[ordered[1]] = left, right
    return result


def graph_masks(graph, roles, pose, cfg):
    shared_contact = cfg.get('regional_hug_shared_contact', False)
    padding = cfg.get('regional_arm_mask_padding', 0)
    masks = (shared_contact_masks(roles, pose, cfg['width'], cfg['height']) if shared_contact else
             pair_masks(roles, pose, cfg['width'], cfg['height'], arm_padding=padding))
    identity = {'roles': roles, 'pose': pose, 'width': cfg['width'], 'height': cfg['height'], 'recipe': 5,
                'shared_contact': shared_contact}
    if padding and not shared_contact:
        identity['arm_padding'] = padding
    key = e.digest(identity)
    folder = DATA / 'comfy-input' / 'interaction-masks'
    folder.mkdir(parents=True, exist_ok=True)
    result = {}
    for index, (role, mask) in enumerate(zip(roles, masks)):
        filename = f'{key}-{index}.png'
        path = folder / filename
        if not path.exists():
            mask.convert('RGB').save(path)
        image_id, mask_id = f'interaction-mask-image-{index}', f'interaction-mask-{index}'
        graph[image_id] = {'class_type': 'LoadImage', 'inputs': {'image': 'interaction-masks/'+filename}}
        graph[mask_id] = {'class_type': 'ImageToMask', 'inputs': {'image': [image_id, 0], 'channel': 'red'}}
        result[role['name']] = [mask_id, 0]
    return result


def split_complete_hand_masks(roles, pose, width, height, *, arm_padding=0):
    """Partition existing owner weights into body and confirmed hand regions.

    These are conditioning weights on the same latent, never generated pixels.
    Partial hands receive no new points or specialized hand conditioning.
    """
    owned=pair_masks(roles,pose,width,height,arm_padding=arm_padding)
    bodies=[mask.copy() for mask in owned]
    hands=[]
    for index,person in enumerate(pose['people']):
        for side in ('right','left'):
            points=person.get('hand_'+side+'_keypoints_2d',[])
            if len(points)!=63 or not all(points[j*3+2]>0 for j in range(21)):
                continue
            xy=[tuple(points[j*3:j*3+2]) for j in range(21)]
            e.need(all(all(math.isfinite(v) for v in point) and 0<=point[0]<width and
                       0<=point[1]<height for point in xy),'手形参考需要完整有效的画幅内关节')
            e.need(math.dist(xy[0],xy[9])>=2,'手形参考的手腕与指根需要分开')
            radius=max(3,round(math.dist(xy[0],xy[9])*.12))
            footprint=Image.new('L',(width,height),0)
            draw=ImageDraw.Draw(footprint)
            for first in (1,5,9,13,17):
                line=[xy[j] for j in [0]+list(range(first,first+4))]
                draw.line(line,fill=255,width=radius*2,joint='curve')
                for x,y in line:
                    draw.ellipse((x-radius,y-radius,x+radius,y+radius),fill=255)
            # Intersect with the remaining owner weight. Even intersecting
            # hands cannot double the contribution or steal another owner.
            mask=ImageChops.multiply(bodies[index],footprint.filter(ImageFilter.GaussianBlur(1)))
            bodies[index]=ImageChops.subtract(bodies[index],mask)
            hands.append({'owner_index':index,'side':side,'mask':mask})
    return owned,bodies,hands


def graph_hand_masks(graph, roles, pose, cfg):
    owned,bodies,hands=split_complete_hand_masks(roles,pose,cfg['width'],cfg['height'],
                                                arm_padding=cfg.get('regional_arm_mask_padding',0))
    e.need(hands,'当前参考没有可单独核对的完整手形')
    key=e.digest({'roles':roles,'pose':pose,'width':cfg['width'],'height':cfg['height'],
                  'arm_padding':cfg.get('regional_arm_mask_padding',0),'recipe':1})
    folder=DATA/'comfy-input'/'contact-hand-masks';folder.mkdir(parents=True,exist_ok=True)
    def load(label,mask):
        filename=key+'-'+label+'.png';path=folder/filename
        if not path.exists():mask.convert('RGB').save(path)
        image_id,mask_id='contact-hand-image-'+label,'contact-hand-mask-'+label
        graph[image_id]={'class_type':'LoadImage','inputs':{'image':'contact-hand-masks/'+filename}}
        graph[mask_id]={'class_type':'ImageToMask','inputs':{'image':[image_id,0],'channel':'red'}}
        return [mask_id,0]
    body_nodes={role['name']:load('body-'+str(index),bodies[index]) for index,role in enumerate(roles)}
    hand_nodes=[{'owner_index':hand['owner_index'],'side':hand['side'],
                 'mask':load(str(hand['owner_index'])+'-'+hand['side'],hand['mask'])} for hand in hands]
    return body_nodes,hand_nodes


def shared_contact_masks(roles, pose, width, height):
    """Leave the contact band uncovered for native default conditioning.

    The author's default-combine node supplies the pair context there. These
    are soft conditioning weights and never edited or composited body pixels.
    """
    e.need(len(roles) == len(pose['people']) == 2, '共同接触区域需要两个对应角色')
    bodies = [p['pose_keypoints_2d'] for p in pose['people']]
    necks = [b[3:6] for b in bodies if b[5] > 0]
    hips = [b[j*3:j*3+3] for b in bodies for j in (8, 11) if b[j*3+2] > 0]
    shoulders = [b[j*3:j*3+3] for b in bodies for j in (2, 5) if b[j*3+2] > 0]
    e.need(necks and hips and shoulders, '接触区域需要已存在的肩部、颈部和躯干位置')
    top = min(p[1] for p in necks)
    bottom = max(p[1] for p in hips)
    torso = bottom-top
    band = Image.new('L', (width, height), 0)
    ImageDraw.Draw(band).rectangle((min(p[0] for p in shoulders)-torso*.5, top+torso*.08,
                                   max(p[0] for p in shoulders)+torso*.5, bottom+torso*.12), fill=255)
    retained = ImageChops.invert(band.filter(ImageFilter.GaussianBlur(8)))
    masks = []
    for role in roles:
        x1, y1, x2, y2 = role['region']
        mask = Image.new('L', (width, height), 0)
        ImageDraw.Draw(mask).rectangle((round(x1*width), round(y1*height), round(x2*width)-1, round(y2*height)-1), fill=255)
        masks.append(ImageChops.multiply(mask, retained))
    return masks
