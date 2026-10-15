"""Local control page. Session data is never interpolated into executable HTML."""

PAGE = r'''<!doctype html>
<html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>KV Agent Desk · Codex · ZCode · Antigravity</title>
<style>
:root{color-scheme:dark;font-family:"Segoe UI","Microsoft YaHei",sans-serif;background:#090f16;color:#eff4f9}
*{box-sizing:border-box}body{margin:0;padding:40px 4vw;max-width:1800px;margin:auto}
header{display:flex;align-items:center;justify-content:space-between;margin-bottom:28px;gap:20px}
h1{font-size:27px;letter-spacing:-.6px;margin:0}h1 span{font-size:14px;font-weight:400;color:#8fa3b7;margin-left:14px}
.label{font-family:Consolas,monospace;font-size:12px;letter-spacing:2px;color:#78e7c2;margin-bottom:8px}
.preview{border:1px solid #253748;border-radius:16px;overflow:hidden;box-shadow:0 24px 80px #0005;background:#090f16}
#frame{display:block;width:100%;height:auto;aspect-ratio:4/1}
.caption{display:flex;justify-content:space-between;color:#849bb1;font-size:13px;padding:15px 2px;margin-bottom:22px;gap:15px}
.controls{display:flex;gap:10px;align-items:center;flex-wrap:wrap}button{border:1px solid #324756;background:#15212e;color:#dce8f2;font:inherit;font-size:13px;padding:11px 16px;border-radius:8px;cursor:pointer}button:hover{border-color:#78e7c2}button:disabled{opacity:.5;cursor:wait}button.danger{color:#b8a19d;border-color:#413b3d}
.cards{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:18px;margin-top:22px}.card{background:#101a25;border:1px solid #243344;border-radius:12px;padding:22px;min-width:0}
h2{margin:0 0 14px;font-size:17px}.card p{color:#91a4b7;font-size:13px;line-height:1.8;margin:7px 0;overflow-wrap:anywhere}.status{color:#78e7c2}.note{font-size:13px;color:#7b8da0;margin-top:25px;line-height:1.9}.detail{font-family:Consolas,"Microsoft YaHei",sans-serif;font-size:12px!important;opacity:.85}a{color:#9cb5c9}.pill{border:1px solid #304456;border-radius:20px;padding:7px 14px;font-size:12px;color:#99adbd}
#feedback{font-size:13px;color:#aabbd0;margin-left:8px}.stale{color:#a8a28b!important}
.device-settings{display:flex;align-items:center;gap:22px;flex-wrap:wrap;padding:18px 20px;border:1px solid #243344;background:#101a25;border-radius:12px;margin-top:18px;font-size:13px;color:#a4b6c7}.device-settings label{display:flex;align-items:center;gap:10px}.device-settings input[type=range]{width:210px;accent-color:#78e7c2}.device-settings select{background:#13202e;color:#e8f0f8;border:1px solid #304456;padding:6px;border-radius:5px}.device-settings output{min-width:40px;font-family:Consolas,monospace;color:#e8f0f8}
.plans{display:grid;grid-template-columns:1fr 1fr;gap:18px;margin-top:18px}.plan-form label{display:block;font-size:13px;color:#9db0c1;margin-top:14px}.plan-form input,.plan-form select{display:block;width:100%;margin-top:7px;background:#0b141e;border:1px solid #304456;border-radius:7px;padding:10px 12px;color:#eff4f9;font:inherit;font-size:14px}.plan-form input:focus{outline:1px solid #78e7c2}.plan-form .controls{margin-top:18px}.plan-status{min-height:24px;color:#a4b6c7;font-size:12px;margin-top:12px}.plan-help{margin:25px 0 0;font-size:13px;color:#8fa3b7;line-height:1.8}
@media(max-width:800px){body{padding:24px 15px}header{align-items:flex-start}h1 span{display:block;margin:8px 0 0}.cards{grid-template-columns:1fr}.caption{font-size:11px}.pill{display:none}}
@media(max-width:800px){.plans{grid-template-columns:1fr}}
</style>
<header><div><div class="label">LOCAL AGENT TELEMETRY</div><h1>KV Agent Desk <span>Codex · ZCode · Antigravity · Windows</span></h1></div><span class="pill">本机采集 / 官方额度</span></header>
<div class="preview"><img id="frame" src="/frame.png" alt="KV 9.16 仪表盘实时画面"></div>
<div class="caption"><span>屏幕与预览使用同一张画面 · 1920 × 480 设计画布</span><span id="refresh">正在连接本机服务</span></div>
<div class="controls"><button id="usb">连接 USB 屏幕</button><button id="fullscreen">全屏预览</button><button class="danger" id="stop">退出应用</button><span id="feedback" role="status" aria-live="polite"></span></div>
<div class="device-settings"><label for="brightness">屏幕画面亮度（软件调光）<input id="brightness" type="range" min="0" max="100" step="1" value="100"><output id="brightness-value">100%</output></label><label for="rotation">屏幕方向<select id="rotation"><option value="180">旋转180°</option><option value="0">正常方向</option></select></label><label><input id="auto-start" type="checkbox">开机自启并恢复上次画面</label><span id="startup-status"></span><label><input id="codex-no-five-hour-limit" type="checkbox">ChatGPT 无5小时限额（手动设为100%）</label></div>
<div class="cards"><section class="card"><h2>Codex / ChatGPT 订阅</h2><p id="codex-state"></p><p id="codex-fresh"></p><p id="codex-quota"></p><p class="detail" id="codex-source"></p><p class="detail" id="codex-detail"></p></section>
<section class="card"><h2>ZCode</h2><p id="zcode-state"></p><p id="zcode-fresh"></p><p id="zcode-quota"></p><p class="detail" id="zcode-source"></p><p class="detail" id="zcode-detail"></p></section>
<section class="card"><h2>Antigravity</h2><p id="antigravity-state"></p><p id="antigravity-fresh"></p><p id="antigravity-quota"></p><p class="detail" id="antigravity-source"></p><p class="detail" id="antigravity-detail"></p></section>
<section class="card"><h2>KV 9.16 LCD</h2><p id="lcd-state"></p><p class="detail" id="lcd-detail"></p><p>屏幕由一个程序独占输出。使用利民官方软件前，请先暂停本仪表盘的 USB 输出。</p></section></div>
<div class="plans">
<section class="card"><h2>课题组 DeepSeek</h2><p id="deepseek_lab-value"></p><p class="detail" id="deepseek_lab-detail"></p><p>通过 DeepSeek 官方余额接口只读更新。</p></section>
<section class="card"><h2>个人 DeepSeek</h2><p id="deepseek_personal-value"></p><p class="detail" id="deepseek_personal-detail"></p><p>使用个人账户的独立环境变量读取。</p></section></div>
<p class="plan-help">套餐规格与到期时间：优先使用自动信息。无法获取时可在下方补充；手动填写优先显示并保存在本机，留空或清除即可恢复自动信息。套餐到期日期与额度重置时间分别显示。</p>
<div class="plans">
<form class="card plan-form" id="codex-subscription"><h2>ChatGPT 套餐信息</h2>
<label for="codex-plan-name">套餐名称 / 等级<input id="codex-plan-name" maxlength="80" placeholder="例如：ChatGPT Pro"></label>
<label for="codex-plan-spec">套餐规格<input id="codex-plan-spec" maxlength="160" placeholder="例如：月付 / 年付、额度档位"></label>
<label for="codex-expires">订阅到期日期<input id="codex-expires" type="date"></label>
<div class="controls"><button type="submit">保存套餐信息</button><button type="button" id="codex-clear">清除手动填写</button></div>
<div class="plan-status" id="codex-plan-status" role="status" aria-live="polite"></div><p class="detail" id="codex-plan-effective"></p></form>
<form class="card plan-form" id="zcode-subscription"><h2>ZCode 套餐信息</h2>
<label for="zcode-plan-name">套餐名称 / 等级<input id="zcode-plan-name" maxlength="80" placeholder="例如：GLM Coding Plan Pro / Max"></label>
<label for="zcode-plan-spec">套餐规格<input id="zcode-plan-spec" maxlength="160" placeholder="例如：月付 / 年付、额度档位"></label>
<label for="zcode-expires">订阅到期日期<input id="zcode-expires" type="date"></label>
<div class="controls"><button type="submit">保存套餐信息</button><button type="button" id="zcode-clear">清除手动填写</button></div>
<div class="plan-status" id="zcode-plan-status" role="status" aria-live="polite"></div><p class="detail" id="zcode-plan-effective"></p></form>
<form class="card plan-form" id="antigravity-subscription"><h2>Antigravity 套餐信息</h2>
<label for="antigravity-plan-name">套餐名称 / 等级<input id="antigravity-plan-name" maxlength="80" placeholder="可补充实际订阅名称"></label>
<label for="antigravity-plan-spec">套餐规格<input id="antigravity-plan-spec" maxlength="160" placeholder="填写实际套餐规格"></label>
<label for="antigravity-expires">订阅到期日期<input id="antigravity-expires" type="date"></label>
<div class="controls"><button type="submit">保存套餐信息</button><button type="button" id="antigravity-clear">清除手动填写</button></div>
<div class="plan-status" id="antigravity-plan-status" role="status" aria-live="polite"></div><p class="detail" id="antigravity-plan-effective"></p></form></div>
<p class="note">应用独立运行。关闭原生窗口会收进系统托盘；需要彻底退出时，使用托盘菜单或“退出应用”。开机先恢复上次画面，再逐个更新数据。亮度采用软件画面调光。缓存 Token 是输入量的子集；数据缺失或过期时保留最后可信记录，跨天的旧用量保留原日期。</p>
<script>
const token=__TOKEN__;
let enabled=false,busy=false,stopped=false;
let brightnessTimer=null;
const dirtyProfiles=new Set();
function text(id,value){document.getElementById(id).textContent=value||''}
function fillProfiles(s){for(const provider of ['codex','zcode','antigravity']){const p=(s.subscription_profiles||{})[provider]||{};const a=s.agents.find(x=>x.provider===provider)||{};const q=a.subscription||{};if(!dirtyProfiles.has(provider)){for(const [field,id]of [['plan_name','plan-name'],['plan_spec','plan-spec'],['expires_at','expires']]){const input=document.getElementById(provider+'-'+id);if(document.activeElement!==input)input.value=(p[field]||'').slice(0,field==='expires_at'?10:1000)}}const src=q.metadata_sources||{};const labels={manual:'手动填写',automatic:'自动获取',cached:'最近可信记录',unknown:'待填写'};const name=q.plan_name?`${q.plan_name}（${labels[src.plan_name]||'自动'}）`:'套餐待填写';const spec=q.plan_spec?' · '+q.plan_spec:'';const expiry=q.expires_at?' · 到期 '+q.expires_at.replace('T',' ').replace(/\+08:00$/,'')+'（'+(labels[src.expires_at]||'自动')+'）':' · 到期日期待填写';text(provider+'-plan-effective','当前显示：'+name+spec+expiry)}}
function fillAccounts(s){for(const a of s.accounts||[]){let value='待更新';if(a.known)value=a.value+' '+(a.currency||'');text(a.id+'-value',value);text(a.id+'-detail',(a.detail||'')+(a.stale?' · 数据待检查更新':''))}}
function fillSettings(s){const p=s.settings||{};const slider=document.getElementById('brightness');if(document.activeElement!==slider){slider.value=p.brightness??100;text('brightness-value',slider.value+'%')}document.getElementById('rotation').value=String(p.rotation??180);document.getElementById('auto-start').checked=!!p.auto_start;document.getElementById('codex-no-five-hour-limit').checked=!!p.codex_no_five_hour_limit;text('startup-status',s.auto_start_registered?'已登记 Windows 当前用户自启':'可在正式应用中启用自启')}
async function update(){if(stopped)return;try{const r=await fetch('/api/state',{cache:'no-store'});if(!r.ok)throw Error(r.status);const s=await r.json();enabled=s.usb_enabled;document.getElementById('usb').textContent=enabled?'暂停 USB 输出':'连接 USB 屏幕';text('refresh','更新于 '+new Date(s.updated_at).toLocaleTimeString());
for(const a of s.agents){text(a.provider+'-state',a.state_label+' · '+(a.project||'等待本机会话'));text(a.provider+'-fresh',(a.stale?'数据待检查更新 · 最近记录 ':'最近记录 ')+(a.updated_at?new Date(a.updated_at).toLocaleString():'暂无'));document.getElementById(a.provider+'-fresh').className=a.stale?'stale':'';const q=a.subscription||{};function quota(w){return w&&w.known?(w.remaining_percent+'%'+(w.unlimited?'（无限额）':w.reset_at?'，重置 '+new Date(w.reset_at).toLocaleString():'')):'待更新'}text(a.provider+'-quota',a.provider==='antigravity'?(q.quota_windows||[]).map(w=>(w.label||w.model||'模型额度')+' '+quota(w)).join(' / ')||'模型额度待更新':'5小时 '+quota(q.five_hour)+' / 周额度 '+quota(q.weekly));text(a.provider+'-source',a.source);text(a.provider+'-detail',(a.detail||'')+(q.detail?' · '+q.detail:''))}fillProfiles(s);fillAccounts(s);fillSettings(s);
text('lcd-state',s.display.connected?'已连接 · '+s.display.width+' × '+s.display.height:(s.usb_enabled?'等待连接':'USB 输出已暂停'));text('lcd-detail',s.display.detail);document.getElementById('frame').src='/frame.png?t='+Date.now();}catch(e){text('feedback','本机服务未更新，保留当前画面。数据待检查更新。')}}
async function command(path,data){if(busy)return;busy=true;const b=document.getElementById('usb');b.disabled=true;try{const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json','X-Dashboard-Token':token},body:JSON.stringify(data||{})});if(!r.ok)throw Error(await r.text());text('feedback',path.endsWith('stop')?'正在停止后台服务并释放 USB 设备…':'设置已更新');if(path.endsWith('stop'))stopped=true;else await update()}catch(e){text('feedback','操作失败：'+e.message)}finally{busy=false;b.disabled=false}}
document.getElementById('usb').onclick=()=>command('/api/display',{enabled:!enabled});document.getElementById('stop').onclick=()=>command('/api/stop');document.getElementById('fullscreen').onclick=()=>{const p=document.querySelector('.preview');if(document.fullscreenElement)document.exitFullscreen();else p.requestFullscreen()};
async function saveSettings(fields){try{const r=await fetch('/api/settings',{method:'POST',headers:{'Content-Type':'application/json','X-Dashboard-Token':token},body:JSON.stringify(fields)});const result=await r.json();if(!r.ok)throw Error(result.error||'设置保存失败');text('feedback','设置已保存');await update()}catch(e){text('feedback',e.message)}}
document.getElementById('brightness').oninput=e=>{text('brightness-value',e.target.value+'%');clearTimeout(brightnessTimer);const value=Number(e.target.value);brightnessTimer=setTimeout(()=>saveSettings({brightness:value}),180)};document.getElementById('rotation').onchange=e=>saveSettings({rotation:Number(e.target.value)});document.getElementById('auto-start').onchange=e=>saveSettings({auto_start:e.target.checked});document.getElementById('codex-no-five-hour-limit').onchange=e=>saveSettings({codex_no_five_hour_limit:e.target.checked});
async function saveProfile(provider,clear=false){const form=document.getElementById(provider+'-subscription');const buttons=[...form.querySelectorAll('button')];buttons.forEach(b=>b.disabled=true);try{const profile=clear?{}:{plan_name:document.getElementById(provider+'-plan-name').value,plan_spec:document.getElementById(provider+'-plan-spec').value,expires_at:document.getElementById(provider+'-expires').value};const r=await fetch('/api/subscriptions',{method:'POST',headers:{'Content-Type':'application/json','X-Dashboard-Token':token},body:JSON.stringify({provider,profile})});const result=await r.json();if(!r.ok)throw Error(result.error||'保存失败');dirtyProfiles.delete(provider);text(provider+'-plan-status',clear?'手动填写已清除，恢复自动信息':'已保存，屏幕稍后同步更新');await update()}catch(e){text(provider+'-plan-status',e.message)}finally{buttons.forEach(b=>b.disabled=false)}}
for(const provider of ['codex','zcode','antigravity']){document.getElementById(provider+'-subscription').onsubmit=e=>{e.preventDefault();saveProfile(provider)};document.getElementById(provider+'-clear').onclick=()=>saveProfile(provider,true);for(const id of ['plan-name','plan-spec','expires'])document.getElementById(provider+'-'+id).oninput=()=>dirtyProfiles.add(provider)}
update();setInterval(update,2000);
</script></html>'''
