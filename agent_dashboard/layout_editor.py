"""Inline, data-only layout editor for the local dashboard page."""

LAYOUT_EDITOR_CSS = r'''
.layout-open-overlay{position:absolute;right:14px;top:14px;z-index:2;background:#101a25e8;border-color:#3e5963;opacity:.86}
.layout-open-overlay:hover{opacity:1}
.layout-editor[hidden]{display:none!important}
.layout-editor{margin-top:22px;padding:20px;border:1px solid var(--ui-edge);border-radius:var(--ui-radius);background:var(--ui-surface);box-shadow:0 16px 44px #0003}
.layout-editor-head{display:flex;align-items:flex-start;justify-content:space-between;gap:16px;flex-wrap:wrap;margin-bottom:16px}
.layout-editor-head h2{margin:0 0 5px;font-size:19px}
.layout-editor-head p,.layout-editor-help{margin:0;color:var(--ui-muted);font-size:13px;line-height:1.65}
.layout-editor-name{display:flex;align-items:center;gap:8px;color:var(--ui-muted);font-size:13px}
.layout-editor-name input,.layout-editor-inspector select,.layout-editor-inspector input{min-width:0;background:var(--ui-background);border:1px solid var(--ui-edge);border-radius:7px;padding:9px 10px;color:var(--ui-text);font:inherit}
.layout-editor-name input{width:min(260px,60vw)}
.layout-editor-actions{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.layout-editor-actions button{padding:9px 12px}
.layout-editor-actions .layout-apply{border-color:var(--ui-codex);color:var(--ui-codex)}
.layout-editor-grid{display:grid;grid-template-columns:minmax(0,1fr) 250px;gap:18px;align-items:start}
.layout-editor-stage{position:relative;width:100%;aspect-ratio:4/1;overflow:hidden;border:1px solid var(--ui-edge);border-radius:var(--ui-radius-small);background:var(--ui-background);outline:none;touch-action:manipulation;user-select:none}
.layout-editor-stage:focus-visible{box-shadow:0 0 0 2px var(--ui-codex)}
.layout-editor-stage.is-dragging{cursor:grabbing}
.layout-editor-image{position:absolute;inset:0;display:block;width:100%;height:100%;object-fit:fill;pointer-events:none}
.layout-editor-overlay{position:absolute;inset:0;width:100%;height:100%;overflow:visible;touch-action:none}
.layout-hitbox{fill:transparent;stroke:transparent;stroke-width:12;vector-effect:non-scaling-stroke;pointer-events:all;cursor:pointer}
.layout-hitbox:hover{fill:#78e7c21a;stroke:#78e7c2a8}
.layout-hitbox.kind-group{stroke-dasharray:5 4}
.layout-hitbox.is-hidden{fill:#f7ba7118;stroke:#f7ba71;stroke-dasharray:3 4;pointer-events:none}
.layout-hitbox.is-selected{fill:#78e7c224;stroke:var(--ui-codex);stroke-width:2;stroke-dasharray:none}
.layout-hitbox.is-primary{stroke:#fff;stroke-width:2.5}
.layout-editor-inspector{display:grid;gap:12px;padding:15px;border:1px solid var(--ui-edge);border-radius:var(--ui-radius-small);background:var(--ui-background)}
.layout-editor-inspector label{display:grid;gap:6px;color:var(--ui-muted);font-size:12px}
.layout-editor-inspector select{width:100%;font-size:13px}
.layout-editor-inspector input{width:100%;font-family:Consolas,monospace;font-size:14px}
.layout-editor-inspector input:disabled{opacity:.45}
.layout-editor-xy{display:grid;grid-template-columns:1fr 1fr;gap:9px}
.layout-editor-font-row{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:8px;align-items:end}
.layout-editor-font-row button,.layout-editor-default{padding:7px 9px;font-size:11px}
.layout-editor-color-row{display:grid;grid-template-columns:52px minmax(0,1fr) auto;gap:8px;align-items:center}
.layout-editor-color-row input[type=color]{width:52px;height:36px;padding:3px}
.layout-editor-color-state{color:var(--ui-subtle);font-size:11px;overflow-wrap:anywhere}
.layout-editor-check-grid{display:grid;grid-template-columns:1fr 1fr;gap:8px}
.layout-editor-check{display:flex!important;align-items:center;gap:7px!important}
.layout-editor-check input{width:auto}
.layout-editor-align{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:6px}
.layout-editor-align button{padding:7px 5px;font-size:11px}
.layout-editor-align button:disabled{opacity:.4}
.layout-editor-control-title{margin:4px 0 0;color:var(--ui-text);font-size:12px;font-weight:600}
.layout-editor-selection{font-size:13px;color:var(--ui-text);font-weight:600}
.layout-editor-status{min-height:22px;margin:10px 0 0;color:var(--ui-muted);font-size:12px}
.layout-editor-help{margin-top:14px}
@media(max-width:900px){.layout-editor-grid{grid-template-columns:1fr}.layout-editor-inspector{grid-template-columns:repeat(2,minmax(0,1fr))}.layout-editor-selection{grid-column:1/-1}}
@media(max-width:560px){.layout-editor-inspector{grid-template-columns:1fr}.layout-editor-name{width:100%}.layout-editor-name input{flex:1}}
'''

