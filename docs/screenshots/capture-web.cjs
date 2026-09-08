// Render the released frontend with explicit synthetic API data, never a live NAS.
// Run: npm install --no-save playwright; npx playwright install chromium
//      node docs/screenshots/capture-web.cjs
const {chromium}=require('playwright');
const fs=require('fs'),path=require('path'),http=require('http');
const root=path.resolve(__dirname,'../..'),out=path.join(root,'images/guide');
fs.mkdirSync(out,{recursive:true});
const settings={revision:1,storage_mode:'auto',storage_paths:[],storage_volume_ids:{},cpu_sensor:'auto',disk_sensor:'auto',gpu:'auto',ups_mode:'auto',ups_socket:'',ups_host:'127.0.0.1',ups_port:3493,ups_name:'',profile:'realtime'};
const volumes=[{path:'/vol1',filesystem:'btrfs',source:'/dev/md0',total:8e12,used:3.2e12,percent:40,valid:true,included:true},{path:'/mnt/usb',filesystem:'ext4',source:'/dev/sdc1',total:2e12,used:7e11,percent:35,valid:true,included:true}];
const storage={total:1e13,used:3.9e12,percent:39,filesystems:2,valid:true,mode:'auto',volumes};
const interfaces=[{id:'1111111111111111',name:'eth0',physical:true,kind:'physical',valid:true,carrier:true,speed_mbps:2500,addresses:['192.168.1.10/24'],rx_speed:86e6,tx_speed:12.4e6},{id:'2222222222222222',name:'eth1',physical:true,kind:'physical',valid:true,carrier:false,speed_mbps:1000,addresses:[],rx_speed:0,tx_speed:0}];
const network={revision:1,mode:'auto',members:interfaces.map(r=>({id:r.id,name:r.name})),aliases:{'1111111111111111':'主网络 · 示例','2222222222222222':'备用端口 · 示例'}};
const hardware={settings,storage,intervals:{network:.2,status:1,storage:30,sensors:5,ups:2},sensors:[{id:'cpu-demo',kind:'cpu',label:'CPU Package',temp:48},{id:'disk-demo',kind:'disk',label:'硬盘温度',temp:36}],gpus:[{id:'gpu-demo',label:'Intel 核显 · 示例',backend:'i915',valid:true}],ups:[{id:'/run/nut/demo',label:'NUT UPS · 示例',valid:true}],diagnostics:[{component:'storage',status:'ok',detail:'示例：2 个本地文件系统，重复挂载已去重'},{component:'gpu',status:'ok',detail:'示例：Intel 引擎利用率可用'},{component:'cpu_temperature',status:'ok',detail:'示例：CPU Package 48 °C'},{component:'ups',status:'ok',detail:'示例：NUT 有功功率 28.6 W'}]};
const overview={available:true,age:.2,net:{tx_speed:12.4e6,rx_speed:86e6},disk_io:{valid:true,read_speed:184e6,write_speed:62e6,devices:'physical:sda,sdb'},cpu:{percent:18.5,valid:true},gpu:{utilization:12,valid:true,backend:'i915'},memory:{percent:42.5,valid:true,used:6.8e9,total:16e9},ups:{watts:28.6,valid:true,source:'nut'},traffic_24h:{valid:true,tx_bytes:68.4e9,rx_bytes:256e9,coverage_seconds:86400},storage,temperature_summary:{cpu:48,disk:36},uptime:657840,temp:[{type:'CPU Package',temp:48},{type:'Disk',temp:36}],sources:{network:['eth0','eth1'],cpu:'/host/proc/stat',memory:'/host/proc/meminfo',gpu:'i915',storage_paths:['/vol1','/mnt/usb']}};
const caps={device:{online:true,address:'192.168.1.20',age:1},interface_count:2,host_network:true,protocol:2,supported:{gpu:['Intel','AMD','NVIDIA（需宿主工具）'],ups:['NUT']},lan_access:{port:18199,addresses:[{interface:'eth0',ip:'192.168.1.10',url:'http://192.168.1.10:18199'}]}};
(async()=>{
 const server=http.createServer((req,res)=>{const name=req.url.split('?')[0]==='/'?'index.html':req.url.split('?')[0].slice(1);if(!['index.html','app.js','style.css'].includes(name)){res.writeHead(404);return res.end();}res.setHeader('Content-Type',name.endsWith('.js')?'text/javascript':name.endsWith('.css')?'text/css':'text/html');res.end(fs.readFileSync(path.join(root,'fnos/app/web',name)));});
 await new Promise(r=>server.listen(0,'127.0.0.1',r));
 let browser;
 try {
 browser=await chromium.launch({headless:true,...(process.env.DOCS_BROWSER_CHANNEL?{channel:process.env.DOCS_BROWSER_CHANNEL}:{})});const page=await browser.newPage({viewport:{width:1440,height:1050},deviceScaleFactor:1});const errors=[];page.on('pageerror',e=>errors.push(e.message));let authenticated=false;
 await page.route('**/api/**',async route=>{const u=new URL(route.request().url());let value;
 switch(u.pathname){case '/api/session':value={authenticated,csrf:'documentation-fixture'};break;case '/api/login':authenticated=true;value={authenticated:true,csrf:'documentation-fixture'};break;case '/api/network/interfaces':value={interfaces,settings:network,host_network:true,recommended:interfaces.map(r=>r.id)};break;case '/api/network/preview':value={valid:true,members:network.members,warnings:[],message:'选择有效，可应用当前统计范围。'};break;case '/api/overview':value=overview;break;case '/api/hardware':value=hardware;break;case '/api/hardware-settings':value=settings;break;case '/api/capabilities':value=caps;break;case '/api/device-access':value={token:'example-token-not-for-use'};break;case '/api/network/stream':value={epoch:'demo',seq:150,points:Array.from({length:150},(_,i)=>[i+1,i*.2,76e6+15e6*Math.sin(i/11),11e6+4e6*Math.sin(i/8)])};break;default:throw new Error('Missing fixture '+u.pathname);}
 await route.fulfill({json:value});});
 await page.goto('http://127.0.0.1:'+server.address().port);await page.locator('#password').waitFor();await page.screenshot({path:path.join(out,'web-login.png')});
 await page.locator('#password').fill('documentation-only');await page.locator('#login-form button').click();await page.locator('#connection').filter({hasText:'实时连接'}).waitFor();await page.locator('#volume-count').filter({hasText:'2 个计入'}).waitFor();await page.screenshot({path:path.join(out,'web-overview.png'),clip:{x:0,y:0,width:1440,height:980}});
 // Static positioning prevents sticky controls obscuring section screenshots.
 await page.addStyleTag({content:'.topbar,.settings-shortcuts,.apply-bar{position:static!important}'});
 await page.setViewportSize({width:1440,height:1600});
 await page.locator('.volume-card').screenshot({path:path.join(out,'web-volumes.png')});
 await page.locator('[data-page="settings"]').click();await page.locator('#device-info').filter({hasText:'192.168.1.10'}).waitFor();await page.locator('#management-section').screenshot({path:path.join(out,'web-management.png')});
 await page.locator('#custom-token-panel summary').click();await page.locator('.device-access-card').screenshot({path:path.join(out,'web-token.png')});
 await page.locator('#hardware-section').screenshot({path:path.join(out,'web-hardware.png')});
 await page.locator('#network-section').screenshot({path:path.join(out,'web-network.png')});
 await page.locator('[data-page="overview"]').click();await page.setViewportSize({width:430,height:932});await page.evaluate(()=>window.scrollTo(0,0));await page.screenshot({path:path.join(out,'web-mobile.png'),fullPage:true});
 if(errors.length)throw new Error(errors.join('\n'));console.log('Saved 8 screenshots of the real frontend with synthetic data.');
 }finally{if(browser)await browser.close();server.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
