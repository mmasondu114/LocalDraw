"use strict";

function panelJson(id, positive, negative) {
  return {id, visual_prompt:positive, negative};
}

async function copyPromptText(text) {
  try {await navigator.clipboard.writeText(text); return;} catch {}
  const host = document.activeElement?.closest("dialog") || [...document.querySelectorAll("dialog[open]")].at(-1) || document.body;
  const field = node("textarea"), previous = document.activeElement;
  field.className = "clipboard-fallback"; field.value = text; host.append(field);
  try {
    field.select();
    if (!document.execCommand("copy")) throw Error("复制失败，请在 JSON 框中全选并按 Ctrl+C。");
  } finally {field.remove();previous?.focus({preventScroll:true});}
}

function promptJsonEditor(positive, negative, id, state, change, fixedId=false, boardMode=false) {
  const group=node("fieldset",undefined,"prompt-json-editor"), tools=node("div",undefined,"prompt-json-tools");
  group.setAttribute("aria-label","提示词格式");
  const mode=node("input"), modeLabel=node("label"), fieldLabel=node("label","本格 JSON"), field=node("textarea");
  mode.type="checkbox";mode.checked=state.enabled===true;
  modeLabel.append(mode,document.createTextNode("正负面同框（JSON）"));
  field.spellcheck=false;field.rows=7;fieldLabel.append(field);
  const status=node("small");status.setAttribute("role","status");
  let lastPositive=positive.value,lastNegative=negative.value;
  const encode=()=>JSON.stringify([panelJson(state.id || id,positive.value,negative.value)],null,2);
  function read() {
    let data;
    try { data=JSON.parse(field.value); } catch {throw Error("JSON 格式有误，请检查引号、逗号或括号。");}
    let row=Array.isArray(data) && data.length===1 ? data[0] : data;
    if(boardMode && row && !Array.isArray(row))row={id:boardPanelId(row.id || row.panel_id),
      visual_prompt:row.visual_prompt ?? row.positive ?? row.prompt,negative:row.negative ?? row.negative_prompt ?? ""};
    if (!row || typeof row.id!=="string" || !/^P\d+-K\d+$/.test(row.id) || (fixedId && row.id!==id))
      throw Error(fixedId ? "请保留本格编号 "+id+"。" : "请填写一格，编号如 P001-K01。");
    if (typeof row.visual_prompt!=="string" || typeof row.negative!=="string")
      throw Error("visual_prompt 和 negative 都要填字符串，负面可填空字符串。");
    if ([row.visual_prompt,row.negative].some(s=>Array.from(s).length>20000)) throw Error("每项提示词最多 20000 字。");
    return row;
  }
  function validate() {
    try {const row=read();field.setCustomValidity("");return row;}
    catch(error) {field.setCustomValidity(error.message);return null;}
  }
  function display() {
    positive.parentElement.hidden=negative.parentElement.hidden=mode.checked;
    fieldLabel.hidden=!mode.checked;
    const error=mode.checked && field.validationMessage;
    field.setAttribute("aria-invalid",String(Boolean(error)));reset.hidden=!error;
    status.textContent=error || "";
  }
  function resetText() {
    delete state.raw;
    field.value=encode();field.setCustomValidity("");lastPositive=positive.value;lastNegative=negative.value;
    display();
  }
  const reset=button("还原为上次有效内容",()=>{resetText();change(false);field.focus();});
  const copy=button("复制本格 JSON",async()=>{
    if(mode.checked && !validate()){display();field.reportValidity();return;}
    const text=encode();
    await copyPromptText(text);status.textContent="已复制本格 JSON";
  });
  tools.append(modeLabel,copy,reset);group.append(tools,fieldLabel,status);
  positive.parentElement.before(group);
  field.value=state.raw ?? encode();validate();display();
  field.oninput=()=>{
    const row=validate();
    if(row) {
      positive.value=row.visual_prompt;negative.value=row.negative;state.id=row.id;
      lastPositive=positive.value;lastNegative=negative.value;delete state.raw;
    } else {state.raw=field.value;}
    display();change(true);
  };
  mode.onchange=()=>{
    if(!mode.checked && !validate()){mode.checked=true;display();field.reportValidity();return;}
    state.enabled=mode.checked;resetText();change(false);
    (mode.checked?field:positive).focus();
  };
  return {group,state,
    setId:value=>{state.id=value;resetText();},
    valid:()=>!mode.checked || field.validity.valid,
    refresh:()=>{if(positive.value!==lastPositive || negative.value!==lastNegative)resetText();}
  };
}