LAYOUT_EDITOR_HTML = r'''
<section class="layout-editor" id="layout-editor" hidden aria-labelledby="layout-editor-title">
  <div class="layout-editor-head">
    <div>
      <h2 id="layout-editor-title">可视布局编辑</h2>
      <p>点击元素选中；Ctrl / ⌘ 点击可多选。聚焦画布后用方向键微调，Shift + 方向键每次移动 10 px。</p>
    </div>
    <label class="layout-editor-name" for="layout-editor-name">样式名称
      <input id="layout-editor-name" type="text" maxlength="48" autocomplete="off">
    </label>
    <div class="layout-editor-actions">
      <button type="button" id="layout-editor-undo">撤销草稿</button>
      <button type="button" id="layout-editor-reset-selected">重置所选</button>
      <button type="button" id="layout-editor-export">导出草稿 JSON</button>
      <button type="button" id="layout-editor-cancel">取消</button>
      <button type="button" id="layout-editor-save-as">另存为</button>
      <button type="button" class="layout-apply" id="layout-editor-apply">保存并应用</button>
    </div>
  </div>
  <div class="layout-editor-grid">
    <div>
      <div class="layout-editor-stage" id="layout-editor-stage" tabindex="0" role="application" aria-label="1920乘480布局编辑画布">
        <img class="layout-editor-image" id="layout-editor-image" alt="当前布局草稿预览" draggable="false">
        <svg class="layout-editor-overlay" id="layout-editor-overlay" viewBox="0 0 1920 480" preserveAspectRatio="none" aria-label="布局元素选择层"></svg>
      </div>
      <p class="layout-editor-status" id="layout-editor-status" role="status" aria-live="polite"></p>
    </div>
    <aside class="layout-editor-inspector" aria-label="所选元素属性">
      <div class="layout-editor-selection" id="layout-editor-selection-count">未选中元素</div>
      <label for="layout-editor-element">元素（包含线条与隐藏元素）
        <select id="layout-editor-element"><option value="">未选中</option></select>
      </label>
      <div class="layout-editor-xy">
        <label for="layout-editor-x">X（px）<input id="layout-editor-x" type="number" step="1" inputmode="numeric"></label>
        <label for="layout-editor-y">Y（px）<input id="layout-editor-y" type="number" step="1" inputmode="numeric"></label>
      </div>
      <label for="layout-editor-font">文字字号（8–96 px）
        <input id="layout-editor-font" type="number" min="8" max="96" step="1" inputmode="numeric" placeholder="仅文字元素">
      </label>
      <label for="layout-editor-font-family">本机字体
        <select id="layout-editor-font-family"><option value="">默认</option></select>
      </label>
      <div>
        <div class="layout-editor-control-title">文字颜色</div>
        <div class="layout-editor-color-row">
          <input id="layout-editor-color" type="color" value="#eff4f9" aria-label="文字颜色">
          <span class="layout-editor-color-state" id="layout-editor-color-state">默认</span>
          <button type="button" class="layout-editor-default" id="layout-editor-color-default">默认</button>
        </div>
      </div>
      <div>
        <div class="layout-editor-control-title">文字样式</div>
        <div class="layout-editor-check-grid">
          <label class="layout-editor-check" for="layout-editor-bold"><input id="layout-editor-bold" type="checkbox"><span id="layout-editor-bold-state">加粗</span></label>
          <button type="button" class="layout-editor-default" id="layout-editor-bold-default">默认</button>
          <label class="layout-editor-check" for="layout-editor-italic"><input id="layout-editor-italic" type="checkbox"><span id="layout-editor-italic-state">斜体</span></label>
          <button type="button" class="layout-editor-default" id="layout-editor-italic-default">默认</button>
        </div>
      </div>
      <div>
        <div class="layout-editor-control-title">多选对齐</div>
        <div class="layout-editor-align">
          <button type="button" id="layout-editor-align-left" title="按左边缘对齐">左对齐</button>
          <button type="button" id="layout-editor-align-center-x" title="按水平中心对齐">水平居中</button>
          <button type="button" id="layout-editor-align-right" title="按右边缘对齐">右对齐</button>
          <button type="button" id="layout-editor-align-top" title="按上边缘对齐">顶对齐</button>
          <button type="button" id="layout-editor-align-center-y" title="按垂直中心对齐">垂直居中</button>
          <button type="button" id="layout-editor-align-bottom" title="按下边缘对齐">底对齐</button>
          <button type="button" id="layout-editor-distribute-x" title="首尾位置不变，水平等距分布">水平等距</button>
          <button type="button" id="layout-editor-distribute-y" title="首尾位置不变，垂直等距分布">垂直等距</button>
        </div>
      </div>
    </aside>
  </div>
  <p class="layout-editor-help">预览只修改样式草稿，不写入屏幕或配置。X/Y 以元素边框左上角为准；字号只应用于所选文字元素，多选时会跳过图形和分组。</p>
</section>
'''

