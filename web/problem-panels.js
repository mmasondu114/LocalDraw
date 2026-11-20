"use strict";
window.panelStoryPreview = function(project, panelId, node) {
  const entries=Object.values(project?.pages||{}).flatMap(page=>page.panels.map(panel=>({page,panel})));
  const index=entries.findIndex(item=>item.panel.id===panelId);
  const box=node('section',undefined,'panel-story-preview');
  box.setAttribute('aria-label',panelId+' 剧情预览');
  if(index<0){box.append(node('p','这一格暂无已确认剧情。'));return box;}
  const {page,panel}=entries[index];
  const names=new Map((project.canon?.characters||[]).map(c=>[c.id,c.name]));
  const action=panel.main_instant||panel.composition||panel.drawing_description;
  box.append(node('strong','这一格的剧情'));
  if(panel.purpose)box.append(node('p',panel.purpose,'panel-story-purpose'));
  box.append(node('p',action||'尚未填写画面动作。','panel-story-action'));
  const cast=[...new Set((panel.characters||[]).map(c=>names.get(c.character_id)||c.character_id))];
  const framing=panel.angle||panel.shot;
  if(cast.length||framing)box.append(node('small',[cast.length?'出场：'+cast.join('、'):'',framing?'镜头：'+framing:''].filter(Boolean).join('　·　')));
  const lines=(page.lettering||[]).filter(line=>line.panel_id===panelId).slice().sort((a,b)=>a.order-b.order);
  const dialogue=node('div',undefined,'panel-story-dialogue');
  dialogue.append(node('b','对白 / 旁白'));
  for(const line of lines){
    const speaker=names.get(line.speaker_id)||(line.kind==='narration'?'旁白':line.kind==='sfx'?'音效':line.speaker_id?'角色':'文字');
    dialogue.append(node('p',speaker+'：'+line.text));
  }
  if(!lines.length)dialogue.append(node('p','本格无对白，通过画面推进剧情。'));
  box.append(dialogue);
  const neighbors=node('details');neighbors.append(node('summary','看看前后剧情'));
  for(const [offset,label] of [[-1,'上一格'],[1,'下一格']]){
    const other=entries[index+offset]?.panel;
    if(other)neighbors.append(node('p',`${label} ${other.id}：${other.main_instant||other.composition||other.purpose||'暂无描述'}`));
  }
  if(entries.length>1)box.append(neighbors);
  return box;
};
window.renderProblemPanels = async function(root, ctx) {
  const {pid, api, act, node, button, notify} = ctx;
  await window.flushProblemEdits?.();
  window.flushProblemEdits = null;
  const [state, drawing] = await Promise.all([
    api('/api/problems?id='+encodeURIComponent(pid)), api('/api/drawing?id='+encodeURIComponent(pid))]);
  if (!ctx.current()) return;
  root.className='problem-studio';root.replaceChildren();
  const edits=[], pending=new Map();let timer, saving=Promise.resolve();
  const saveStatus=node('p','问题说明自动保存。','problem-save-status');
  function flush() {
    clearTimeout(timer);
    saving=saving.catch(()=>{}).then(async()=>{
      for(const [key, item] of [...pending]) {
        await act('save_problem',{project:pid,...item});
        if(pending.get(key)===item)pending.delete(key);
      }
      saveStatus.textContent=pending.size?'正在保存…':'问题说明已保存。';
    });
    return saving;
  }
  window.flushProblemEdits=flush;
  function queue(edit) {
    pending.set(edit.row.panel_id,{panel_id:edit.row.panel_id,note:edit.note.value,candidate_count:Number(edit.count.value)});
    saveStatus.textContent='正在保存…';clearTimeout(timer);
    if(edit.note.value.trim()!==edit.row.note){edit.row.ready=false;edit.result.textContent='问题说明已修改，请重新理解。';}
    timer=setTimeout(()=>flush().catch(error=>{saveStatus.textContent='未保存：'+error.message;notify(error.message,true);}),400);
  }
  async function reload(){await flush();await window.renderProblemPanels(root,ctx);}
  root.append(node('h2',`问题格子汇总 · ${state.items.length} 格`),
    node('p','逐格描述问题，选择 GPT 理解或本地 LLM 理解；核对修改说明后统一重画，再从新候选中选图。'),saveStatus);
  const toolbar=node('div',undefined,'problem-toolbar');
  const allCount=node('input');allCount.type='number';allCount.min='1';allCount.max='16';allCount.value='8';
  const allLabel=node('label','统一设置每格张数');allLabel.append(allCount);
  toolbar.append(allLabel,button('应用到所有问题格',async()=>{
    const count=Number(allCount.value);if(!Number.isInteger(count)||count<1||count>16)throw Error('每格张数须为 1–16。');
    for(const edit of edits){edit.count.value=String(count);queue(edit);}await flush();updateTotal();
  }));
  const total=node('strong');function updateTotal(){total.textContent=`本次共 ${edits.reduce((n,e)=>n+(Number(e.count.value)||0),0)} 张新候选`;}
  toolbar.append(total,button('刷新结果',reload));root.append(toolbar);
  const methods=node('section',undefined,'problem-methods');
  const mode=node('select');mode.setAttribute('aria-label','问题理解方式');
  for(const [value,text] of [['gpt','GPT 理解 · 手动导出／导回 JSON'],['llm','LLM 理解 · 本地 FlashNext 64K']]){
    const option=node('option',text);option.value=value;mode.append(option);
  }
  const modeLabel=node('label','理解方式');modeLabel.append(mode);
  const gpt=node('div',undefined,'problem-method-actions'),llm=node('div',undefined,'problem-method-actions');
  const upload=node('input');upload.type='file';upload.accept='.json,application/json';upload.hidden=true;
  const json=node('textarea');json.rows=4;json.placeholder='也可以把 GPT 返回的完整 JSON 粘贴到这里';json.setAttribute('aria-label','GPT 返回的 JSON');
  async function importText(text){const document=JSON.parse(text.trim().replace(/^```(?:json)?\s*/i,'').replace(/\s*```$/,''));await flush();await act('import_problems',{project:pid,document});notify('GPT 理解结果已导入，请核对后重画。');await reload();}
  upload.onchange=async()=>{try{if(upload.files[0])await importText(await upload.files[0].text());}catch(error){notify(error.message,true);}};
  gpt.append(node('p','导出 JSON → 你亲自交给 GPT → 将 GPT 返回的完整 JSON 导回。此处不会自动调用 GPT。'),
    button('导出问题 JSON 给 GPT',async()=>{
      await flush();const document=await act('export_problems',{project:pid});
      const url=URL.createObjectURL(new Blob([JSON.stringify(document,null,2)],{type:'application/json;charset=utf-8'}));
      const link=node('a');link.href=url;link.download=`${pid}-问题格子.json`;documentBodyAppend(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),10000);
      notify('已导出。让 GPT 填写理解说明和绘图提示词，再导回本步骤。');
    }),button('导入 GPT 返回的 JSON 文件',()=>{upload.value='';upload.click();}),upload,json,
    button('导入粘贴的 JSON',()=>importText(json.value)));
  function documentBodyAppend(el){window.document.body.append(el);}
  const understand=button('让 LLM 理解全部问题',async()=>{
    await flush();await act('understand_problems',{project:pid});notify('FlashNext 开始理解问题，完成后在这里核对修改说明。');
  },'primary');understand.dataset.startJob='true';understand.disabled=Boolean(window.activeStudioJob);
  llm.append(node('p','使用本地 FlashNext，64K 上下文。先生成问题理解和修改后的提示词，完成后由你确认重画。'),understand);
  mode.onchange=()=>{gpt.hidden=mode.value!=='gpt';llm.hidden=mode.value!=='llm';};mode.onchange();
  methods.append(modeLabel,gpt,llm);if(state.items.length)root.append(methods);
  const cards=node('div',undefined,'problem-cards');root.append(cards);
  for(const row of state.items){
    const card=node('article',undefined,'problem-card');card.dataset.panelId=row.panel_id;
    const header=node('div',undefined,'problem-card-header');
    header.append(node('h3',row.panel_id),button('已满意，解除问题标记',async()=>{
      await flush();await act('save_problem',{project:pid,panel_id:row.panel_id,marked:false});await reload();
    }));card.append(header,window.panelStoryPreview(ctx.project,row.panel_id,node));
    const batches=drawing.images.batches.filter(b=>b.panel_id===row.panel_id && b.panel_hash===drawing.panel_hashes[row.panel_id]);
    const selected=drawing.images.selections[row.panel_id]?.image?.id;
    const images=[...new Map(batches.slice().reverse().flatMap(b=>b.images.slice().reverse()).map(im=>[im.id,im])).values()];
    const picked=images.find(im=>im.id===(row.image_id||selected));
    const shown=picked?[picked,...images.filter(im=>im.id!==picked.id)]:images;
    const grid=node('div',undefined,'image-grid choice-grid problem-images');
    const renderImage=image=>{
      const option=node('article',undefined,'image-option'+(image.id===selected?' selected':''));
      const img=node('img');const [bundle,file]=image.file.split('/');
      img.src=`/api/image?id=${encodeURIComponent(pid)}&bundle=${encodeURIComponent(bundle)}&file=${encodeURIComponent(file)}`;
      img.alt=`${row.panel_id} 候选 ${image.view_index||''}`;img.loading='lazy';
      const preview=node('a');preview.href=img.src;preview.target='_blank';preview.rel='noopener';preview.append(img);
      option.append(preview,node('p',`${image.id===row.image_id?'问题参考图 · ':''}候选 ${image.view_index||''}`),
        button(image.id===selected?'✓ 已选用':'选用此图',async()=>{
          await flush();await act('select_image',{project:pid,target:row.panel_id,image:image.id,allow_unreviewed:true,allow_rejected:true});
          ctx.clearFinishedCache();await reload();
        }),button('用这张图说明问题',async()=>{
          await flush();await act('save_problem',{project:pid,panel_id:row.panel_id,image_id:image.id});await reload();
        }));return option;
    };
    for(const image of shown.slice(0,8))grid.append(renderImage(image));
    if(!shown.length)grid.append(node('p','这一格尚无有效候选，仍可填写问题后重画。'));
    card.append(grid);
    if(shown.length>8){const more=node('details');more.append(node('summary',`查看其余 ${shown.length-8} 张候选`));let loaded=false;more.ontoggle=()=>{if(more.open&&!loaded){loaded=true;const rest=node('div',undefined,'image-grid choice-grid problem-images');shown.slice(8).forEach(im=>rest.append(renderImage(im)));more.append(rest);}};card.append(more);}
    const note=node('textarea');note.rows=4;note.maxLength=4000;note.value=row.note;note.placeholder='例如：手里应该只有一个杯子，现在多画了一个；镜头要保持旅行者第一人称。';
    const label=node('label','这格有什么问题？');label.append(note);
    const count=node('input');count.type='number';count.min='1';count.max='16';count.value=String(row.candidate_count);
    const countLabel=node('label','这一格重画几张');countLabel.append(count);
    const result=node('section',undefined,'problem-understanding');
    if(row.ready){result.append(node('strong',row.prepared.provider==='gpt'?'GPT 理解结果':'LLM 理解结果'),node('p',row.prepared.understanding),node('p','修改方案：'+row.prepared.changes));
      const prompts=node('details');prompts.append(node('summary','查看将使用的提示词'),node('p',row.prepared.positive),node('p',row.prepared.negative));result.append(prompts);
    }else result.append(node('p','待理解问题。'));
    const edit={row,note,count,result};edits.push(edit);
    note.oninput=()=>queue(edit);count.oninput=()=>{queue(edit);updateTotal();};
    card.append(label,countLabel,result);cards.append(card);
  }
  updateTotal();
  if(!state.items.length)root.append(node('p','还没有问题格子。在“绘图与选图”中点击“标记有问题”，即可加入这里。'));
  const actions=node('div',undefined,'problem-toolbar');
  const draw=button('按理解结果全部重画',async()=>{
    await flush();if(window.activeStudioJob)throw Error('请等待当前任务结束。');
    const blank=edits.find(edit=>!edit.note.value.trim());
    if(blank){blank.note.focus();throw Error(blank.row.panel_id+' 还缺少问题说明。');}
    await act(edits.every(edit=>edit.row.ready)?'redraw_problems':'understand_and_redraw',{project:pid});notify('已按每格设置的数量开始重画。完成后在这里选择新图；问题标记由你手动解除。');
  },'primary');draw.dataset.startJob='true';draw.dataset.unavailable=String(!state.items.length);draw.disabled=!state.items.length||Boolean(window.activeStudioJob);
  actions.append(button('返回绘图与选图',ctx.goDrawing),draw,button('下一步 · 05 成品查看',()=>window.openFinishedComic()));root.append(actions);
};
