from PIL import Image, ImageDraw, ImageFont
from pathlib import Path
import json, html, zipfile
P=Path(__file__).resolve().parents[1]/'output/seven-invitations-pov-20261105'
story=json.loads((P/'storyboard.json').read_text(encoding='utf-8'))
F='C:/Windows/Fonts/msyh.ttc'; B='C:/Windows/Fonts/msyhbd.ttc'
def font(n,b=False):return ImageFont.truetype(B if b else F,n)
ink='#202c39'; muted='#637080'; paper='#f5f1e8'
colors=['#80634b','#27866d','#9b722d','#7655a0','#4f7e36','#3770a1','#b75634','#4a7b94']
def wrap(d,text,f,width):
 lines=[];line=''
 for c in text:
  if d.textlength(line+c,font=f)>width:lines.append(line);line=c
  else:line+=c
 if line:lines.append(line)
 return lines
pages=[]
for pg in range(4):
 im=Image.new('RGB',(1680,2440),paper); d=ImageDraw.Draw(im)
 d.text((72,35),'七封邀请，一次休假',font=font(62,True),fill=ink)
 d.text((74,117),'旅行者第一人称 · 七国梦中邀约',font=font(25),fill=muted)
 d.text((1400,55),f'0{pg+1} / 04',font=font(30,True),fill=muted)
 d.line((72,162,1608,162),fill=ink,width=3)
 for row in range(2):
  i=pg*2+row;p=story['panels'][i]; y=190+row*1090; accent=colors[i]
  d.rounded_rectangle((72,y,1608,y+46),radius=8,fill=accent)
  d.text((92,y+5),p['name'],font=font(25,True),fill='white')
  w=d.textlength(p['place'],font=font(23));d.text((1586-w,y+6),p['place'],font=font(23),fill='white')
  art=Image.open(P/f'panel-{i+1:02}.png').convert('RGB')
  assert art.size==(1536,864)
  im.paste(art,(72,y+56));d.rectangle((72,y+56,1607,y+919),outline=ink,width=3)
  # An attached dialogue balloon sits below the art, preserving all POV hands and faces.
  top=y+938
  d.rounded_rectangle((72,top,1608,top+127),radius=25,fill='white',outline=ink,width=2)
  d.polygon([(210,top+2),(235,top-20),(260,top+2)],fill='white')
  d.line([(210,top),(235,top-20),(260,top)],fill=ink,width=2)
  d.text((98,top+15),p['speaker'],font=font(27,True),fill=accent)
  lines=wrap(d,p['line'],font(34),1310)
  for j,line in enumerate(lines):d.text((250,top+17+j*44),line,font=font(34),fill=ink)
  d.text((98,top+82),p['caption'],font=font(24),fill=muted)
 footer='你看向世界时，世界也在温柔地看着你。' if pg==3 else '跟着邀请函，去往下一站。'
 d.text((72,2380),footer,font=font(24),fill=muted)
 d.text((1260,2382),'DELTAMIX V6 · POV',font=font(21,True),fill=muted)
 name=f'comic-page-{pg+1:02}.png';im.save(P/name);pages.append(name)
 im.resize((840,1220)).save(P/f'preview-page-{pg+1:02}.jpg',quality=93)
sheet=Image.new('RGB',(1680,2440),paper)
for i in range(4):sheet.paste(Image.open(P/f'preview-page-{i+1:02}.jpg'),((i%2)*840,(i//2)*1220))
sheet.resize((1008,1464)).save(P/'comic-overview.jpg',quality=94)
body=''.join(f'<figure id="p{i+1}"><img src="{n}" alt="第{i+1}页"><figcaption>第 {i+1} / 4 页 · <a href="{n}" download>保存原图</a></figcaption></figure>' for i,n in enumerate(pages))
doc='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>七封邀请，一次休假</title><style>body{margin:0;background:#171d25;color:#f5f1e8;font-family:system-ui,"Microsoft YaHei",sans-serif}header,footer{max-width:960px;margin:35px auto;padding:0 20px}h1{font-size:32px}p{line-height:1.8;color:#bec5ce}nav{display:flex;gap:20px;flex-wrap:wrap}a{color:#eac691}figure{max-width:1000px;margin:30px auto}img{width:100%;display:block}figcaption{text-align:center;margin:12px}footer{padding-bottom:40px}</style><header><h1>七封邀请，一次休假</h1><p>你是旅行者。今天，七国不需要你拯救世界，只邀请你好好休息。<br>4 页 / 8 格 · 从上到下阅读 · 全程第一人称视角</p><nav><a href="#p1">第 1 页</a><a href="#p2">第 2 页</a><a href="#p3">第 3 页</a><a href="#p4">第 4 页</a><a href="seven-invitations-comic.zip" download>下载完整包</a></nav></header>'''+body+'''<footer><p>梦境同人：水之席由芙宁娜出演；冰之女皇采用原创同人造型。本故事与对白均为原创，不作为原作剧情或官方造型资料。</p><p>画面：本地 DeltaMix v6。剧本、分镜、提示词、中文排版与逐格检查已随完整包保存。</p></footer></html>'''
(P/'read-comic.html').write_text(doc,encoding='utf-8')
lines=['《七封邀请，一次休假》','原创同人短篇 / 旅行者空第一人称 / 从上到下阅读','水之席芙宁娜，冰之女皇为同人造型，不是官方设定。','', '生成参数：DeltaMix v6；1536×864；35 steps；CFG 4.5；dpmpp_2m / sgm_uniform；无 LoRA。','']
for i,p in enumerate(story['panels'],1):
 lines.extend([f'{i:02} {p["name"]} — {p["place"]}',p['shot'],'旁白：'+p['caption'],p['speaker']+'：'+p['line'],'正向提示词与种子见对应 panel-XX-workflow.json。',''])
lines+=['视觉检查：首轮第8格出现双人，已重画。第7格最终为伸手邀约，未出现原分镜计划的备用头盔；第4格最终为双方各持一串团子。服装与手套细节有生成差异。','资料核对：https://genshin-impact.fandom.com/wiki/Focalors','资料核对：https://genshin-impact.fandom.com/wiki/The_Seven']
(P/'production-notes.txt').write_text('\n'.join(lines),encoding='utf-8')
with zipfile.ZipFile(P/'seven-invitations-comic.zip','w',zipfile.ZIP_DEFLATED) as z:
 for f in P.iterdir():
  if f.suffix in ['.png','.html','.txt','.json'] and not f.name.endswith(('-history.json','-submitted.json')):z.write(f,f.name)
print('Created 4 pages, reader, production notes and ZIP.')