LAYOUT_EDITOR_JS = r'''
(() => {
  const byId = id => document.getElementById(id);
  const section = byId('layout-editor');
  const stage = byId('layout-editor-stage');
  const overlay = byId('layout-editor-overlay');
  const previewImage = byId('layout-editor-image');
  const status = byId('layout-editor-status');
  const nameInput = byId('layout-editor-name');
  const elementSelect = byId('layout-editor-element');
  const xInput = byId('layout-editor-x');
  const yInput = byId('layout-editor-y');
  const fontInput = byId('layout-editor-font');
  const fontFamilyInput = byId('layout-editor-font-family');
  const colorInput = byId('layout-editor-color');
  const colorState = byId('layout-editor-color-state');
  const boldInput = byId('layout-editor-bold');
  const italicInput = byId('layout-editor-italic');
  const boldState = byId('layout-editor-bold-state');
  const italicState = byId('layout-editor-italic-state');
  const selectionLabel = byId('layout-editor-selection-count');
  const ns = 'http://www.w3.org/2000/svg';
  const idPattern = /^[a-z0-9][a-z0-9_-]{0,39}$/;
  const allowedKinds = new Set(['text', 'shape', 'image', 'group']);
  const editor = {
    open: false, builtin: false, original: null, profile: null, scene: [],
    sceneById: new Map(), selected: new Set(), primary: null,
    sequence: 0, timer: 0, controller: null, saving: false, opening: false,
    previewPending: false, fonts: [], fontsLoaded: false
  };
  let pointerGesture = null;
  let suppressClick = false;
  let suppressClickTimer = 0;

  function clone(value) { return JSON.parse(JSON.stringify(value)); }
  function setStatus(value) { status.textContent = value || ''; }
  function setSaving(value) {
    editor.saving = value;
    for (const id of ['layout-editor-undo','layout-editor-reset-selected','layout-editor-export',
                      'layout-editor-save-as','layout-editor-apply','layout-editor-cancel',
                      'layout-editor-font-family','layout-editor-color','layout-editor-color-default',
                      'layout-editor-bold','layout-editor-bold-default','layout-editor-italic','layout-editor-italic-default',
                      'layout-editor-element','layout-editor-x','layout-editor-y','layout-editor-font','layout-editor-name',
                      'layout-editor-align-left','layout-editor-align-center-x','layout-editor-align-right',
                      'layout-editor-align-top','layout-editor-align-center-y','layout-editor-align-bottom',
                      'layout-editor-distribute-x','layout-editor-distribute-y']) {
      byId(id).disabled = value;
    }
    if (!value) updateInspector();
  }
  function apiHeaders() {
    return {'Content-Type':'application/json','X-Dashboard-Token':token};
  }
  function cleanText(value, fallback) {
    if (typeof value !== 'string') return fallback;
    return value.replace(/[\u0000-\u001f\u007f]/g,' ').trim().slice(0,96) || fallback;
  }
  function normalizeScene(value) {
    if (!Array.isArray(value) || value.length > 512) throw Error('布局元素清单无效');
    const result = [];
    const ids = new Set();
    for (const item of value) {
      if (!item || typeof item !== 'object' || Array.isArray(item) || typeof item.id !== 'string' ||
          !item.id || item.id.length > 128 || /[\u0000-\u001f\u007f]/.test(item.id) || ids.has(item.id) ||
          !allowedKinds.has(item.kind) || !Array.isArray(item.bbox) || item.bbox.length !== 4 ||
          !item.bbox.every(Number.isFinite)) continue;
      if (item.parent !== null && item.parent !== undefined &&
          (typeof item.parent !== 'string' || item.parent.length > 128)) continue;
      const row = {
        id:item.id, label:cleanText(item.label,item.id), kind:item.kind,
        parent:typeof item.parent === 'string' ? item.parent : null,
        visible:item.visible!==false,
        bbox:item.bbox.map(Number), default_bbox:Array.isArray(item.default_bbox) &&
          item.default_bbox.length === 4 && item.default_bbox.every(Number.isFinite) ? item.default_bbox.map(Number) : item.bbox.map(Number)
      };
      if (Number.isInteger(item.font_size) && item.font_size >= 8 && item.font_size <= 96) row.font_size = item.font_size;
      if (Number.isInteger(item.default_font_size) && item.default_font_size >= 8 && item.default_font_size <= 96) row.default_font_size = item.default_font_size;
      for (const key of ['color','default_color']) {
        if (typeof item[key]==='string' && /^#[0-9a-fA-F]{6}$/.test(item[key])) row[key]=item[key].toUpperCase();
      }
      for (const key of ['bold','default_bold','italic','default_italic']) {
        if (typeof item[key]==='boolean') row[key]=item[key];
      }
      for (const key of ['font_family','default_font_family']) {
        if (item[key]==='' || (typeof item[key]==='string' && /^[A-Za-z0-9_-]{1,80}$/.test(item[key]))) row[key]=item[key];
      }
      ids.add(row.id);
      result.push(row);
    }
    return result;
  }
  function closeEditor() {
    if (editor.controller) editor.controller.abort();
    clearTimeout(editor.timer);
    editor.open = false;
    editor.saving = false;
    editor.opening = false;
    editor.previewPending = false;
    pointerGesture = null;
    stage.classList.remove('is-dragging');
    editor.original = null;
    editor.profile = null;
    editor.scene = [];
    editor.sceneById.clear();
    editor.selected.clear();
    editor.primary = null;
    editor.sequence++;
    previewImage.removeAttribute('src');
    overlay.replaceChildren();
    section.hidden = true;
    uiBusy = false;
    setSaving(false);
    for (const card of document.querySelectorAll('.ui-profile-card')) card.disabled = false;
  }
  function hasAncestorSelected(id) {
    let parent = editor.sceneById.get(id)?.parent;
    const visited = new Set();
    while (parent && !visited.has(parent)) {
      if (editor.selected.has(parent)) return true;
      visited.add(parent);
      parent = editor.sceneById.get(parent)?.parent || null;
    }
    return false;
  }
  function isDescendant(id, ancestor) {
    let parent = editor.sceneById.get(id)?.parent;
    const visited = new Set();
    while (parent && !visited.has(parent)) {
      if (parent === ancestor) return true;
      visited.add(parent);
      parent = editor.sceneById.get(parent)?.parent || null;
    }
    return false;
  }
  function selectedMoveRoots() {
    return [...editor.selected].filter(id => !hasAncestorSelected(id));
  }
  function setSelection(id, multi) {
    if (editor.saving || !editor.sceneById.has(id)) return;
    if (multi) {
      if (editor.selected.has(id)) {
        editor.selected.delete(id);
        if (editor.primary===id) editor.primary = [...editor.selected].pop() || null;
      } else {
        editor.selected.add(id);
        editor.primary = id;
      }
    } else {
      editor.selected.clear();
      editor.selected.add(id);
      editor.primary = id;
    }
    stage.focus({preventScroll:true});
    drawOverlay();
    updateInspector();
  }
  function clearSelection() {
    editor.selected.clear();
    editor.primary = null;
    drawOverlay();
    updateInspector();
  }
  function boxArea(item) {
    const b = item.bbox;
    return Math.max(0, b[2]-b[0]) * Math.max(0, b[3]-b[1]);
  }
  function drawOverlay() {
    overlay.replaceChildren();
    // Paint large groups first so thin/small elements remain easy to select.
    const ordered = [...editor.scene].sort((a,b) => boxArea(b)-boxArea(a));
    for (const item of ordered) {
      if (!item.visible && !editor.selected.has(item.id)) continue;
      const [left,top,right,bottom] = item.bbox;
      const rect = document.createElementNS(ns,'rect');
      rect.setAttribute('x',String(left));
      rect.setAttribute('y',String(top));
      rect.setAttribute('width',String(Math.max(0.5,right-left)));
      rect.setAttribute('height',String(Math.max(0.5,bottom-top)));
      rect.setAttribute('aria-label',item.label);
      rect.setAttribute('data-element-id',item.id);
      rect.classList.add('layout-hitbox','kind-'+item.kind);
      if (!item.visible) rect.classList.add('is-hidden');
      if (editor.selected.has(item.id)) rect.classList.add('is-selected');
      if (editor.primary === item.id) rect.classList.add('is-primary');
      overlay.appendChild(rect);
    }
  }
  function effectiveStyle(item,key,defaultKey,fallback) {
    if (item[key]!==undefined && item[key]!==null && item[key]!=='') return item[key];
    if (item[defaultKey]!==undefined && item[defaultKey]!==null && item[defaultKey]!=='') return item[defaultKey];
    return fallback;
  }
  function hasStyleOverride(id,key) {
    const entry=editor.profile&&editor.profile.elements&&editor.profile.elements[id];
    return !!(entry && Object.prototype.hasOwnProperty.call(entry,key));
  }
  function setFontOptions(value,mixed) {
    fontFamilyInput.replaceChildren(new Option('默认字体',''));
    for (const font of editor.fonts) fontFamilyInput.appendChild(new Option(font.name,font.id));
    if (value && !editor.fonts.some(font=>font.id===value)) fontFamilyInput.appendChild(new Option('当前字体（未列出）',value));
    if (mixed) fontFamilyInput.appendChild(new Option('多种字体','__mixed__'));
    fontFamilyInput.value=mixed?'__mixed__':(value||'');
  }
  async function loadFonts() {
    if (editor.fontsLoaded) return;
    editor.fontsLoaded=true;
    try {
      const response=await fetch('/api/ui/fonts',{cache:'no-store',credentials:'same-origin'});
      const payload=await response.json().catch(()=>({}));
      if (!response.ok || !Array.isArray(payload.fonts)) throw Error(payload.error||'字体列表不可用');
      const seen=new Set();
      editor.fonts=payload.fonts.filter(font=>font&&typeof font.id==='string'&&
        /^[A-Za-z0-9_-]{1,80}$/.test(font.id)&&typeof font.name==='string'&&
        font.name.length<=80&&!seen.has(font.id)&&seen.add(font.id));
    } catch (_) {
      editor.fonts=[];
      if (editor.open) setStatus('字体列表不可用；可继续编辑位置、颜色和其它样式。');
    }
  }
  function updateInspector() {
    const items = editor.scene;
    const previous = elementSelect.value;
    elementSelect.replaceChildren(new Option('未选中',''));
    for (const item of items) {
      const indent = item.parent ? '　↳ ' : '';
      const visibility = item.visible ? '' : '（隐藏）';
      elementSelect.appendChild(new Option(indent+item.label+visibility,item.id));
    }
    elementSelect.value = editor.primary || (items.some(item=>item.id===previous) ? previous : '');
    const primary = editor.sceneById.get(editor.primary);
    const hasSelection = editor.selected.size > 0;
    selectionLabel.textContent = hasSelection ? `已选 ${editor.selected.size} 个元素` : '未选中元素';
    xInput.disabled = !primary || editor.saving || editor.previewPending;
    yInput.disabled = !primary || editor.saving || editor.previewPending;
    xInput.value = primary ? String(Math.round(primary.bbox[0])) : '';
    yInput.value = primary ? String(Math.round(primary.bbox[1])) : '';
    const texts = [...editor.selected].map(id=>editor.sceneById.get(id)).filter(item=>item&&item.kind==='text');
    fontInput.disabled = texts.length===0 || editor.saving;
    const sizes = [...new Set(texts.map(item=>item.font_size ?? item.default_font_size).filter(Number.isInteger))];
    fontInput.value = sizes.length===1 ? String(sizes[0]) : '';
    fontInput.placeholder = texts.length===0 ? '仅文字元素' : sizes.length>1 ? '多种字号' : '字号';
    const families=texts.map(item=>hasStyleOverride(item.id,'font_family')?String(editor.profile.elements[item.id].font_family||''):'');
    const uniqueFamilies=[...new Set(families)];
    fontFamilyInput.disabled=texts.length===0 || editor.saving;
    setFontOptions(uniqueFamilies.length===1?uniqueFamilies[0]:'',uniqueFamilies.length>1);
    const colors=texts.map(item=>effectiveStyle(item,'color','default_color','#eff4f9'));
    const uniqueColors=[...new Set(colors)];
    const primaryText=texts.find(item=>item.id===editor.primary)||texts[0];
    const displayColor=uniqueColors.length===1?uniqueColors[0]:
      (primaryText?effectiveStyle(primaryText,'color','default_color','#eff4f9'):'#eff4f9');
    colorInput.disabled=texts.length===0 || editor.saving;
    colorInput.value=/^#[0-9a-fA-F]{6}$/.test(displayColor)?displayColor:'#eff4f9';
    colorState.textContent=texts.length===0?'仅文字元素':uniqueColors.length>1?'多种颜色':
      texts.every(item=>!hasStyleOverride(item.id,'color'))?'默认':`当前 ${displayColor.toUpperCase()}`;
    const bolds=texts.map(item=>Boolean(effectiveStyle(item,'bold','default_bold',false)));
    boldInput.disabled=texts.length===0 || editor.saving;
    const mixedBold=new Set(bolds).size>1;
    boldInput.indeterminate=mixedBold;
    boldInput.checked=bolds.length>0?bolds[0]:false;
    boldState.textContent=mixedBold?'加粗（多种）':'加粗';
    const italics=texts.map(item=>Boolean(effectiveStyle(item,'italic','default_italic',false)));
    italicInput.disabled=texts.length===0 || editor.saving;
    const mixedItalic=new Set(italics).size>1;
    italicInput.indeterminate=mixedItalic;
    italicInput.checked=italics.length>0?italics[0]:false;
    italicState.textContent=mixedItalic?'斜体（多种）':'斜体';
    const roots=selectedMoveRoots();
    for (const id of ['layout-editor-align-left','layout-editor-align-center-x','layout-editor-align-right',
                      'layout-editor-align-top','layout-editor-align-center-y','layout-editor-align-bottom']) {
      byId(id).disabled=editor.saving || editor.previewPending || roots.length<2;
    }
    byId('layout-editor-distribute-x').disabled=editor.saving || editor.previewPending || roots.length<3;
    byId('layout-editor-distribute-y').disabled=editor.saving || editor.previewPending || roots.length<3;
    for (const id of ['layout-editor-color-default','layout-editor-bold-default','layout-editor-italic-default']) {
      byId(id).disabled=editor.saving || texts.length===0;
    }
    for (const id of ['layout-editor-align-left','layout-editor-align-center-x','layout-editor-align-right',
                      'layout-editor-align-top','layout-editor-align-center-y','layout-editor-align-bottom',
                      'layout-editor-distribute-x','layout-editor-distribute-y']) {
      // Alignment buttons remain disabled until the returned scene provides enough editable roots.
      if (!editor.open) byId(id).disabled=true;
    }
    for (const id of ['layout-editor-reset-selected','layout-editor-undo','layout-editor-export','layout-editor-save-as','layout-editor-apply']) {
      byId(id).disabled = editor.saving || !editor.open;
    }
    byId('layout-editor-reset-selected').disabled = editor.saving || !editor.open || !hasSelection;
  }
  function queuePreview(immediate=false) {
    if (!editor.open || editor.saving || !editor.profile) return;
    editor.previewPending = true;
    updateInspector();
    clearTimeout(editor.timer);
    if (editor.controller) editor.controller.abort();
    const sequence = ++editor.sequence;
    editor.timer = setTimeout(()=>sendPreview(sequence),immediate?0:150);
  }
  async function sendPreview(sequence) {
    if (!editor.open || sequence!==editor.sequence) return;
    const controller = new AbortController();
    editor.controller = controller;
    setStatus('正在生成未保存预览…');
    try {
      const body = JSON.stringify({profile:editor.profile});
      if (new Blob([body]).size > 65536) throw Error('样式草稿超过 64 KiB');
      const response = await fetch('/api/ui/editor/preview',{
        method:'POST',credentials:'same-origin',signal:controller.signal,
        headers:apiHeaders(),body
      });
      const payload = await response.json().catch(()=>({}));
      if (sequence!==editor.sequence || !editor.open) return;
      if (!response.ok) throw Error(payload.error || '预览失败');
      if (!payload.profile || typeof payload.profile!=='object' ||
          typeof payload.image!=='string' || !/^data:image\/png;base64,[A-Za-z0-9+/]+={0,2}$/.test(payload.image)) {
        throw Error('预览响应格式无效');
      }
      const scene = normalizeScene(payload.scene);
      editor.profile = clone(payload.profile);
      editor.scene = scene;
      editor.previewPending = false;
      editor.sceneById = new Map(scene.map(item=>[item.id,item]));
      for (const id of [...editor.selected]) if (!editor.sceneById.has(id)) editor.selected.delete(id);
      if (!editor.sceneById.has(editor.primary)) editor.primary = [...editor.selected].pop() || null;
      if (document.activeElement!==nameInput) nameInput.value = editor.profile.name || '';
      previewImage.src = payload.image;
      drawOverlay();
      updateInspector();
      setStatus('草稿预览已更新；未保存，也未写入 USB。');
    } catch (error) {
      if (controller.signal.aborted || sequence!==editor.sequence) return;
      setStatus('预览失败：'+(error&&error.message?error.message:'本机服务暂不可用'));
    }
  }
  async function openEditor() {
    if (editor.open || editor.opening || uiBusy) return;
    editor.opening = true;
    try {
      if (!uiCatalogue) await update();
      const id = uiCatalogue && uiCatalogue.active_id;
      if (typeof id!=='string' || !idPattern.test(id)) throw Error('当前样式不可用');
      const response = await fetch('/api/ui/export?id='+encodeURIComponent(id),{cache:'no-store',credentials:'same-origin'});
      const profile = await response.json().catch(()=>null);
      if (!response.ok || !profile || typeof profile!=='object') throw Error('无法读取当前样式');
      const meta = (uiCatalogue.profiles||[]).find(item=>item.id===id);
      editor.open = true;
      editor.opening = false;
      editor.builtin = !!(meta&&meta.builtin);
      editor.original = clone(profile);
      editor.profile = clone(profile);
      editor.scene = [];
      editor.sceneById.clear();
      editor.selected.clear();
      editor.primary = null;
      editor.sequence++;
      uiBusy = true;
      section.hidden = false;
      nameInput.value = profile.name || '';
      await loadFonts();
      updateInspector();
      setStatus('载入布局元素…');
      stage.focus({preventScroll:true});
      queuePreview(true);
    } catch (error) {
      uiBusy = false;
      editor.opening = false;
      setStatus('无法打开编辑器：'+(error&&error.message?error.message:'读取失败'));
    }
  }
  function overrideFor(id) {
    if (!editor.profile.elements || typeof editor.profile.elements!=='object' || Array.isArray(editor.profile.elements)) {
      editor.profile.elements = {};
    }
    return editor.profile.elements[id] || {};
  }
  function writeOverride(id, values) {
    const entry = Object.assign({},overrideFor(id),values);
    if (entry.dx===0) delete entry.dx;
    if (entry.dy===0) delete entry.dy;
    if (Object.keys(entry).length) editor.profile.elements[id]=entry;
    else delete editor.profile.elements[id];
  }
  function moveRoots() {
    return [...editor.selected].filter(id=>!hasAncestorSelected(id));
  }
  function currentOffset(id,axis) {
    const limit=axis==='dx'?1920:480;
    const raw=Number(overrideFor(id)[axis]);
    if (!Number.isFinite(raw)) return 0;
    return Math.max(-limit,Math.min(limit,Math.round(raw)));
  }
  function clampSharedDelta(axis,requested,roots) {
    const limit = axis==='dx'?1920:480;
    if (!Number.isFinite(requested) || roots.length===0) return 0;
    requested=Math.round(requested);
    let minimum=-limit, maximum=limit;
    for (const id of roots) {
      const current = currentOffset(id,axis);
      minimum = Math.max(minimum,-limit-current);
      maximum = Math.min(maximum,limit-current);
    }
    return Math.round(Math.max(minimum,Math.min(maximum,requested)));
  }
  function shiftLocalScene(rootId,dx,dy) {
    if (!Number.isFinite(dx) || !Number.isFinite(dy)) return;
    for (const item of editor.scene) {
      if (item.id===rootId || isDescendant(item.id,rootId)) {
        const next=[item.bbox[0]+dx,item.bbox[1]+dy,item.bbox[2]+dx,item.bbox[3]+dy];
        if (next.every(Number.isFinite)) item.bbox=next;
      }
    }
  }
  function moveSelection(dx,dy,schedule=true) {
    if (!editor.open || editor.saving || !editor.selected.size) return false;
    const roots = moveRoots();
    dx = clampSharedDelta('dx',dx,roots);
    dy = clampSharedDelta('dy',dy,roots);
    if (!dx && !dy) return false;
    for (const id of roots) {
      const nextX=currentOffset(id,'dx')+dx;
      const nextY=currentOffset(id,'dy')+dy;
      writeOverride(id,{dx:nextX,dy:nextY});
      shiftLocalScene(id,dx,dy);
    }
    drawOverlay();
    updateInspector();
    if (schedule) queuePreview();
    return true;
  }
  function applyRootDeltas(deltas) {
    const plans=[];
    for (const move of deltas) {
      const requestedX=move.dx===undefined?0:move.dx;
      const requestedY=move.dy===undefined?0:move.dy;
      if (!editor.sceneById.has(move.id) || !Number.isFinite(requestedX) || !Number.isFinite(requestedY)) {
        setStatus('调整值无效；未应用本次操作。');
        return false;
      }
      const currentX=currentOffset(move.id,'dx');
      const currentY=currentOffset(move.id,'dy');
      const nextX=Math.round(currentX+requestedX);
      const nextY=Math.round(currentY+requestedY);
      if (!Number.isFinite(nextX) || !Number.isFinite(nextY) ||
          nextX < -1920 || nextX > 1920 || nextY < -480 || nextY > 480) {
        setStatus('调整超出允许范围；请稍微减少移动距离。');
        return false;
      }
      plans.push({id:move.id,nextX,nextY,dx:nextX-currentX,dy:nextY-currentY});
    }
    for (const move of plans) {
      writeOverride(move.id,{dx:move.nextX,dy:move.nextY});
      if (move.dx || move.dy) shiftLocalScene(move.id,move.dx,move.dy);
    }
    drawOverlay();
    updateInspector();
    queuePreview();
    return true;
  }
  function alignSelection(mode) {
    const roots=selectedMoveRoots();
    if (roots.length<2) return;
    const items=roots.map(id=>editor.sceneById.get(id)).filter(Boolean);
    if (items.length<2) return;
    const left=Math.min(...items.map(item=>item.bbox[0]));
    const top=Math.min(...items.map(item=>item.bbox[1]));
    const right=Math.max(...items.map(item=>item.bbox[2]));
    const bottom=Math.max(...items.map(item=>item.bbox[3]));
    const centerX=(left+right)/2, centerY=(top+bottom)/2;
    const deltas=items.map(item=>{
      const b=item.bbox;
      if (mode==='left') return {id:item.id,dx:left-b[0],dy:0};
      if (mode==='center-x') return {id:item.id,dx:centerX-(b[0]+b[2])/2,dy:0};
      if (mode==='right') return {id:item.id,dx:right-b[2],dy:0};
      if (mode==='top') return {id:item.id,dx:0,dy:top-b[1]};
      if (mode==='center-y') return {id:item.id,dx:0,dy:centerY-(b[1]+b[3])/2};
      return {id:item.id,dx:0,dy:bottom-b[3]};
    });
    applyRootDeltas(deltas);
  }
  function distributeSelection(axis) {
    const roots=selectedMoveRoots();
    if (roots.length<3) return;
    const horizontal=axis==='x';
    const items=roots.map(id=>editor.sceneById.get(id)).filter(Boolean).sort((a,b)=>{
      const primary=horizontal?0:1, secondary=horizontal?1:0;
      return a.bbox[primary]-b.bbox[primary] || a.bbox[secondary]-b.bbox[secondary] || a.id.localeCompare(b.id);
    });
    if (items.length<3) return;
    const start=items[0].bbox[horizontal?0:1];
    const end=items[items.length-1].bbox[horizontal?2:3];
    const sizes=items.map(item=>Math.max(0,item.bbox[horizontal?2:3]-item.bbox[horizontal?0:1]));
    const gap=(end-start-sizes.reduce((sum,size)=>sum+size,0))/(items.length-1);
    let cursor=start;
    const deltas=[];
    for (let index=0;index<items.length;index++) {
      const item=items[index];
      const current=item.bbox[horizontal?0:1];
      const delta=cursor-current;
      deltas.push({id:item.id,dx:horizontal?delta:0,dy:horizontal?0:delta});
      cursor+=sizes[index]+gap;
    }
    applyRootDeltas(deltas);
  }
  function textTargets() {
    return [...editor.selected].map(id=>editor.sceneById.get(id)).filter(item=>item&&item.kind==='text');
  }
  function setFontSize(value) {
    if (!Number.isInteger(value) || value<8 || value>96) return;
    const targets=textTargets();
    if (!targets.length) return;
    for (const item of targets) {
      writeOverride(item.id,{font_size:value});
      item.font_size=value;
    }
    updateInspector();
    queuePreview();
  }
  function removeOverride(id,key) {
    const entry=editor.profile&&editor.profile.elements&&editor.profile.elements[id];
    if (!entry) return;
    delete entry[key];
    if (!Object.keys(entry).length) delete editor.profile.elements[id];
  }
  function resetTextStyle(key) {
    for (const item of textTargets()) {
      removeOverride(item.id,key);
      const defaults={color:'default_color',font_family:'default_font_family',bold:'default_bold',italic:'default_italic'};
      const defaultKey=defaults[key];
      if (defaultKey) {
        if (item[defaultKey]===undefined || item[defaultKey]===null) delete item[key];
        else item[key]=item[defaultKey];
      }
    }
    updateInspector();
    queuePreview();
  }
  function setFontFamily(value) {
    if (value==='__mixed__') return;
    if (value && !editor.fonts.some(font=>font.id===value)) return;
    for (const item of textTargets()) {
      if (value) writeOverride(item.id,{font_family:value});
      else removeOverride(item.id,'font_family');
      item.font_family=value || item.default_font_family || '';
    }
    updateInspector();
    queuePreview();
  }
  function setTextColor(value) {
    if (typeof value!=='string' || !/^#[0-9a-fA-F]{6}$/.test(value)) return;
    for (const item of textTargets()) {
      writeOverride(item.id,{color:value.toUpperCase()});
      item.color=value.toUpperCase();
    }
    updateInspector();
    queuePreview();
  }
  function setTextBoolean(key,value) {
    if (typeof value!=='boolean' || !['bold','italic'].includes(key)) return;
    for (const item of textTargets()) {
      writeOverride(item.id,{[key]:value});
      item[key]=value;
    }
    updateInspector();
    queuePreview();
  }
  function resetSelected() {
    if (!editor.profile || !editor.profile.elements) return;
    for (const id of editor.selected) delete editor.profile.elements[id];
    queuePreview();
  }
  function undoDraft() {
    if (!editor.original) return;
    editor.profile=clone(editor.original);
    nameInput.value=editor.profile.name||'';
    queuePreview();
    setStatus('草稿已恢复为打开时的已保存副本。');
  }
  function uniqueProfileId() {
    const used=new Set((uiCatalogue&&uiCatalogue.profiles||[]).map(item=>item.id));
    const base='layout-'+Date.now().toString(36);
    let candidate=base, suffix=1;
    while (used.has(candidate)) candidate=base+'-'+(suffix++).toString(36);
    return candidate.slice(0,40);
  }
  function profileName(saveAs) {
    const entered=nameInput.value.trim();
    if (entered && [...entered].length>48) throw Error('样式名称最多 48 个字符');
    let result=entered || '自定义样式';
    if ((saveAs||editor.builtin) && result===editor.original.name) result += ' 副本';
    return result.slice(0,48);
  }
  async function persistDraft(saveAs) {
    if (!editor.open || !editor.profile || editor.saving) return;
    let profile=clone(editor.profile);
    try {
      profile.name=profileName(saveAs);
      if (saveAs || editor.builtin) profile.id=uniqueProfileId();
      else profile.id=editor.original.id;
    } catch (error) {
      setStatus(error.message||'样式名称无效');
      return;
    }
    const body=JSON.stringify({profile});
    if (new Blob([body]).size>65536) { setStatus('样式包超过 64 KiB'); return; }
    const previewWasPending=editor.previewPending;
    clearTimeout(editor.timer);
    if (editor.controller) editor.controller.abort();
    editor.sequence++;
    editor.previewPending=false;
    setSaving(true);
    setStatus(saveAs?'正在另存并应用…':'正在保存并应用…');
    try {
      const response=await fetch('/api/ui/import',{
        method:'POST',credentials:'same-origin',headers:apiHeaders(),body
      });
      const result=await response.json().catch(()=>({}));
      if (!response.ok) throw Error(result.error||'保存失败');
      if (!result.ui) throw Error('服务未返回样式目录');
      fillUi(result.ui);
      await update();
      setStatus(saveAs?'样式已另存并应用。':'样式已保存并应用。');
      closeEditor();
    } catch (error) {
      setSaving(false);
      if (previewWasPending) {
        editor.previewPending=true;
        updateInspector();
        queuePreview(true);
      }
      setStatus('保存失败：'+(error&&error.message?error.message:'本机服务暂不可用'));
    }
  }
  function exportDraft() {
    if (!editor.profile) return;
    const profile=clone(editor.profile);
    try { profile.name=profileName(false); } catch (error) { setStatus(error.message||'样式名称无效'); return; }
    if (editor.builtin) profile.id=uniqueProfileId();
    const blob=new Blob([JSON.stringify(profile,null,2)+'\n'],{type:'application/json'});
    const url=URL.createObjectURL(blob);
    const link=document.createElement('a');
    link.href=url;
    link.download='kv-agent-ui-'+profile.id+'.json';
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(()=>URL.revokeObjectURL(url),30000);
    setStatus('已导出样式 profile；未包含预览图或任务数据。');
  }

  function stagePoint(clientX,clientY) {
    const rect=stage.getBoundingClientRect();
    const width=stage.clientWidth, height=stage.clientHeight;
    if (!width || !height) return null;
    const left=rect.left+stage.clientLeft, top=rect.top+stage.clientTop;
    return {
      x:Math.max(0,Math.min(1920,(clientX-left)*1920/width)),
      y:Math.max(0,Math.min(480,(clientY-top)*480/height))
    };
  }
  function startPointerGesture(event) {
    if (pointerGesture || editor.saving || event.button!==0) return;
    const target=event.target instanceof Element?event.target.closest('[data-element-id]'):null;
    const id=target&&overlay.contains(target)?target.dataset.elementId:null;
    if (!id) return;
    const point=stagePoint(event.clientX,event.clientY);
    if (!point) return;
    pointerGesture={
      pointerId:event.pointerId,id,multi:event.ctrlKey||event.metaKey,
      clientX:event.clientX,clientY:event.clientY,startX:point.x,startY:point.y,
      dragging:false,moved:false,canDrag:!editor.previewPending
    };
    try { stage.setPointerCapture(event.pointerId); } catch (_) {}
    event.preventDefault();
  }
  function beginPointerDrag(gesture) {
    if (!gesture.canDrag || !gesture.id || gesture.dragging) return false;
    const wasSelected=editor.selected.has(gesture.id);
    if (!wasSelected) setSelection(gesture.id,gesture.multi);
    if (!editor.selected.size || !editor.primary) return false;
    const primary=editor.sceneById.get(editor.primary);
    if (!primary) return false;
    gesture.primaryId=editor.primary;
    gesture.primaryX=primary.bbox[0];
    gesture.primaryY=primary.bbox[1];
    gesture.dragging=true;
    stage.classList.add('is-dragging');
    stage.focus({preventScroll:true});
    return true;
  }
  function updatePointerGesture(event) {
    const gesture=pointerGesture;
    if (!gesture || gesture.pointerId!==event.pointerId) return;
    const distance=Math.hypot(event.clientX-gesture.clientX,event.clientY-gesture.clientY);
    if (!gesture.dragging && distance<=3) return;
    if (!gesture.dragging && !beginPointerDrag(gesture)) return;
    const point=stagePoint(event.clientX,event.clientY);
    const primary=editor.sceneById.get(gesture.primaryId);
    if (!point || !primary) return;
    const totalX=Math.round(point.x-gesture.startX);
    const totalY=Math.round(point.y-gesture.startY);
    const targetX=gesture.primaryX+totalX;
    const targetY=gesture.primaryY+totalY;
    const dx=Math.round(targetX-primary.bbox[0]);
    const dy=Math.round(targetY-primary.bbox[1]);
    if (moveSelection(dx,dy,false)) gesture.moved=true;
  }
  function suppressFollowingClick() {
    suppressClick=true;
    clearTimeout(suppressClickTimer);
    suppressClickTimer=setTimeout(()=>{suppressClick=false;},500);
  }
  function finishPointerGesture(event,canceled=false) {
    const gesture=pointerGesture;
    if (!gesture || gesture.pointerId!==event.pointerId) return;
    if (!canceled) updatePointerGesture(event);
    pointerGesture=null;
    stage.classList.remove('is-dragging');
    if (gesture.dragging) {
      if (gesture.moved) queuePreview();
      suppressFollowingClick();
    } else if (!canceled && gesture.id) {
      setSelection(gesture.id,gesture.multi);
    }
    try {
      if (stage.hasPointerCapture(event.pointerId)) stage.releasePointerCapture(event.pointerId);
    } catch (_) {}
  }

  byId('layout-editor-open').addEventListener('click',openEditor);
  byId('layout-editor-open-control').addEventListener('click',openEditor);
  const mainFrame=byId('frame');
  mainFrame.addEventListener('click',openEditor);
  mainFrame.addEventListener('keydown',event=>{
    if (event.key==='Enter' || event.key===' ') { event.preventDefault(); openEditor(); }
  });
  stage.addEventListener('click',event=>{
    if (!suppressClick) return;
    suppressClick=false;
    clearTimeout(suppressClickTimer);
    event.preventDefault();
    event.stopPropagation();
  },true);
  stage.addEventListener('pointerdown',startPointerGesture);
  stage.addEventListener('pointermove',updatePointerGesture);
  stage.addEventListener('pointerup',event=>finishPointerGesture(event,false));
  stage.addEventListener('pointercancel',event=>finishPointerGesture(event,true));
  overlay.addEventListener('click',event=>{
    if(event.target===overlay){stage.focus({preventScroll:true});clearSelection();}
  });
  elementSelect.addEventListener('change',()=>{
    if (elementSelect.value) setSelection(elementSelect.value,false);
    else clearSelection();
  });
  xInput.addEventListener('change',()=>{
    const item=editor.sceneById.get(editor.primary);
    const target=Number(xInput.value);
    if (item && Number.isFinite(target)) moveSelection(Math.round(target-item.bbox[0]),0);
  });
  yInput.addEventListener('change',()=>{
    const item=editor.sceneById.get(editor.primary);
    const target=Number(yInput.value);
    if (item && Number.isFinite(target)) moveSelection(0,Math.round(target-item.bbox[1]));
  });
  fontInput.addEventListener('change',()=>{
    if (fontInput.value==='') return;
    setFontSize(Math.round(Number(fontInput.value)));
  });
  stage.addEventListener('keydown',event=>{
    if (!editor.open || editor.saving || event.defaultPrevented || event.isComposing || event.keyCode===229 ||
        event.ctrlKey || event.metaKey || event.altKey || !editor.selected.size) return;
    const target=event.target;
    if (target instanceof Element && target.closest('input,textarea,select,button,[contenteditable="true"]')) return;
    const step=event.shiftKey?10:1;
    const moves={ArrowLeft:[-step,0],ArrowRight:[step,0],ArrowUp:[0,-step],ArrowDown:[0,step]};
    const delta=moves[event.key];
    if (!delta) return;
    event.preventDefault();
    moveSelection(delta[0],delta[1]);
  });
  byId('layout-editor-reset-selected').addEventListener('click',resetSelected);
  byId('layout-editor-undo').addEventListener('click',undoDraft);
  byId('layout-editor-cancel').addEventListener('click',closeEditor);
  byId('layout-editor-save-as').addEventListener('click',()=>persistDraft(true));
  byId('layout-editor-apply').addEventListener('click',()=>persistDraft(false));
  byId('layout-editor-export').addEventListener('click',exportDraft);
  fontFamilyInput.addEventListener('change',()=>setFontFamily(fontFamilyInput.value));
  colorInput.addEventListener('change',()=>setTextColor(colorInput.value));
  boldInput.addEventListener('change',()=>setTextBoolean('bold',boldInput.checked));
  italicInput.addEventListener('change',()=>setTextBoolean('italic',italicInput.checked));
  byId('layout-editor-color-default').addEventListener('click',()=>resetTextStyle('color'));
  byId('layout-editor-bold-default').addEventListener('click',()=>resetTextStyle('bold'));
  byId('layout-editor-italic-default').addEventListener('click',()=>resetTextStyle('italic'));
  byId('layout-editor-align-left').addEventListener('click',()=>alignSelection('left'));
  byId('layout-editor-align-center-x').addEventListener('click',()=>alignSelection('center-x'));
  byId('layout-editor-align-right').addEventListener('click',()=>alignSelection('right'));
  byId('layout-editor-align-top').addEventListener('click',()=>alignSelection('top'));
  byId('layout-editor-align-center-y').addEventListener('click',()=>alignSelection('center-y'));
  byId('layout-editor-align-bottom').addEventListener('click',()=>alignSelection('bottom'));
  byId('layout-editor-distribute-x').addEventListener('click',()=>distributeSelection('x'));
  byId('layout-editor-distribute-y').addEventListener('click',()=>distributeSelection('y'));
})();
'''
