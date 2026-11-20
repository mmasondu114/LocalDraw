import json, time, subprocess, urllib.request, urllib.parse, copy
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'output/seven-invitations-pov-20261105'
BASE='http://127.0.0.1:8190'
STYLE='masterpiece, best quality, detailed anime illustration, cinematic landscape composition, expressive detailed face, recognizable character design, clean fine linework, refined cel shading, elegant color harmony, rich layered environment, clear foreground middle ground and background, carefully composed hands. First person POV from the eyes of the Traveler Aether in Genshin Impact. Immersive subjective camera. The viewer IS the traveler, no visible protagonist face or body, no over-the-shoulder shot. One coherent scene. '
NEG='worst quality, low quality, blurry, bad anatomy, extra arms, extra fingers, fused fingers, malformed hands, duplicate character, cropped head, cropped face, text, watermark, signature, logo, panel borders, split screen, collage, photorealistic, 3d render, nude, swimsuit, back of protagonist head, third person view, visible traveler face'
PANELS=[
dict(name='序 · 异常委托',place='尘歌壶 / 清晨',caption='今天的委托，只有一句话。',speaker='我',line='「请旅行者，认真休息一天。」……谁写的？',shot='俯视近景；我的双手把一张无字邀请函举到眼前，桌上的七枚彩色印记引出七国。',prompt='No people visible. Strict first person looking down at my own TWO gloved hands holding ONE blank ivory invitation card by its lower corners, black fingerless gloves with gold wrist cuffs. On a wooden breakfast table are seven small jewel seals in teal, gold, purple, green, blue, red, icy white. A travel map, steaming tea and warm sunlight. Beyond the table a round open window overlooks floating mountains of Serenitea Pot. Quiet morning, tactile paper, cinematic close-up of invitation.'),
dict(name='01 · 风的借口',place='蒙德 / 风起地',caption='第一站，风先没收了我的日程。',speaker='温迪',line='风都替你请好假了。走吧，今天只听一首歌。',shot='平视中近景；温迪在树荫下看向我，邀请函的边角处于前景下方。',prompt='1boy, Venti (genshin impact), solo bard with short black hair and two teal-tipped braids, green eyes, green beret with white Cecilia flower, green cape, white blouse with bow, canonical costume. Venti sits close facing the viewer under the huge Windrise oak, smiling mischievously with direct eye contact, holding a wooden lyre naturally at his chest. At the very bottom foreground my black fingerless gloved hand holds the corner of an ivory invitation. Green leaves drift between us, sunlit grass, distant Mondstadt, warm breeze. Medium shot from seated traveler eye level, complete head.'),
dict(name='02 · 岩的契约',place='璃月 / 茶馆',caption='第二站，我差点把休假签成长期契约。',speaker='钟离',line='契约已备好：今日，不接委托。茶钱？我已付过。',shot='隔桌平视；钟离端坐对面，一只茶杯在我的前景手中。',prompt='1man, Zhongli (genshin impact), solo, handsome adult man, amber eyes, dark brown hair with amber tips and long low ponytail, ornate brown and black formal long coat with gold geometric trim, canonical costume. Zhongli sits directly opposite the viewer at a wooden tea table, calm warm smile, looking directly into camera. In the bottom foreground the traveler own black fingerless gloved hand with gold cuff holds a small porcelain teacup near the camera. Zhongli rests his own hands on the table. Liyue wooden pavilion, red lanterns, harbor roofs in golden afternoon light. Intimate seated eye-level view, upper body.'),
dict(name='03 · 雷的例外',place='稻妻 / 樱树下',caption='第三站，永恒临时多了一条规定。',speaker='影',line='团子趁热。至于切磋……今日的你，可以例外。',shot='平视近景；影正看向我，团子在她与我的视线之间。',prompt='1woman, Raiden Ei, raiden shogun (genshin impact), solo, violet eyes, very long dark purple braided hair, purple flower hair ornament, ornate purple kimono with red obi, canonical costume. She sits on a bench opposite the viewer under blooming sakura and offers one skewer of three pastel dango toward camera in her right hand, gentle shy smile, direct eye contact. Strong foreshortening of offered dango. At bottom left foreground a small portion of the traveler own black gloved hand with gold wrist cuff reaches toward skewer. Inazuma shrine courtyard, drifting petals, violet evening shadows, soft lighting, upper body medium closeup.'),
dict(name='04 · 草的答案',place='须弥 / 梦境温室',caption='第四站，有人看穿了我没说出口的疲惫。',speaker='纳西妲',line='休息不是浪费时间。种子也要睡一觉，才会发芽呀。',shot='蹲下后的平视；纳西妲与我隔着一片发光嫩叶相望。',prompt='1girl, Nahida (genshin impact), solo, small childlike forest deity, white hair with pale green tips in side ponytail, green leaf ornament, large green eyes, white and green leaf patterned dress, canonical modest costume. Wholesome gentle scene. The viewer is crouching at her eye level. Nahida faces camera with a kind knowing smile, hands folded calmly at her chest. At bottom foreground my black fingerless gloved hand with gold cuff holds a tiny green seedling, luminous leaf floating above it. Dreamlike Sumeru greenhouse with giant leaves, sunbeams, tiny green light motes, medium portrait, complete head.'),
dict(name='05 · 水的谢幕',place='枫丹 / 午后甜品店',caption='第五站，主角终于获准坐进观众席。',speaker='芙宁娜',line='今天不用拯救世界。你只要负责——夸这块蛋糕！',shot='餐桌对坐；芙宁娜得意地展示蛋糕，我的叉子在画面下缘。',prompt='1woman, Furina (genshin impact), solo, white hair with blue streaks, short bob with long curled side locks, heterochromatic blue eyes, small tilted blue top hat, ornate navy blue tailcoat and white ruffled shirt, blue jewel ribbon, canonical costume. Furina sits across a small cafe table facing the viewer, playful proud smile, direct eye contact, one hand open presenting a strawberry cream cake on a white plate at the center. At bottom foreground the viewer own black gloved hand holds a dessert fork. Elegant Fontaine cafe, large window, blue and gold decor, warm afternoon sun. Eye-level first person seated close view, wholesome comedy.'),
dict(name='06 · 火的方向',place='纳塔 / 落日高地',caption='第六站，目的地终于不是下一场战斗。',speaker='玛薇卡',line='这次不去战场。上车，我带你追一次落日。',shot='平视中景；玛薇卡从车旁转向我，把备用头盔递近镜头。',prompt='1woman, Mavuika (genshin impact), solo, tall athletic adult woman, long flowing flame-red hair with golden orange ends, orange eyes, black fitted biker suit with gold and red flame ornaments, black gloves, sunglasses perched on head, canonical costume. She stands next to her black and gold fantasy motorcycle, faces the viewer, friendly confident smile and direct eye contact, extending ONE spare black motorcycle helmet toward the camera. At very bottom foreground a portion of my own black fingerless gloved hand with gold wrist cuff reaches to receive it. Natlan rocky plateau, broad orange sunset, wind moving her hair, dynamic medium shot, complete head, warm adventurous mood.'),
dict(name='07 · 冰的温度',place='至冬 / 雪夜会客厅',caption='最后一站，我终于把行囊放下。',speaker='冰之女皇',line='风雪留在门外。今晚，你只是一位远道而来的客人。',shot='隔桌平视近景；女皇在烛光中迎接我，我双手捧住热茶。',prompt='1woman, the Tsaritsa, Cryo Archon of Snezhnaya, original fan interpretation, regal adult ice queen with long silver white hair, pale blue eyes, small ice crystal crown, elegant high collared white and ice blue embroidered gown, white fur mantle, fully clothed. She sits close opposite the viewer at a small table beside a warm fireplace, serene gentle expression with direct eye contact, her own hands folded on table. At bottom foreground BOTH of my own black fingerless gloved hands with gold wrist cuffs cradle a single steaming ceramic tea mug. Frosted tall window, snowflakes beyond glass, warm candlelight on her face contrasted with cool blue snow, intimate first person eye-level medium portrait, no throne, welcoming quiet ending.')
]
def save(p,v):
 p.write_text(json.dumps(v,ensure_ascii=False,indent=2),encoding='utf-8')
