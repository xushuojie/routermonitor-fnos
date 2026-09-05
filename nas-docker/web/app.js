'use strict';
const $ = id => document.getElementById(id);
const text = (id, value) => { $(id).textContent = value ?? '—'; };
const state = {csrf: null, page: 'overview', running: false, saved: null, draft: null, interfaces: [], overview: null, caps: null, previewValid: false, previewSerial: 0, generation: 0, saving: false, points: [], seq: 0, epoch: '', tableKey: ''};
const finite = n => typeof n === 'number' && Number.isFinite(n) && n >= 0;
function amount(value, speed = false) {
  if (!finite(value)) return ['—', speed ? 'B/s' : ''];
  const units = ['B', 'KB', 'MB', 'GB', 'TB', 'PB']; let index = 0;
  while (value >= 1000 && index < units.length - 1) { value /= 1000; index++; }
  return [value.toLocaleString('en-US', {maximumFractionDigits: value >= 100 ? 0 : value >= 10 ? 1 : 2}), units[index] + (speed ? '/s' : '')];
}
function format(value, speed = false) { return amount(value, speed).join(' ').trim(); }
function metric(id, value, unit, digits = 0) {
  const element = $(id); element.replaceChildren(document.createTextNode(finite(value) ? value.toLocaleString('en-US', {maximumFractionDigits: digits}) : '—'));
  if (unit) { const small = document.createElement('small'); small.textContent = unit; element.append(small); }
}
function transfer(id, value, speed = true) { const [number, unit] = amount(value, speed); $(id).replaceChildren(document.createTextNode(number)); const small = document.createElement('small'); small.textContent = unit; $(id).append(small); }
function duration(seconds) { if (!finite(seconds)) return '—'; if(seconds<60)return Math.floor(seconds)+'秒'; const days=Math.floor(seconds/86400),hours=Math.floor(seconds/3600)%24,minutes=Math.floor(seconds/60)%60; return (days?days+'天 ':'')+(hours?hours+'小时 ':'')+minutes+'分'; }
function toast(message, error = false) { text('toast', message); $('toast').classList.toggle('error', error); $('toast').hidden = false; clearTimeout(toast.timer); toast.timer = setTimeout(() => $('toast').hidden = true, 5500); }
async function api(path, method = 'GET', body) {
  const headers = {}; if (body !== undefined) headers['Content-Type'] = 'application/json'; if (state.csrf) headers['X-CSRF-Token'] = state.csrf;
  const generation=state.generation;
  const response = await fetch(path, {method, headers, credentials: 'same-origin', body: body === undefined ? undefined : JSON.stringify(body), signal: AbortSignal.timeout(6000)});
  const value = await response.json();
  if (!response.ok) { if (response.status === 401 && path !== '/api/login' && generation===state.generation) showLogin(); const error = new Error(value.error || '请求失败'); error.status = response.status; throw error; }
  return value;
}
function showLogin() { state.generation++; clearTimeout(state.refreshTimer); clearTimeout(state.streamTimer); state.running = false; state.csrf = null; $('app').hidden = true; $('login').hidden = false; $('device-token').value = ''; $('device-token').hidden = true; }
async function start() {
  if (state.running) return;
  $('login').hidden = true; $('app').hidden = false; state.running = true; const generation=++state.generation;
  state.points = []; state.seq = 0; state.epoch = '';
  try { await loadInterfaces(true); } catch (error) { toast(error.message, true); }
  navigate(location.hash.slice(1) || 'overview'); refresh(generation); stream(generation);
}
function navigate(page) {
  if (!['overview','network','settings'].includes(page)) page = 'overview'; state.page = page;
  for (const p of ['overview','network','settings']) $('page-' + p).hidden = p !== page;
  document.querySelectorAll('[data-page]').forEach(button => { if (button.dataset.page === page) button.setAttribute('aria-current','page'); else button.removeAttribute('aria-current'); });
  text('breadcrumb', '控制台 / ' + {overview:'数据概览',network:'网络数据源',settings:'设备与设置'}[page]);
  if (location.hash !== '#' + page) history.replaceState(null, '', '#' + page);
  if (page === 'network') renderInterfaces();
  if (page === 'settings') refreshSettings().catch(error => toast(error.message, true));
  if (page === 'overview') drawChart();
}
function connected(ok) { text('connection', ok ? '实时连接' : '连接中断'); $('connection').className = 'badge ' + (ok ? 'online' : 'offline'); }
async function refresh(generation) {
  if (!state.running || generation!==state.generation) return;
  try {
    const overview = await api('/api/overview'); if(generation!==state.generation)return; state.overview=overview; connected(state.overview.available); renderOverview();
    if (state.page === 'network') await loadInterfaces(false);
    if (state.page === 'settings') await refreshSettings();
  } catch (error) { connected(false); $('offline-note').hidden = false; clearOverview(); }
  if (state.running && generation===state.generation) state.refreshTimer=setTimeout(()=>refresh(generation), 1000);
}
async function stream(generation) {
  if (!state.running || generation!==state.generation) return;
  const started = performance.now();
  if (state.page === 'overview' && !document.hidden) {
    try {
      const data = await api(`/api/network/stream?since=${state.seq}&epoch=${encodeURIComponent(state.epoch)}`);
      if(generation!==state.generation)return;
      if (data.epoch !== state.epoch || data.gap) state.points = [];
      for (const point of data.points) if (point[0] > state.seq || data.epoch !== state.epoch) state.points.push(point);
      state.epoch = data.epoch; state.seq = data.seq;
      const last = state.points.at(-1)?.[1]; state.points = state.points.filter(point => point[1] >= last - 30).slice(-180); drawChart();
    } catch (_) { state.points = []; drawChart(); }
  }
  if (state.running && generation===state.generation) state.streamTimer=setTimeout(()=>stream(generation), Math.max(30, 200 - (performance.now() - started)));
}
function drawChart() {
  const canvas = $('network-chart'), w = canvas.clientWidth, h = canvas.clientHeight; if (!w || !h) return;
  const ratio = window.devicePixelRatio || 1; canvas.width = w * ratio; canvas.height = h * ratio;
  const c = canvas.getContext('2d'); c.scale(ratio, ratio); const top = 16, bottom = h - 8, plotWidth = Math.max(1, w - 58);
  const points = state.points; let maximum = 1000; for (const point of points) maximum = Math.max(maximum, point[2] || 0, point[3] || 0); maximum *= 1.15;
  c.font = '9px system-ui'; c.textAlign = 'right'; c.lineWidth = 1;
  for (let row = 0; row < 3; row++) { const y = top + (bottom - top) * row / 2; c.strokeStyle = '#293947'; c.beginPath(); c.moveTo(0,y); c.lineTo(plotWidth,y); c.stroke(); c.fillStyle='#8098aa'; c.fillText(format(maximum * (1-row/2), true), w, y - 5); }
  if (points.length < 2) return;
  const end = points.at(-1)[1];
  for (const [column,color] of [[2,'#66ccff'],[3,'#ff8795']]) {
    c.strokeStyle=color; c.lineWidth=2; c.lineJoin='round'; c.beginPath(); let active=false, previousTime=0;
    for (const point of points) { if (!finite(point[column])) { active=false; continue; } const x = (point[1] - end + 30) / 30 * plotWidth, y = bottom - point[column] / maximum * (bottom - top); if (!active || point[1]-previousTime>.6) c.moveTo(x,y); else c.lineTo(x,y); active=true; previousTime=point[1]; }
    c.stroke();
  }
}
function clearOverview() {
  for (const id of ['network-tx','network-rx','disk-read','disk-write']) transfer(id,null);
  for (const [id,unit] of [['power','W'],['cpu','%'],['gpu','%'],['memory','%']]) { metric(id,null,unit); $(id+'-bar').value=0; }
  for (const id of ['traffic-tx','traffic-rx','storage-used','storage-total','cpu-temp','disk-temp','uptime']) text(id,'—'); $('storage-bar').value=0;
}
function renderOverview() {
  const d = state.overview; $('offline-note').hidden = !!d.available; if (!d.available) { clearOverview(); return; }
  transfer('network-tx',d.net?.tx_speed); transfer('network-rx',d.net?.rx_speed);
  transfer('disk-read',d.disk_io.valid ? d.disk_io.read_speed : null); transfer('disk-write',d.disk_io.valid ? d.disk_io.write_speed : null);
  text('source-badge', d.sources.network.join(' + ') || '未选择接口'); $('source-badge').title=d.sources.network.join(' + '); text('disk-source',d.disk_io.devices || '未检测到物理磁盘');
  for (const [id,value,unit] of [['power',d.ups.watts,'W'],['cpu',d.cpu.percent,'%'],['gpu',d.gpu.utilization,'%'],['memory',d.memory.percent,'%']]) { metric(id,value,unit,1); $(id+'-bar').value=finite(value)?value:0; }
  text('power-source', d.ups.valid ? (d.ups.source === 'dc_voltage_current' ? '直流电压 × 电流 · 计算值' : 'UPS 有功功率') + ' · 0–35W' : 'UPS 功率不可用');
  text('gpu-source',d.gpu.backend === 'unavailable' ? '当前硬件无可用采集方式' : d.gpu.backend === 'i915' ? 'Intel i915 · 渲染引擎' : d.gpu.backend);
  text('memory-detail',d.memory.valid?`${format(d.memory.used)} / ${format(d.memory.total)}`:'内存数据不可用');
  text('traffic-tx',d.traffic_24h.valid?format(d.traffic_24h.tx_bytes):'—'); text('traffic-rx',d.traffic_24h.valid?format(d.traffic_24h.rx_bytes):'—');
  text('traffic-coverage',(d.traffic_24h.basis === 'legacy_group' ? '原组合历史 · 已覆盖 ' : '成员共同覆盖 ') + duration(d.traffic_24h.coverage_seconds));
  text('storage-used',d.storage.valid?format(d.storage.used):'—'); text('storage-total',d.storage.valid?' / '+format(d.storage.total):' / —'); $('storage-bar').value=d.storage.valid?d.storage.percent:0;
  text('storage-detail',d.storage.valid?`${d.storage.percent}% 已用 · ${d.storage.filesystems} 个文件系统（已去重）`:(d.storage.reason || '未读取到已挂载的数据卷'));
  text('cpu-temp',finite(d.temperature_summary.cpu)?d.temperature_summary.cpu+' °C':'不可用'); text('disk-temp',finite(d.temperature_summary.disk)?d.temperature_summary.disk+' °C':'不可用'); text('uptime',duration(d.uptime));
  text('overview-age',`状态更新于 ${d.age.toFixed(1)} 秒前`);
  const rows=[['容量发现',d.storage.mode === 'auto' ? '自动 · 每 30 秒重新扫描' : '手动覆盖'],...((d.storage.volumes||[]).map(v=>[v.path, v.valid ? `${v.filesystem} · ${format(v.used)} / ${format(v.total)}${v.included?'':' · 已去重'}` : v.reason])),['CPU / 内存',d.sources.cpu+' / '+d.sources.memory],['GPU',d.sources.gpu === 'unavailable'?'未支持当前 GPU 采集方式':d.sources.gpu],['数据卷',d.sources.storage_paths.join('、')],['UPS',`${d.ups.source} · ${d.ups.valid?'有效':'不可用'}${d.ups.reason?' · '+d.ups.reason:''}${d.ups.age_seconds!=null?' · '+d.ups.age_seconds+'秒前':''}`],...d.temp.map(sensor=>[sensor.type,`${sensor.temp} °C`])];
  renderList('source-details',rows);
}
function renderList(id, rows) { const fragment=document.createDocumentFragment(); for(const [name,value] of rows){const div=document.createElement('div'), label=document.createElement('span'), content=document.createElement('strong'); label.textContent=name;content.textContent=value??'—';div.append(label,content);fragment.append(div);} $(id).replaceChildren(fragment); }
function renderDl(id, rows) { const fragment=document.createDocumentFragment(); for(const [name,value] of rows){const dt=document.createElement('dt'),dd=document.createElement('dd');dt.textContent=name;dd.textContent=value??'—';fragment.append(dt,dd);} $(id).replaceChildren(fragment); }
function dirty() { if(!state.saved||!state.draft)return false; return JSON.stringify(state.saved)!==JSON.stringify(state.draft); }
async function loadInterfaces(reset) {
  const data=await api('/api/network/interfaces'); state.interfaces=data.interfaces;state.network=data;
  if(reset||!state.saved||(!dirty()&&data.settings.revision!==state.saved.revision)){state.saved=structuredClone(data.settings);state.draft=structuredClone(data.settings);state.previewValid=false;}
  $('namespace-note').hidden=data.host_network; text('namespace-note','当前容器未使用 host 网络，IP 和拓扑信息无法完整核对。请使用随项目提供的 Compose 部署模板。');
  text('interface-count',`${data.interfaces.filter(row=>row.physical).length} 个物理端口 / ${data.interfaces.length} 个接口`);
  renderInterfaces(); if(reset) await preview(); else updatePreviewRates();
}
const kinds={physical:'物理端口',wireless:'无线网卡',bridge:'网桥',bond:'链路聚合',team:'Team 聚合',vlan:'VLAN',veth:'容器 veth',tun:'隧道',wireguard:'WireGuard',openvswitch:'Open vSwitch',loopback:'Loopback',unknown:'未识别',missing:'接口已消失'};
function renderInterfaces() {
  if(!state.draft)return;
  const selected=new Set(state.draft.members.map(m=>m.id)), filter=$('filter').value.toLowerCase();
  const rows=state.interfaces.filter(row=>($('show-virtual').checked||row.physical||selected.has(row.id)) && [row.name,(row.addresses||[]).join(' '),state.draft.aliases[row.id]||''].join(' ').toLowerCase().includes(filter));
  const key=rows.map(row=>row.id).join(',');
  if(key!==state.tableKey){state.tableKey=key;const fragment=document.createDocumentFragment(); for(const row of rows){const tr=document.createElement('tr');tr.dataset.id=row.id; for(let i=0;i<7;i++)tr.append(document.createElement('td'));
    const check=document.createElement('input');check.type='checkbox';check.dataset.select=row.id;check.setAttribute('aria-label','选择 '+row.name);tr.children[0].append(check);
    const name=document.createElement('strong'),alias=document.createElement('input');name.textContent=row.name;alias.className='alias';alias.dataset.alias=row.id;alias.maxLength=40;alias.placeholder='添加别名';alias.setAttribute('aria-label','接口别名 '+row.name);tr.children[1].append(name,alias);
    for(const i of [2,3]){tr.children[i].append(document.createElement('span'));const sub=document.createElement('span');sub.className='sub';tr.children[i].append(sub);}
    tr.children[4].className='numeric upload-text';tr.children[5].className='numeric download-text';const detail=document.createElement('button');detail.dataset.detail=row.id;detail.textContent='详情 ↗';tr.children[6].append(detail);fragment.append(tr);
  } $('interfaces').replaceChildren(fragment);}
  for(const row of rows){const tr=$('interfaces').querySelector(`[data-id="${row.id}"]`);tr.classList.toggle('selected',selected.has(row.id));tr.children[0].firstChild.checked=selected.has(row.id);const alias=tr.children[1].lastChild;if(document.activeElement!==alias)alias.value=state.draft.aliases[row.id]||'';
    tr.children[2].firstChild.textContent=kinds[row.kind]||row.kind;tr.children[2].lastChild.textContent=[row.master&&'归属 '+row.master,row.parent&&'父接口 '+row.parent].filter(Boolean).join(' · ');
    tr.children[3].firstChild.textContent=!row.valid?'不可用':row.carrier===false?'链路未连接':row.carrier===true?'已连接'+(Number(row.speed_mbps)>0?' · '+(Number(row.speed_mbps)>=1000?Number(row.speed_mbps)/1000+' Gb/s':row.speed_mbps+' Mb/s'):''):'链路未知';tr.children[3].lastChild.textContent=(row.addresses||[]).filter(ip=>!ip.startsWith('fe80:')).join(' · ')||'未获取到 IP';tr.children[4].textContent=format(row.tx_speed,true);tr.children[5].textContent=format(row.rx_speed,true);
  }
  $('empty-interfaces').hidden=rows.length>0;document.querySelectorAll('[data-mode]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.mode===state.draft.mode)));
  text('selection-count',`已选 ${selected.size} 个接口`);text('selection-members',state.draft.members.map(member=>state.interfaces.find(row=>row.id===member.id)?.name||member.name||member.id).join(' + ')||'尚未选择接口');
  text('dirty-state',dirty()?'有未应用的更改':'设置已同步');text('applied','当前生效：'+state.saved.members.map(member=>state.interfaces.find(row=>row.id===member.id)?.name||member.name).join(' + '));$('apply').disabled=state.saving||!state.previewValid||!dirty()||state.draft.members.some(member=>!state.interfaces.find(row=>row.id===member.id)?.valid);
}
function updatePreviewRates(){ const rows=state.draft.members.map(member=>state.interfaces.find(row=>row.id===member.id)),valid=state.previewValid&&rows.length&&rows.every(row=>row?.valid&&finite(row.rx_speed)&&finite(row.tx_speed));text('preview-tx',valid?format(rows.reduce((sum,row)=>sum+row.tx_speed,0),true):'—');text('preview-rx',valid?format(rows.reduce((sum,row)=>sum+row.rx_speed,0),true):'—'); }
async function preview() {
  const serial=++state.previewSerial;state.previewValid=false;renderInterfaces();
  try{const result=await api('/api/network/preview','POST',state.draft);if(serial!==state.previewSerial)return;state.previewValid=true; text('preview-message',result.message);$('preview-message').className='';text('preview-warning',result.warnings.join('；'));}
  catch(error){if(serial!==state.previewSerial)return;state.previewValid=false;text('preview-message',error.message);$('preview-message').className='error';text('preview-warning','');}
  renderInterfaces();updatePreviewRates();
}
function details(id){const row=state.interfaces.find(row=>row.id===id);text('interface-title',row.name);renderDl('interface-detail',[['别名',state.draft.aliases[id]||'未设置'],['类型',kinds[row.kind]||row.kind],['数据状态',row.valid?'计数可读':'不可用 / 接口消失'],['IP 地址',(row.addresses||[]).join('\n')||'未知'],['MAC',row.mac||'未知'],['永久 MAC',row.permanent_mac||'未提供'],['硬件路径',row.hardware||'虚拟接口'],['驱动',row.driver||'未提供'],['ifindex',row.ifindex],['RX / TX 原始字节',row.counters?.join(' / ')],['RX / TX 错误',row.errors?.join(' / ')],['RX / TX 丢包',row.drops?.join(' / ')],['近 24h 下载 / 上传',row.history?.valid?format(row.history.rx_bytes)+' / '+format(row.history.tx_bytes):'暂无有效区间'],['已观测时长',duration(row.history?.coverage_seconds)],['身份标识',id]]);$('interface-dialog').showModal();}
async function refreshSettings(){state.caps=await api('/api/capabilities');const d=state.caps; text('device-status',d.device.online?'显示端在线':'暂无活跃显示端');$('device-status').className='badge '+(d.device.online?'online':'offline');renderDl('device-info',[['服务地址',location.origin],['NAS 主机',location.hostname],['端口',location.port||80],['最近显示端',d.device.address||'等待连接'],['最近通信',d.device.age==null?'—':d.device.age+' 秒前'],['协议','HTTP / JSON v2']]);text('capabilities',`${d.interface_count} 个接口 · ${d.host_network?'宿主网络已验证':'网络命名空间待核对'} · 协议 v${d.protocol}`);renderList('capability-list',[['网络',d.host_network?'宿主接口、拓扑、地址':'宿主 sysfs；地址和拓扑不完整'],['GPU',d.supported.gpu.join(' / ')],['UPS',d.supported.ups.join(' / ')],['24h 历史','逐接口记录，合计只计算共同覆盖区间'],['磁盘 / 容量','物理块设备与已挂载文件系统自动检测']]);}
function download(name, value){const url=URL.createObjectURL(new Blob([JSON.stringify(value,null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
$('login-form').addEventListener('submit',async event=>{event.preventDefault();const button=event.target.querySelector('button');button.disabled=true;text('login-error','');try{const result=await api('/api/login','POST',{password:$('password').value});state.csrf=result.csrf;$('password').value='';await start();}catch(error){text('login-error',error.message);}finally{button.disabled=false;}});
$('logout').addEventListener('click',async()=>{try{await api('/api/logout','POST',{});}finally{showLogin();}});
document.querySelectorAll('[data-page]').forEach(button=>button.addEventListener('click',()=>navigate(button.dataset.page)));window.addEventListener('hashchange',()=>navigate(location.hash.slice(1)));window.addEventListener('resize',drawChart);
document.querySelectorAll('[data-mode]').forEach(button=>button.addEventListener('click',()=>{state.draft.mode=button.dataset.mode;if(['auto','recommended'].includes(state.draft.mode))state.draft.members=state.interfaces.filter(row=>state.network.recommended.includes(row.id)).map(row=>({id:row.id,name:row.name}));if(state.draft.mode==='single')state.draft.members=state.draft.members.slice(0,1);preview();}));
$('interfaces').addEventListener('change',event=>{const id=event.target.dataset.select;if(!id)return;const row=state.interfaces.find(row=>row.id===id);if(['auto','recommended'].includes(state.draft.mode))state.draft.mode='sum';if(event.target.checked){if(state.draft.mode==='single')state.draft.members=[];state.draft.members.push({id,name:row.name});}else state.draft.members=state.draft.members.filter(member=>member.id!==id);preview();});
$('interfaces').addEventListener('input',event=>{const id=event.target.dataset.alias;if(id){state.draft.aliases[id]=event.target.value;renderInterfaces();}});
$('interfaces').addEventListener('click',event=>{const id=event.target.dataset.detail;if(id)details(id);});$('close-dialog').addEventListener('click',()=>$('interface-dialog').close());
$('show-virtual').addEventListener('change',renderInterfaces);$('filter').addEventListener('input',renderInterfaces);$('preview').addEventListener('click',preview);$('reset-selection').addEventListener('click',()=>loadInterfaces(true).catch(error=>toast(error.message,true)));
$('apply').addEventListener('click',async()=>{ state.saving=true;$('apply').disabled=true;try{const value=await api('/api/settings','PUT',state.draft);state.saved=structuredClone(value);state.draft=structuredClone(value);renderInterfaces();toast('设置已应用，网页与小屏幕使用新的统计范围');}catch(error){toast(error.message,true);}finally{state.saving=false;renderInterfaces();}});
$('export-settings').addEventListener('click',()=>download('nas-monitor-settings.json',state.saved));$('import-settings').addEventListener('change',async event=>{const file=event.target.files[0];if(!file)return;try{if(file.size>32768)throw new Error('配置文件不能超过 32KB');const value=JSON.parse(await file.text());if(!value||!Array.isArray(value.members)||!value.members.every(m=>m&&typeof m.id==='string'&&/^[a-f0-9]{16}$/.test(m.id))||!['auto','recommended','single','sum'].includes(value.mode)||(value.aliases&&(typeof value.aliases!=='object'||Array.isArray(value.aliases)||!Object.values(value.aliases).every(v=>typeof v==='string'&&v.length<=40))))throw new Error('不是有效的数据源配置');state.draft={...value,revision:state.saved.revision,aliases:value.aliases||{}};navigate('network');await preview();toast('已载入配置预览，点击“应用设置”后才会保存');}catch(error){toast(error.message,true);}event.target.value='';});
$('diagnostics').addEventListener('click',()=>{const d=state.overview;download('nas-monitor-diagnostics.json',{protocol:2,host_network:state.caps?.host_network,interface_count:state.caps?.interface_count,intervals:state.caps?.intervals,metrics:d?{available:d.available,age:d.age,cpu:d.cpu,gpu:d.gpu,memory:d.memory,storage:d.storage,ups:{watts:d.ups.watts,valid:d.ups.valid,source:d.ups.source,age_seconds:d.ups.age_seconds}}:null});});
$('reveal-token').addEventListener('click',async()=>{try{if(!$('device-token').hidden){$('device-token').hidden=true;$('device-token').value='';text('reveal-token','查看只读 Token');return;}const value=await api('/api/device-access');$('device-token').value=value.token;$('device-token').hidden=false;text('reveal-token','隐藏只读 Token');}catch(error){toast(error.message,true);}});
$('password-form').addEventListener('submit',async event=>{event.preventDefault();const values=Object.fromEntries(new FormData(event.target));try{await api('/api/password','PUT',values);event.target.reset();showLogin();toast('密码已更新，请重新登录');}catch(error){toast(error.message,true);}});
function updateClock(){text('clock',new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false}).format(new Date()));}
updateClock();setInterval(updateClock,1000);
(async()=>{try{const session=await api('/api/session');if(session.authenticated){state.csrf=session.csrf;await start();}}catch(_){text('login-error','无法连接服务，请刷新重试');}})();
