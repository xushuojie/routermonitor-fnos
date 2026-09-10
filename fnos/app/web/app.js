'use strict';
const $ = id => document.getElementById(id);
const text = (id, value) => { $(id).textContent = value ?? '—'; };
const state = {csrf: null, page: 'overview', running: false, saved: null, draft: null, interfaces: [], overview: null, caps: null, previewValid: false, previewSerial: 0, generation: 0, saving: false, points: [], seq: 0, epoch: '', tableKey: '', hardware: null, hardwareSaved: null, hardwareDraft: null, hardwareSaving: false, hardwareBusy: false, hardwareFormKey: '', intervals: {network: .2, status: 1}, settingsRefreshed: 0};
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
function showLogin() { state.generation++; state.tokenEpoch=(state.tokenEpoch||0)+1; $('token-form').reset(); $('custom-token-panel').open=false; text('token-save-status',''); clearTimeout(state.refreshTimer); clearTimeout(state.streamTimer); clearTimeout(state.hardwareTimer); state.running = false; state.csrf = null; state.hardwareSaved = null; state.hardwareDraft = null; state.hardwareFormKey = ''; $('app').hidden = true; $('login').hidden = false; $('device-token').value = ''; $('device-token').hidden = true; $('copy-device-token').hidden = true; text('reveal-token', '查看只读 Token'); }
async function start() {
  if (state.running) return;
  $('login').hidden = true; $('app').hidden = false; state.running = true; const generation=++state.generation;
  state.points = []; state.seq = 0; state.epoch = '';
  try { await loadInterfaces(true); } catch (error) { toast(error.message, true); }
  navigate(location.hash.slice(1) || 'overview'); refresh(generation); stream(generation); pollHardware(generation);
}
function navigate(page) {
  if (page === 'network') page = 'settings';
  if (!['overview','settings'].includes(page)) page = 'overview'; state.page = page;
  for (const p of ['overview','settings']) $('page-' + p).hidden = p !== page;
  document.querySelectorAll('[data-page]').forEach(button => { if (button.dataset.page === page) button.setAttribute('aria-current','page'); else button.removeAttribute('aria-current'); });
  text('breadcrumb', '控制台 / ' + {overview:'数据展示',settings:'设置'}[page]);
  if (location.hash !== '#' + page) history.replaceState(null, '', '#' + page);
  if (page === 'settings') { renderInterfaces(); refreshSettings().catch(error => toast(error.message, true)); }
  if (page === 'overview') drawChart();
}
function connected(ok) { text('connection', ok ? '实时连接' : '连接中断'); $('connection').className = 'badge ' + (ok ? 'online' : 'offline'); }
async function refresh(generation) {
  if (!state.running || generation!==state.generation) return;
  try {
    const overview = await api('/api/overview'); if(generation!==state.generation)return; state.overview=overview; connected(state.overview.available); renderOverview();
    if (state.page === 'settings') {
      await loadInterfaces(false);
      if (Date.now() - state.settingsRefreshed > 10000) await refreshSettings();
    }
  } catch (error) { connected(false); $('offline-note').hidden = false; clearOverview(); }
  if (state.running && generation===state.generation) state.refreshTimer=setTimeout(()=>refresh(generation), intervalMs('status', 1000));
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
  if (state.running && generation===state.generation) state.streamTimer=setTimeout(()=>stream(generation), Math.max(30, intervalMs('network', 200) - (performance.now() - started)));
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
    for (const point of points) { if (!finite(point[column])) { active=false; continue; } const x = (point[1] - end + 30) / 30 * plotWidth, y = bottom - point[column] / maximum * (bottom - top); if (!active || point[1]-previousTime>Math.max(.6, intervalMs('network', 200) / 1000 * 3)) c.moveTo(x,y); else c.lineTo(x,y); active=true; previousTime=point[1]; }
    c.stroke();
  }
}
function clearOverview() {
  renderPowerReadings(null);
  text('power-label','功率不可用'); text('power-source','等待有效采样');
  for (const id of ['network-tx','network-rx','disk-read','disk-write']) transfer(id,null);
  for (const [id,unit] of [['power','W'],['cpu','%'],['gpu','%'],['memory','%']]) { metric(id,null,unit); $(id+'-bar').value=0; }
  for (const id of ['traffic-tx','traffic-rx','storage-used','storage-total','cpu-temp','disk-temp','uptime']) text(id,'—'); $('storage-bar').value=0;
}
function renderOverview() {
  const d = state.overview; $('offline-note').hidden = !!d.available; if (!d.available) { clearOverview(); return; }
  renderPowerReadings(d.power?.sources);
  const power = d.power || {...d.ups, scope:'ups_output', label:'UPS 输出功率'};
  text('power-label', power.valid ? ({ups_output:'UPS 输出功率',platform:'平台功率',cpu_package:'CPU 封装功率'}[power.scope] || '功率') : '功率不可用');
  transfer('network-tx',d.net?.tx_speed); transfer('network-rx',d.net?.rx_speed);
  transfer('disk-read',d.disk_io.valid ? d.disk_io.read_speed : null); transfer('disk-write',d.disk_io.valid ? d.disk_io.write_speed : null);
  text('source-badge', d.sources.network.join(' + ') || '未选择接口'); $('source-badge').title=d.sources.network.join(' + '); text('disk-source',d.disk_io.devices || '未检测到物理磁盘');
  for (const [id,value,unit] of [['power',power.valid ? power.watts : null,'W'],['cpu',d.cpu.percent,'%'],['gpu',d.gpu.utilization,'%'],['memory',d.memory.percent,'%']]) { metric(id,value,unit,1); $(id+'-bar').value=finite(value)?value:0; }
  const powerMaximum = finite(power.watts) ? Math.max(35, Math.ceil(power.watts / 10) * 10) : 35;
  $('power-bar').max = powerMaximum; $('power-bar').setAttribute('aria-label', `功率 0 到 ${powerMaximum} 瓦`);
  text('power-source', power.reason || (power.valid ? 'UPS 输出端功率，不一定等于 NAS 单机功率' : '无可读的功率传感器'));

  text('gpu-source',d.gpu.reason || (d.gpu.backend === 'unavailable' ? '当前硬件无可用采集方式' : d.gpu.backend === 'i915' ? 'Intel i915 · 渲染引擎' : d.gpu.backend));
  text('memory-detail',d.memory.valid?`${format(d.memory.used)} / ${format(d.memory.total)}`:'内存数据不可用');
  text('traffic-tx',d.traffic_24h.valid?format(d.traffic_24h.tx_bytes):'—'); text('traffic-rx',d.traffic_24h.valid?format(d.traffic_24h.rx_bytes):'—');
  text('traffic-coverage',(d.traffic_24h.basis === 'legacy_group' ? '原组合历史 · 已覆盖 ' : '成员共同覆盖 ') + duration(d.traffic_24h.coverage_seconds));
  text('storage-used',d.storage.valid?format(d.storage.used):'—'); text('storage-total',d.storage.valid?' / '+format(d.storage.total):' / —'); $('storage-bar').value=d.storage.valid?d.storage.percent:0;
  text('storage-detail',d.storage.valid?`${d.storage.percent}% 已用 · ${d.storage.filesystems} 个文件系统（已去重）`:(d.storage.reason || '未读取到已挂载的数据卷'));
  text('cpu-temp',finite(d.temperature_summary.cpu)?d.temperature_summary.cpu+' °C':'不可用'); text('disk-temp',finite(d.temperature_summary.disk)?d.temperature_summary.disk+' °C':'不可用'); text('uptime',duration(d.uptime));
  text('overview-age',`状态更新于 ${d.age.toFixed(1)} 秒前`);
  const rows=[['容量发现',d.storage.mode === 'auto' ? '自动发现本地存储卷' : '手动选择存储卷'],['CPU / 内存',d.sources.cpu+' / '+d.sources.memory],['GPU',d.sources.gpu === 'unavailable'?'未支持当前 GPU 采集方式':d.sources.gpu],['数据卷',d.sources.storage_paths.join('、')],['UPS',`${d.ups.source} · ${d.ups.valid?'有效':'不可用'}${d.ups.reason?' · '+d.ups.reason:''}${d.ups.age_seconds!=null?' · '+d.ups.age_seconds+'秒前':''}`],...d.temp.map(sensor=>[sensor.type,`${sensor.temp} °C`])];
  renderList('source-details',rows);
}
function renderPowerReadings(sources) {
  const names = {ups_output:'UPS 输出功率',platform:'平台功率',cpu_package:'CPU 封装功率'};
  for (const id of ['power-readings','power-overview-readings']) {
    const fragment = document.createDocumentFragment();
    for (const [scope,label] of Object.entries(names)) {
      const value = sources?.[scope], valid = value?.valid === true && finite(value.watts);
      const row=document.createElement('div'), heading=document.createElement('div'), title=document.createElement('strong'), reading=document.createElement('strong'), reason=document.createElement('p');
      row.className='power-reading'; row.dataset.scope=scope; heading.className='volume-heading'; title.textContent=label;
      reading.textContent=valid ? value.watts.toLocaleString('en-US',{maximumFractionDigits:1})+' W' : '不可用';
      reading.className=valid?'power-reading-value':'muted'; reason.className='muted small-text';
      reason.textContent=value?.reason || '等待该来源的有效读数；设备可能未提供传感器';
      heading.append(title,reading);row.append(heading,reason);fragment.append(row);
    }
    $(id).replaceChildren(fragment);
  }
}
function renderList(id, rows) { const fragment=document.createDocumentFragment(); for(const [name,value] of rows){const div=document.createElement('div'), label=document.createElement('span'), content=document.createElement('strong'); label.textContent=name;content.textContent=value??'—';div.append(label,content);fragment.append(div);} $(id).replaceChildren(fragment); }
function renderDl(id, rows) { const fragment=document.createDocumentFragment(); for(const [name,value] of rows){const dt=document.createElement('dt'),dd=document.createElement('dd');dt.textContent=name;dd.textContent=value??'—';fragment.append(dt,dd);} $(id).replaceChildren(fragment); }
function intervalMs(name, fallback) { const seconds = state.intervals?.[name]; return finite(seconds) && seconds > 0 ? Math.max(100, seconds * 1000) : fallback; }
function intervalLabel(seconds) { return finite(seconds) && seconds > 0 ? (seconds < 1 ? `${Math.round(seconds * 1000)} ms` : `${seconds} 秒`) : '自动'; }
function hardwareDirty() { return !!state.hardwareSaved && JSON.stringify(state.hardwareSaved) !== JSON.stringify(state.hardwareDraft); }
function updateHardwareControls() {
  const d = state.hardwareDraft;
  $('hardware-fields').disabled = !d || state.hardwareSaving;
  if (!d) return;
  const manual = d.storage_mode === 'selected';
  $('storage-custom').disabled = !manual;
  $('storage-choices').querySelectorAll('input').forEach(input => input.disabled = !manual);
  $('ups-socket-field').hidden = d.ups_mode !== 'local';
  $('ups-remote-fields').hidden = d.ups_mode !== 'remote';
  $('ups-name-field').hidden = d.ups_mode !== 'remote';
  $('ups-socket').disabled = d.ups_mode !== 'local';
  $('ups-host').disabled = d.ups_mode !== 'remote';
  $('ups-port').disabled = d.ups_mode !== 'remote';
  $('ups-name').disabled = d.ups_mode !== 'remote';
  $('save-hardware').disabled = state.hardwareSaving || state.hardwareBusy || !hardwareDirty();
  $('reload-hardware').disabled = state.hardwareSaving || state.hardwareBusy;
  text('hardware-dirty', state.hardwareSaving ? '正在保存硬件设置…' : hardwareDirty() ? '硬件设置有未保存的更改' : '硬件设置已同步');
  text('hardware-config-state', hardwareDirty() ? '待保存' : '已同步');
}
function readHardwareForm() {
  if (!state.hardwareDraft || state.hardwareSaving) return;
  const manual = $('storage-mode').value === 'selected';
  const paths = manual ? [...$('storage-choices').querySelectorAll('input:checked')].map(input => input.value) : [...state.hardwareDraft.storage_paths];
  if (manual) paths.push(...$('storage-custom').value.split(/\r?\n/).map(path => path.trim()).filter(Boolean));
  state.hardwareDraft = {...state.hardwareDraft, storage_mode: $('storage-mode').value, storage_paths: [...new Set(paths)], cpu_sensor: $('cpu-sensor').value, disk_sensor: $('disk-sensor').value, gpu: $('gpu-select').value, power_mode: $('power-mode').value, ups_mode: $('ups-mode').value, ups_socket: $('ups-socket').value.trim(), ups_host: $('ups-host').value.trim(), ups_port: Number($('ups-port').value), ups_name: $('ups-name').value.trim(), profile: $('sampling-profile').value};
  updateHardwareControls();
}
function fillSelect(id, rows, selected) {
  const select = $(id), items = [...rows];
  if (selected && !items.some(([value]) => value === selected)) items.push([selected, `${selected} · 当前未发现`]);
  const key = JSON.stringify(items);
  if (select.dataset.options !== key) {
    const fragment = document.createDocumentFragment();
    for (const [value, label] of items) { const option = document.createElement('option'); option.value = value; option.textContent = label; fragment.append(option); }
    select.replaceChildren(fragment); select.dataset.options = key;
  }
  select.value = selected || 'auto';
}
function renderHardwareForm() {
  const d = state.hardwareDraft, h = state.hardware;
  if (!d || !h) return;
  $('storage-mode').value = d.storage_mode;
  const volumes = (h.storage?.volumes || []).filter((v, i, all) => typeof v.path === 'string' && all.findIndex(other => other.path === v.path) === i);
  const selected = new Set(d.storage_paths || []);
  const key = JSON.stringify(volumes.map(v => [v.path, v.filesystem, v.valid, v.reason]));
  if (state.hardwareFormKey !== key) {
    const fragment = document.createDocumentFragment();
    for (const volume of volumes) {
      const label = document.createElement('label'), input = document.createElement('input'), body = document.createElement('span'), name = document.createElement('strong'), info = document.createElement('small');
      label.className = 'storage-choice'; input.type = 'checkbox'; input.value = volume.path; input.setAttribute('aria-label', `统计 ${volume.path}`);
      name.textContent = volume.path; info.textContent = `${volume.filesystem || '未知文件系统'}${volume.valid ? '' : ' · ' + (volume.reason || '暂不可用')}`;
      body.append(name, info); label.append(input, body); fragment.append(label);
    }
    if (!volumes.length) { const empty = document.createElement('p'); empty.className = 'muted small-text'; empty.textContent = '尚未发现存储卷，可在手动模式填写挂载路径。'; fragment.append(empty); }
    $('storage-choices').replaceChildren(fragment); state.hardwareFormKey = key;
  }
  $('storage-choices').querySelectorAll('input').forEach(input => input.checked = d.storage_mode === 'auto' ? !!volumes.find(v => v.path === input.value)?.included : selected.has(input.value));
  if (document.activeElement !== $('storage-custom')) $('storage-custom').value = [...selected].filter(path => !volumes.some(v => v.path === path)).join('\n');
  const sensors = h.sensors || [];
  const sensorChoices = kind => sensors.filter(s => s.kind === kind || s.kind === 'other').map(s => [s.id, `${s.label || s.id}${finite(s.temp) ? ` · ${s.temp} °C` : ' · 温度不可用'}`]);
  fillSelect('cpu-sensor', [['auto','自动选择'], ['off','关闭采集'], ...sensorChoices('cpu')], d.cpu_sensor);
  fillSelect('disk-sensor', [['auto','自动选择最高温度'], ['off','关闭采集'], ...sensorChoices('disk')], d.disk_sensor);
  fillSelect('gpu-select', [['auto','自动选择'], ['off','关闭采集'], ...(h.gpus || []).map(g => [g.id, `${g.label || g.id} · ${g.backend || '未知来源'}${g.valid ? '' : ' · ' + (g.reason || '不可用')}`])], d.gpu);
  $('power-mode').value = d.power_mode || 'auto';
  $('ups-mode').value = d.ups_mode; $('sampling-profile').value = d.profile;
  for (const [id, value] of [['ups-socket',d.ups_socket],['ups-host',d.ups_host],['ups-port',d.ups_port ?? 3493],['ups-name',d.ups_name]]) if (document.activeElement !== $(id)) $(id).value = value ?? '';
  const names = document.createDocumentFragment();
  for (const ups of (h.ups || []).filter(ups => ups.id.startsWith('/'))) { const option = document.createElement('option'); option.value = ups.id; option.label = ups.label || ups.id; names.append(option); }
  $('ups-sockets').replaceChildren(names);
  text('ups-discovered', (h.ups || []).map(ups => `${ups.label || ups.id}${ups.valid ? '' : ' · ' + (ups.reason || '数据不可用')}`).join('；') || '尚未发现本机 UPS，可指定本机或远程 NUT 数据源。');
  updateHardwareControls();
}
function renderHardware() {
  const h = state.hardware; if (!h) return;
  const volumes = h.storage?.volumes || [], fragment = document.createDocumentFragment();
  text('volume-count', `${volumes.filter(v => v.included && v.valid).length} 个计入合计 / ${volumes.length} 个挂载点`);
  for (const v of volumes) {
    const row = document.createElement('div'), head = document.createElement('div'), name = document.createElement('strong'), badge = document.createElement('span'), info = document.createElement('p'), meter = document.createElement('progress'), amountLine = document.createElement('div');
    row.className = 'volume-row'; head.className = 'volume-heading'; name.textContent = v.path; badge.className = 'badge ' + (v.valid && v.included ? 'online' : 'offline'); badge.textContent = !v.valid ? '不可用' : v.included ? '计入合计' : '未计入合计'; head.append(name, badge);
    info.className = 'muted small-text'; info.textContent = [v.filesystem, v.source].filter(Boolean).join(' · ');
    meter.max = 100; meter.value = v.valid && finite(v.percent) ? v.percent : 0; meter.setAttribute('aria-label', `${v.path} 空间使用率`);
    amountLine.className = 'volume-amount'; amountLine.textContent = v.valid ? `${format(v.used)} / ${format(v.total)} · ${finite(v.percent) ? v.percent.toFixed(1) + '%' : '—'} 已用` : '空间数据不可用';
    row.append(head, info, meter, amountLine);
    if (v.reason) { const reason = document.createElement('p'); reason.className = 'muted small-text volume-reason'; reason.textContent = v.reason; row.append(reason); }
    fragment.append(row);
  }
  if (!volumes.length) { const empty = document.createElement('p'); empty.className = 'muted'; empty.textContent = h.storage?.reason || '未发现可读取的存储卷，请在设置中核对挂载路径。'; fragment.append(empty); }
  $('volume-list').replaceChildren(fragment);
  const diagnostics = document.createDocumentFragment();
  const statusNames = {ok:'已识别',ready:'已识别',available:'可用',unavailable:'不可用',missing:'未发现',warning:'需核对',error:'不可用',disabled:'已关闭',off:'已关闭',pending:'检测中',partial:'部分可用'};
  for (const d of h.diagnostics || []) {
    const row = document.createElement('div'), top = document.createElement('div'), component = document.createElement('strong'), badge = document.createElement('span'), detail = document.createElement('p');
    row.className = 'diagnostic-row'; top.className = 'volume-heading'; component.textContent = {cpu_temperature:'CPU 温度',disk_temperature:'硬盘温度',gpu:'显卡',ups:'UPS 功率',storage:'存储空间',network:'网络',cpu:'处理器',memory:'内存',disk_io:'硬盘读写'}[d.component] || d.component; badge.className = 'badge ' + (['ok','ready','available'].includes(d.status) ? 'online' : 'offline'); badge.textContent = statusNames[d.status] || d.status || '未知'; detail.className = 'muted small-text'; detail.textContent = d.detail || '未提供进一步信息'; top.append(component,badge); row.append(top,detail); diagnostics.append(row);
  }
  if (!diagnostics.childNodes.length) { const empty = document.createElement('p'); empty.className = 'muted'; empty.textContent = '尚未获取采集诊断。'; diagnostics.append(empty); }
  $('hardware-diagnostics').replaceChildren(diagnostics); text('hardware-age', '更新于 ' + new Date().toLocaleTimeString('zh-CN', {hour12:false}));
  renderHardwareForm();
}
function applyIntervals(intervals) {
  if (!intervals) return;
  state.intervals = {...state.intervals, ...intervals};
  text('chart-interval', `每 ${intervalLabel(state.intervals.network)} 采样 · 包含局域网流量`);
  text('footer-intervals', `${intervalLabel(state.intervals.network)} 网络 · ${intervalLabel(state.intervals.status)} 状态 · 10 秒硬件清单`);
  renderList('active-intervals', [['当前生效模式', {realtime:'实时',standard:'标准',eco:'低占用'}[state.hardware?.settings?.profile || state.hardwareSaved?.profile] || '自动'], ['网络 / 状态', `${intervalLabel(state.intervals.network)} / ${intervalLabel(state.intervals.status)}`], ['存储 / 温度 / UPS', ['storage','sensors','ups'].map(key => intervalLabel(state.intervals[key])).join(' / ')]]);
}
async function loadHardware(reset = false) {
  if (state.hardwareBusy || state.hardwareSaving) return;
  const generation = state.generation, epoch = state.hardwareEpoch || 0;
  state.hardwareBusy = true; updateHardwareControls();
  try {
    const [hardware, settings] = await Promise.all([api('/api/hardware'), reset ? api('/api/hardware-settings') : Promise.resolve(null)]);
    if (generation !== state.generation || epoch !== (state.hardwareEpoch || 0)) return;
    const saved = settings || hardware.settings;
    if (!saved || !Array.isArray(saved.storage_paths)) throw new Error('硬件设置响应不完整，请重新载入');
    state.hardware = hardware;
    if (!state.hardwareSaving && (reset || !hardwareDirty())) { state.hardwareSaved = structuredClone(saved); state.hardwareDraft = structuredClone(saved); }
    applyIntervals(hardware.intervals); renderHardware(); $('hardware-settings-error').hidden = true;
  } catch (error) {
    if (generation !== state.generation) return;
    text('hardware-settings-error', '硬件设置暂时无法读取：' + error.message); $('hardware-settings-error').hidden = false; text('hardware-age', '硬件读取失败，等待重试');
    text('volume-count','读取失败'); text('volume-list','暂时无法读取存储卷空间，请等待服务恢复。'); text('hardware-diagnostics','硬件诊断暂时不可用，等待重新连接。');
  } finally { state.hardwareBusy = false; updateHardwareControls(); }
}
async function pollHardware(generation) {
  if (!state.running || generation !== state.generation) return;
  await loadHardware();
  if (state.running && generation === state.generation) state.hardwareTimer = setTimeout(() => pollHardware(generation), 10000);
}
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
async function refreshSettings(){const generation=state.generation; const caps=await api('/api/capabilities');if(generation!==state.generation)return;state.caps=caps;state.settingsRefreshed=Date.now();const d=state.caps; text('device-status',d.device.online?'显示端在线':'暂无活跃显示端');$('device-status').className='badge '+(d.device.online?'online':'offline');const lan=d.lan_access||{},addresses=lan.addresses||[];$('copy-service-address').disabled=!addresses.length;text('copy-service-address',addresses.length>1?'复制首个局域网地址':'复制局域网地址');renderDl('device-info',[...(addresses.length?addresses.map((entry,index)=>[index===0?'局域网服务地址（'+entry.interface+'）':'其他局域网地址（'+entry.interface+'）',entry.url]):[['局域网服务地址',lan.reason||'正在识别 NAS 局域网地址']]),['应用端口',lan.port??'—'],['最近显示端',d.device.address||'等待连接'],['最近通信',d.device.age==null?'—':d.device.age+' 秒前'],['协议','HTTP / JSON v2']]);text('capabilities',`${d.interface_count} 个接口 · ${d.host_network?'宿主网络已验证':'网络命名空间待核对'} · 协议 v${d.protocol}`);renderList('capability-list',[['网络',d.host_network?'宿主接口、拓扑、地址':'宿主 sysfs；地址和拓扑不完整'],['GPU',d.supported.gpu.join(' / ')],['UPS',d.supported.ups.join(' / ')],['24h 历史','逐接口记录，合计只计算共同覆盖区间'],['磁盘 / 容量','物理块设备与已挂载文件系统自动检测']]);}
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
$('export-settings').addEventListener('click',()=>download('nas-monitor-settings.json',state.saved));$('import-settings').addEventListener('change',async event=>{const file=event.target.files[0];if(!file)return;try{if(file.size>32768)throw new Error('配置文件不能超过 32KB');const value=JSON.parse(await file.text());if(!value||!Array.isArray(value.members)||!value.members.every(m=>m&&typeof m.id==='string'&&/^[a-f0-9]{16}$/.test(m.id))||!['auto','recommended','single','sum'].includes(value.mode)||(value.aliases&&(typeof value.aliases!=='object'||Array.isArray(value.aliases)||!Object.values(value.aliases).every(v=>typeof v==='string'&&v.length<=40))))throw new Error('不是有效的数据源配置');state.draft={...value,revision:state.saved.revision,aliases:value.aliases||{}};navigate('settings');$('network-section').scrollIntoView({block:'start'});await preview();toast('已载入配置预览，点击“应用设置”后才会保存');}catch(error){toast(error.message,true);}event.target.value='';});
$('diagnostics').addEventListener('click',async()=>{const button=$('diagnostics');button.disabled=true;try{download('nas-monitor-diagnostics.json',await api('/api/diagnostics'));}catch(error){toast(error.message,true);}finally{button.disabled=false;}});
$('reveal-token').addEventListener('click',async()=>{
  const generation=state.generation,epoch=state.tokenEpoch||0;
  try {
    if(!$('device-token').hidden){$('device-token').hidden=true;$('copy-device-token').hidden=true;$('device-token').value='';text('reveal-token','查看只读 Token');return;}
    const value=await api('/api/device-access');
    if(generation!==state.generation||epoch!==(state.tokenEpoch||0))return;
    $('device-token').value=value.token;$('device-token').hidden=false;$('copy-device-token').hidden=false;text('reveal-token','隐藏只读 Token');
  }catch(error){toast(error.message,true);}
});
$('token-form').addEventListener('submit',async event=>{
  event.preventDefault();
  const body=Object.fromEntries(new FormData(event.target));
  if(!/^[\x21-\x7e]{1,512}$/.test(body.token)){text('token-save-status','Token 需为 1–512 个英文、数字或符号，不能包含空格、中文或换行');return;}
  if(body.token!==body.confirm){text('token-save-status','两次输入的 Token 不一致');return;}
  const generation=state.generation;state.tokenEpoch=(state.tokenEpoch||0)+1;
  $('save-device-token').disabled=true;$('reveal-token').disabled=true;text('token-save-status','正在保存…');
  try {
    await api('/api/device-access','PUT',body);
    if(generation!==state.generation)return;
    state.tokenEpoch++;event.target.reset();$('device-token').value='';$('device-token').hidden=true;$('copy-device-token').hidden=true;text('reveal-token','查看只读 Token');
    text('token-save-status','已保存并生效，请在安卓和 ESP8266 端同步更新 Token。');toast('新 Token 已生效，旧 Token 已失效');
  }catch(error){if(generation===state.generation)text('token-save-status',error.message);}
  finally{$('save-device-token').disabled=false;$('reveal-token').disabled=false;}
});
$('password-form').addEventListener('submit',async event=>{event.preventDefault();const values=Object.fromEntries(new FormData(event.target));try{await api('/api/password','PUT',values);event.target.reset();showLogin();toast('密码已更新，请重新登录');}catch(error){toast(error.message,true);}});
$('hardware-form').addEventListener('input', readHardwareForm);
$('hardware-form').addEventListener('change', readHardwareForm);
$('storage-mode').addEventListener('change', () => { readHardwareForm(); renderHardwareForm(); });
$('reload-hardware').addEventListener('click', () => loadHardware(true));
$('hardware-form').addEventListener('submit', async event => {
  event.preventDefault(); readHardwareForm();
  if (!state.hardwareDraft || state.hardwareSaving || state.hardwareBusy || !hardwareDirty()) return;
  const d = state.hardwareDraft;
  if (d.storage_mode === 'selected' && (!d.storage_paths.length || d.storage_paths.some(path => !path.startsWith('/') || path.includes('\0')))) { toast('手动模式请至少选择一个存储卷，或填写宿主机绝对路径。', true); return; }
  if (d.ups_mode === 'remote' && (!d.ups_host || !d.ups_name || !Number.isInteger(d.ups_port) || d.ups_port < 1 || d.ups_port > 65535)) { toast('请填写远程 NUT 主机、设备名和 1–65535 范围内的端口。', true); return; }
  state.hardwareSaving = true; updateHardwareControls();
  let saved = false;
  try {
    const value = await api('/api/hardware-settings', 'PUT', d);
    state.hardwareEpoch = (state.hardwareEpoch || 0) + 1;
    state.hardwareSaved = structuredClone(value); state.hardwareDraft = structuredClone(value); saved = true;
    toast('硬件设置已保存，正在更新采集状态');
  } catch (error) { toast(error.status === 409 ? '硬件设置已被其他会话更新，请重新载入后再修改。' : error.message, true); }
  finally { state.hardwareSaving = false; updateHardwareControls(); }
  if (saved) await loadHardware(true);
});
function updateClock(){text('clock',new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false}).format(new Date()));}
updateClock();setInterval(updateClock,1000);
(async()=>{try{const session=await api('/api/session');if(session.authenticated){state.csrf=session.csrf;await start();}}catch(_){text('login-error','无法连接服务，请刷新重试');}})();

async function copySetting(value, label) {
  if (!value) { toast('暂时没有可复制的内容', true); return; }
  let copied = false;
  if (navigator.clipboard && window.isSecureContext) {
    try { await navigator.clipboard.writeText(value); copied = true; } catch (_) {}
  }
  if (!copied) {
    const area = document.createElement('textarea'); area.value = value; area.readOnly = true;
    area.style.position = 'fixed'; area.style.opacity = '0'; document.body.append(area); area.select();
    try { copied = document.execCommand('copy'); } catch (_) {} finally { area.remove(); }
  }
  toast(copied ? label + '已复制' : '浏览器未允许复制，请选中内容手动复制', !copied);
}
$('copy-service-address').addEventListener('click', () => copySetting(state.caps?.lan_access?.addresses?.[0]?.url, '局域网地址'));
$('copy-device-token').addEventListener('click', () => copySetting($('device-token').hidden ? '' : $('device-token').value, '只读 Token'));
document.querySelectorAll('[data-settings-target]').forEach(button => button.addEventListener('click', () => {
  $(button.dataset.settingsTarget).scrollIntoView({block: 'start', behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth'});
}));