def api(path,body=None):
 req=urllib.request.Request(BASE+path,data=json.dumps(body).encode() if body is not None else None,headers={'Content-Type':'application/json'})
 with urllib.request.urlopen(req,timeout=30) as r:return json.load(r)
def main():
 PANELS[7]['prompt']='1girl, solo, exactly ONE adult woman, a mature regal ice queen, original character design. SINGLE SUBJECT centered in frame. Long straight silver-white hair, pale blue eyes, elegant small crystal tiara, high-collared white fur cape over a modest dark navy gown with silver frost embroidery. She sits ALONE opposite the viewer at a small wooden table beside a fireplace. Her face is centered, she looks directly at the viewer with a gentle reserved smile, her hands folded on the table. At bottom foreground TWO first-person hands in black fingerless gloves with gold wrist cuffs cradle ONE steaming tea mug. Cozy intimate hospitality, frosted window, blue snow outdoors, warm amber candlelight on her face, medium close-up, complete head. Only one face in entire image. Empty room behind her.'
 OUT.mkdir(parents=True,exist_ok=True)
 save(OUT/'storyboard.json',dict(title='七封邀请，一次休假',premise='旅行者空的第一人称梦境短篇。水之席由芙宁娜出演，冰之女皇为同人造型演绎。',panels=PANELS))
 base=json.loads((ROOT/'workflows/delta-mix-v6-api.json').read_text())
 proc=None
 log=(OUT/'generation.log').open('a',encoding='utf-8')
 try:
  try: api('/system_stats')
  except OSError:
   proc=subprocess.Popen([str(ROOT/'runtime/kreamoss-venv/Scripts/python.exe'),str(ROOT/'runtime/ComfyUI-KreaMoss/main.py'),'--listen','127.0.0.1','--port','8190','--reserve-vram','3','--disable-all-custom-nodes','--whitelist-custom-nodes','ComfyUI-Anima-Delta-Q35-LoRA','local_draw_delta_characters','--disable-api-nodes','--disable-auto-launch','--extra-model-paths-config',str(ROOT/'config/delta-mix-model-paths.yaml'),'--output-directory',str(OUT/'raw')],cwd=ROOT/'runtime/ComfyUI-KreaMoss',stdout=log,stderr=subprocess.STDOUT,creationflags=subprocess.CREATE_NO_WINDOW)
   for _ in range(120):
    if proc.poll() is not None:raise RuntimeError('Comfy startup failed')
    try:api('/system_stats');break
    except OSError:time.sleep(1)
   else:raise TimeoutError('startup')
  q=api('/queue')
  if q['queue_running'] or q['queue_pending']:raise RuntimeError('Existing queue occupied')
  for i,p in enumerate(PANELS,1):
   target=OUT/f'panel-{i:02}.png'
   if target.exists():continue
   g=copy.deepcopy(base)
   g['6']['inputs']['text']=STYLE+p['prompt'];g['13']['inputs']['text']=NEG
   g['5']['inputs'].update(width=1536,height=864)
   g['3']['inputs']['seed']=2026110500+i
   if i==8:
    g['3']['inputs']['seed']=2026110588
    g['13']['inputs']['text']+=', 2girls, multiple girls, two women, twins, duplicate woman, second face, second person, split composition'
   g['29']['inputs']['filename_prefix']=f'pov-{i:02}'
   save(OUT/f'panel-{i:02}-workflow.json',g)
   subpath=OUT/f'panel-{i:02}-submitted.json'
   if subpath.exists(): sub=json.loads(subpath.read_text())
   else: sub=api('/prompt',{'prompt':g});save(subpath,sub)
   print(f'SUBMITTED {i}: {sub["prompt_id"]}',flush=True)
   start=time.monotonic()
   while time.monotonic()-start<1800:
    h=api('/history/'+sub['prompt_id']).get(sub['prompt_id'])
    if h:break
    time.sleep(2)
   else:raise TimeoutError('image generation')
   save(OUT/f'panel-{i:02}-history.json',h)
   if h['status']['status_str']!='success':raise RuntimeError(str(h['status']))
   entry=h['outputs']['29']['images'][0]
   with urllib.request.urlopen(BASE+'/view?'+urllib.parse.urlencode(entry),timeout=30) as r:target.write_bytes(r.read())
   print(f'COMPLETED {i}: {time.monotonic()-start:.1f}s',flush=True)
 finally:
  if proc:proc.terminate();proc.wait(timeout=30)
  log.close()
if __name__=='__main__':main()
