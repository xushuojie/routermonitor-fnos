const fs = require('fs'), vm = require('vm'), assert = require('assert');
const script = fs.readFileSync(require('path').join(__dirname, '../app/web/app.js'), 'utf8');
const fn = script.slice(script.indexOf('async function refreshSettings()'), script.indexOf('function download(', script.indexOf('async function refreshSettings()')));
let rendered;
const caps = {device: {online: false, address: '', age: null}, interface_count: 2, host_network: true, protocol: 2,
  supported: {gpu: [], ups: []}, lan_access: {port: 18888, addresses: [{interface: 'eth0', ip: '192.168.31.20', url: 'http://192.168.31.20:18888'}]}};
const context = {state: {generation: 1}, api: async () => caps, text() {}, $: () => ({}), renderList() {}, renderDl: (id, rows) => { rendered = rows; }, Date,
  location: {origin: 'https://example.5ddd.com', hostname: 'example.5ddd.com', protocol: 'https:', port: ''}};
vm.createContext(context); vm.runInContext(fn, context);
(async () => {
  await context.refreshSettings();
  assert(JSON.stringify(rendered).includes('http://192.168.31.20:18888'));
  assert(!JSON.stringify(rendered).includes('5ddd.com'));
  assert(rendered.some(row => row[0] === '应用端口' && row[1] === 18888));
  caps.lan_access.addresses = []; caps.lan_access.reason = '未识别局域网地址';
  await context.refreshSettings();
  assert(JSON.stringify(rendered).includes('未识别局域网地址'));
  assert(!JSON.stringify(rendered).includes('5ddd.com'));
  console.log('PASS: LAN URL and actual port displayed behind FN Connect, no proxy fallback');
})().catch(error => { console.error(error); process.exitCode = 1; });
