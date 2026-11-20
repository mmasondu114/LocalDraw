// Keep pixel rounding and defaults aligned with image_size.py.
window.createImageSizeControls = function(root, saved = {}, {comic = false, onChange = () => {}} = {}) {
  let alignment=8;
  const presets = [["3:2", "横图 3:2"], ["2:3", "竖图 2:3"], ["1:1", "方图 1:1"],
    ["16:9", "宽图 16:9"], ["9:16", "长图 9:16"]];
  const defaults = {"1:1": [1024,1024], "2:3": [832,1216], "3:2": [1216,832],
    "9:16": [768,1344], "16:9": [1344,768]};
  const element = (tag, text) => {const el=document.createElement(tag);if(text)el.textContent=text;return el;};
  const field = (label, input, key) => {
    const wrapper=element("label",label);input.setAttribute("aria-label",label);
    input.dataset.editKey=(comic?"comic-":"prompt-")+key;wrapper.append(input);root.append(wrapper);return wrapper;
  };
  const option = (select, value, text) => {const item=element("option",text);item.value=value;select.append(item);};
  const ratio=element("select");field("纵横比",ratio,"ratio");
  if(comic)option(ratio,"storyboard","按各格分镜比例");
  presets.forEach(([value,label])=>option(ratio,value,label));
  if(!comic)option(ratio,"custom","自定义比例");
  const initial=saved.aspect_ratio||(comic?"storyboard":"3:2");
  ratio.value=[...ratio.options].some(o=>o.value===initial)?initial:(comic?"storyboard":"custom");
  const custom=element("div");custom.className="image-size-custom";root.append(custom);
  const initialParts=initial.split(":");
  const inputs=["宽", "高"].map((name,index)=>{
    const label=element("label",name),input=element("input");
    input.type="number";input.min="1";input.max="9999";input.step="1";
    input.value=saved[index?"custom_ratio_height":"custom_ratio_width"]??initialParts[index]??1;
    input.setAttribute("aria-label","自定义比例"+name);input.dataset.editKey="prompt-ratio-"+(index?"height":"width");
    label.append(input);custom.append(label);return input;
  });
  const resolution=element("select");field("分辨率",resolution,"resolution");
  for(const value of ["default","1024","1536","2048"])option(resolution,value,value);
  resolution.value=saved.resolution||"default";
  const note=element("small");note.className="image-size-note";note.setAttribute("role","status");root.append(note);
  const aspect=()=>ratio.value==="custom"?inputs.map(i=>i.value).join(":"):ratio.value;
  const dimensions=(value,level)=>{
    if(!/^[1-9][0-9]{0,3}:[1-9][0-9]{0,3}$/.test(value))throw Error("纵横比的宽和高须为 1–9999 的整数");
    const [w,h]=value.split(":").map(Number);
    if(w/h<1/8||w/h>8)throw Error("纵横比须在 1:8 到 8:1 之间");
    const standard=Object.keys(defaults).find(k=>{const [a,b]=k.split(":").map(Number);return w*b===h*a;});
    if(level==="default"&&standard)return defaults[standard];
    const long=level==="default"?1216:Number(level);
    return [w,h].map(n=>Math.round(n*long/Math.max(w,h)/alignment)*alignment);
  };
  const read=()=>{
    if(!["default","1024","1536","2048"].includes(resolution.value))throw Error("请选择分辨率");
    if(aspect()!=="storyboard")dimensions(aspect(),resolution.value);
    return {aspect_ratio:aspect(),resolution:resolution.value};
  };
  const summary=()=>{
    const value=read();
    return value.aspect_ratio==="storyboard"?`按分镜比例 · ${value.resolution==="default"?"默认分辨率":"长边 "+value.resolution+" 像素"}`:
      `${value.aspect_ratio} · ${dimensions(value.aspect_ratio,value.resolution).join(" × ")} 像素`;
  };
  const update=()=>{
    custom.hidden=comic||ratio.value!=="custom";
    try {
      for(const item of resolution.options){
        const label=item.value==="default"?"默认":`长边 ${item.value}`;
        item.textContent=aspect()==="storyboard"?label+" · 按各格比例":label+" · "+dimensions(aspect(),item.value).join(" × ");
      }
      note.textContent=summary()+`。尺寸按 ${alignment} 像素对齐，实际比例可能有微小取整差异。`;
      note.classList.remove("warning");
    } catch(error) {
      for(const item of resolution.options)item.textContent=item.value==="default"?"默认":`长边 ${item.value}`;
      note.textContent=error.message;note.classList.add("warning");
    }
  };
  for(const control of [ratio,resolution,...inputs])control.addEventListener(control.tagName==="INPUT"?"input":"change",()=>{update();onChange();});
  update();
  return {read,summary,setAlignment:(value)=>{alignment=value===16?16:8;update();},controls:[ratio,resolution,...inputs],draft:()=>({aspect_ratio:aspect(),resolution:resolution.value,
    custom_ratio_width:inputs[0].value,custom_ratio_height:inputs[1].value})};
};
