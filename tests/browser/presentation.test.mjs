import assert from 'node:assert/strict';
import {before,after,test} from 'node:test';
import {createServer} from 'node:http';
import {readFile,mkdir,mkdtemp,rm} from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {gunzipSync} from 'node:zlib';
import {runInNewContext} from 'node:vm';
import {chromium,webkit} from 'playwright';

// Owned loopback server, fresh headless contexts, sandbox requested. Never
// connect to a running browser, launch a visible fallback or reuse a profile.
const root=fileURLToPath(new URL('../../_site/',import.meta.url));
const expected=JSON.parse(gunzipSync(await readFile(new URL('../../docs/evidence/traces.json.gz',import.meta.url))));
const mime={'.html':'text/html','.css':'text/css','.js':'text/javascript','.json':'application/json','.svg':'image/svg+xml','.png':'image/png'};
let browser,server,base;
before(async()=>{
  server=createServer(async(req,res)=>{
    const name=new URL(req.url,'http://localhost').pathname.slice(1)||'index.html';
    if(name==='favicon.ico'){res.writeHead(204).end();return;}
    if(!/^[a-z0-9.-]+$/i.test(name)){res.writeHead(404).end();return;}
    try{const bytes=await readFile(path.join(root,name));res.writeHead(200,{'Content-Type':mime[path.extname(name)]??'text/plain'});res.end(bytes);}
    catch{res.writeHead(404).end();}
  });
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  base=`http://127.0.0.1:${server.address().port}`;
  const channel=process.env.TIMING_BROWSER_CHANNEL;
  if(channel&&!['chrome','chromium'].includes(channel))throw Error('Unsupported channel');
  const engine=process.env.TIMING_TOUCH_ENGINE??'chromium';
  if(!['chromium','webkit'].includes(engine))throw Error('Unsupported engine');
  browser=engine==='webkit'?await webkit.launch({headless:true}):
    await chromium.launch({headless:true,chromiumSandbox:true,...(channel?{channel}:{})});
  console.log(`Fresh sandboxed headless browser ${browser.version()}`);
});
after(async()=>{if(browser)await browser.close();if(server)await new Promise(resolve=>server.close(resolve));});

async function fixture(t,options={},blockedStorage=false){
  const context=await browser.newContext({viewport:{width:1280,height:900},...options});
  const failures=[];
  await context.route('**/*',route=>{
    if(new URL(route.request().url()).origin!==base){failures.push(`External request ${route.request().url()}`);return route.abort();}
    return route.continue();
  });
  if(blockedStorage)await context.addInitScript(()=>Object.defineProperty(window,'localStorage',{get(){throw new DOMException('Blocked','SecurityError');}}));
  const page=await context.newPage();
  page.on('pageerror',e=>failures.push(e.message));
  page.on('console',e=>{if(e.type()==='error')failures.push(e.text());});
  page.on('response',r=>{if(r.status()>=400)failures.push(`${r.status()} ${r.url()}`);});
  t.after(async()=>{await context.close();assert.deepEqual(failures,[]);});
  return page;
}
const background=page=>page.locator('body').evaluate(e=>getComputedStyle(e).backgroundColor);
async function ready(page,location='/explorer.html'){
  await page.goto(base+location);
  await page.locator('#appearance:not([disabled])').waitFor();
  if(location.includes('explorer'))await page.locator('#rows tr').first().waitFor();
}
async function capture(page,name){
  if(!process.env.TIMING_SCREENSHOT_DIR)return;
  await mkdir(process.env.TIMING_SCREENSHOT_DIR,{recursive:true});
  await page.screenshot({path:path.join(process.env.TIMING_SCREENSHOT_DIR,name+'.png'),fullPage:true});
}
async function enlargeText(page){
  await page.evaluate(()=>{
    const elements=[...document.querySelectorAll('h1,h2,h3,p,label,button,select,output,.kicker,.badge,.legend span,.value,.caption,th,td,summary,pre,a')];
    const sizes=elements.map(element=>parseFloat(getComputedStyle(element).fontSize));
    elements.forEach((element,index)=>element.style.fontSize=2*sizes[index]+'px');
  });
}
const visibleState=page=>page.evaluate(()=>({scenario:document.querySelector('#scenario').value,
  variant:document.querySelector('#variant').value,seed:document.querySelector('#seed').value,
  start:document.querySelector('#start').value,span:document.querySelector('#span').value,
  metrics:document.querySelector('.metrics').textContent,rows:document.querySelector('#rows').textContent}));

test('mobile saved selection survives reload and browser history',async t=>{
  const page=await fixture(t,{viewport:{width:390,height:844},hasTouch:true,isMobile:true});
  await ready(page,'/explorer.html?ref=phone#selection');
  await page.locator('#scenario').selectOption('recovery');
  await page.locator('#variant').selectOption('no_fatigue');
  await page.locator('#seed').selectOption('73');
  const historySize=await page.evaluate(()=>history.length);
  await page.locator('#start').fill('30');await page.locator('#span').fill('20');
  await page.locator('#start').fill('31');await page.locator('#start').fill('30');
  assert.equal(await page.evaluate(()=>history.length),historySize);
  const original=await visibleState(page);
  const shared=await page.locator('#selection-link').getAttribute('href'),url=new URL(shared);
  assert.equal(url.searchParams.get('ref'),'phone');assert.equal(url.hash,'#selection');
  assert.deepEqual([...url.searchParams.keys()].sort(),['ref','scenario','seed','span','start','variant']);
  await page.locator('#selection-link').tap();await page.locator('#rows tr').first().waitFor();
  assert.deepEqual(await visibleState(page),original);
  await page.locator('#appearance').selectOption('clair');
  await page.reload();await page.locator('#rows tr').first().waitFor();
  assert.deepEqual(await visibleState(page),original);
  assert.equal(await page.locator('#appearance').inputValue(),'clair');
  await page.goto(base+'/causal.html');await page.goBack();
  await page.locator('#rows tr').first().waitFor();
  assert.deepEqual(await visibleState(page),original);
  await page.locator('#variant').selectOption('no_noise');
  const second=await visibleState(page);
  await page.goBack();await page.waitForFunction(()=>document.querySelector('#variant').value==='no_fatigue');
  assert.deepEqual(await visibleState(page),original);
  await page.goForward();await page.waitForFunction(()=>document.querySelector('#variant').value==='no_noise');
  assert.deepEqual(await visibleState(page),second);
  await page.goto(shared);await page.locator('#rows tr').first().waitFor();
  assert.deepEqual(await visibleState(page),original);
  for(const viewport of [{width:844,height:390},{width:320,height:740},{width:1280,height:900}]){
    await page.setViewportSize(viewport);
    assert.deepEqual(await visibleState(page),original);
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth)<=viewport.width);
  }
  const pending=page.waitForEvent('download');await page.locator('#download').tap();
  assert.deepEqual(JSON.parse(await readFile(await (await pending).path(),'utf8')),
    expected.cases.find(c=>c.scenario==='recovery'&&c.variant==='no_fatigue'&&c.seed===73));
  assert.equal(await page.locator('#rows tr').count(),22);
  await capture(page,'mobile-saved-selection');
});

test('mobile enlarged causal headings reflow without page overflow',async t=>{
  const page=await fixture(t,{viewport:{width:320,height:740},hasTouch:true,isMobile:true});
  await ready(page,'/causal.html');
  const headingSize=await page.locator('h1').evaluate(e=>parseFloat(getComputedStyle(e).fontSize));
  await enlargeText(page);
  assert.equal(await page.locator('h1').evaluate(e=>parseFloat(getComputedStyle(e).fontSize)),2*headingSize);
  const geometry=await page.evaluate(()=>({width:document.documentElement.scrollWidth,viewport:innerWidth}));
  assert.ok(geometry.width<=320,JSON.stringify(geometry));
  // Genuine two-dimensional evidence remains in its own scroll region.
  assert.ok(await page.locator('.table-wrap').first().evaluate(e=>e.scrollWidth>e.clientWidth));
  await capture(page,'mobile-causal-enlarged');
});

async function stateFunctions(){
  const template=await readFile(new URL('../../adaptive_timing/viewer.html',import.meta.url),'utf8');
  const source=template.match(/<script id="viewer-state-functions">([\s\S]*?)<\/script>/);
  assert.ok(source,'Selection functions ship inside the standalone template');
  const exports=runInNewContext(source[1]+';({readTraceSelection,traceSelectionHref})',{URL,URLSearchParams});
  return {read:(query,report=expected)=>JSON.parse(JSON.stringify(exports.readTraceSelection(query,report))),href:exports.traceSelectionHref};
}

test('selection query contract uses actual cases and bounded canonical window values',async()=>{
  const {read,href}=await stateFunctions();
  const valid={scenario:'recovery',variant:'no_fatigue',seed:'73',start:'30',span:'20'};
  assert.deepEqual(read(new URLSearchParams(valid).toString()),{state:valid,adjusted:false});
  const defaults={scenario:'steady',variant:'full',seed:'42',start:'0',span:'100'};
  assert.deepEqual(read(''),{state:defaults,adjusted:false});
  for(const query of ['seed=999','seed=073','seed=Infinity','seed=42&seed=73',
    'scenario=missing','variant=missing','start=-1','start=91','start=1.5','start=1e1',
    'span=9','span=101','span=100&span=10','start=','start=%3Cscript%3E']){
    assert.deepEqual(read(query),{state:defaults,adjusted:true},query);
  }
  assert.deepEqual(read('ref='+'x'.repeat(4093)),{state:defaults,adjusted:true});
  assert.deepEqual(read('ref=phone'),{state:defaults,adjusted:false});
  const sparse={cases:[{scenario:'burst',variant:'no_load',seed:17},{scenario:'recovery',variant:'full',seed:73}]};
  assert.deepEqual(read('scenario=burst&variant=full&seed=73',sparse),{
    state:{scenario:'burst',variant:'no_load',seed:'17',start:'0',span:'100'},adjusted:true});
  const link=new URL(href('file:///tmp/explorer.html?ref=phone&seed=42&seed=999#evidence',valid));
  assert.equal(link.protocol,'file:');assert.equal(link.hash,'#evidence');assert.equal(link.searchParams.get('ref'),'phone');
  assert.deepEqual(link.searchParams.getAll('seed'),['73']);
});

test('mobile invalid saved choices disclose supported fallback without altering evidence',async t=>{
  const page=await fixture(t,{viewport:{width:320,height:740},hasTouch:true,isMobile:true});
  await ready(page,'/explorer.html?scenario=recovery&variant=no_fatigue&seed=999&start=91&span=1.5&ref=keep');
  assert.equal(await page.locator('#selection-note').isVisible(),true);
  assert.equal(await page.locator('#seed').inputValue(),'42');
  assert.equal(await page.locator('#start').inputValue(),'0');assert.equal(await page.locator('#span').inputValue(),'100');
  const share=new URL(await page.locator('#selection-link').getAttribute('href'));
  assert.equal(share.searchParams.get('ref'),'keep');assert.equal(share.searchParams.get('seed'),'42');
  assert.deepEqual(JSON.parse(await page.locator('#data').textContent()),expected);
  await page.locator('#seed').selectOption('73');assert.equal(await page.locator('#selection-note').isVisible(),false);
  await enlargeText(page);
  const reflow=await page.evaluate(()=>({width:document.documentElement.scrollWidth,
    overflow:[...document.querySelectorAll('body *')].filter(e=>{
      const box=e.getBoundingClientRect();return box.right>320||e.scrollWidth>e.clientWidth+1;
    }).filter(e=>!e.closest('.tablewrap')).map(e=>({tag:e.tagName,id:e.id,class:e.className,
      width:e.clientWidth,scroll:e.scrollWidth,text:e.textContent.slice(0,80)}))}));
  assert.ok(reflow.width<=320,JSON.stringify(reflow));
  await capture(page,'mobile-explorer-enlarged');
});

test('mobile standalone export shares and restores without external dependencies or history access',async t=>{
  const page=await fixture(t,{viewport:{width:390,height:844},hasTouch:true,isMobile:true});await ready(page,'/');
  const pending=page.waitForEvent('download');await page.getByRole('link',{name:'Download the offline explorer'}).click();
  const dir=await mkdtemp(path.join(os.tmpdir(),'timing-offline-')),file=path.join(dir,'explorer.html');
  await (await pending).saveAs(file);
  const context=await browser.newContext({viewport:{width:390,height:844},hasTouch:true,isMobile:true});
  const requests=[],errors=[];const offline=await context.newPage();
  t.after(async()=>{await context.close();assert.equal(path.dirname(path.resolve(dir)),path.resolve(os.tmpdir()));
    assert.ok(path.basename(dir).startsWith('timing-offline-'));await rm(dir,{recursive:true,force:true});});
  await context.route('http**://**',route=>{requests.push(route.request().url());return route.abort();});
  await context.addInitScript(()=>{
    for(const name of ['pushState','replaceState'])history[name]=()=>{throw new DOMException('Blocked','SecurityError')};
    Object.defineProperty(window,'localStorage',{get(){throw new DOMException('Blocked','SecurityError')}});
  });
  offline.on('pageerror',e=>errors.push(e.message));
  await offline.goto(pathToFileURL(file).href+'?scenario=recovery&variant=no_fatigue&seed=73&start=30&span=20');
  await offline.locator('#rows tr').first().waitFor();
  assert.equal(await offline.locator('#seed').inputValue(),'73');assert.equal(await offline.locator('#rows tr').count(),22);
  await offline.locator('#variant').selectOption('no_noise');await offline.locator('#span').fill('40');
  assert.match(await offline.locator('#selection-note').textContent(),/could not update its address/);
  const shared=await offline.locator('#selection-link').getAttribute('href');
  assert.equal(new URL(shared).protocol,'file:');
  await offline.goto(shared);await offline.locator('#rows tr').first().waitFor();
  assert.equal(await offline.locator('#variant').inputValue(),'no_noise');assert.equal(await offline.locator('#span').inputValue(),'40');
  assert.deepEqual(JSON.parse(await offline.locator('#data').textContent()),expected);
  assert.deepEqual(requests,[]);assert.deepEqual(errors,[]);
});

test('Auto, overrides and redraw preserve the selected case and time window',async t=>{
  const page=await fixture(t,{colorScheme:'dark'});await ready(page);
  assert.equal(await background(page),'rgb(9, 9, 9)');
  await page.locator('#scenario').selectOption('saturation');await page.locator('#variant').selectOption('no_noise');
  await page.locator('#seed').selectOption('73');
  await page.locator('#start').fill('20');await page.locator('#span').fill('40');
  const original=await visibleState(page),darkCanvas=await page.locator('canvas').evaluate(e=>e.toDataURL());
  await page.locator('#appearance').selectOption('clair');
  assert.deepEqual(await visibleState(page),original);assert.equal(await background(page),'rgb(248, 247, 243)');
  assert.notEqual(await page.locator('canvas').evaluate(e=>e.toDataURL()),darkCanvas,'Actual canvas redraws');
  await page.emulateMedia({colorScheme:'light'});await page.emulateMedia({colorScheme:'dark'});
  assert.equal(await background(page),'rgb(248, 247, 243)');
  await page.locator('#appearance').selectOption('auto');assert.equal(await background(page),'rgb(9, 9, 9)');
  await page.emulateMedia({colorScheme:'light'});assert.equal(await background(page),'rgb(248, 247, 243)');
  assert.deepEqual(await visibleState(page),original);
  await page.locator('#appearance').selectOption('obscur');await page.reload();
  assert.equal(await page.locator('#appearance').inputValue(),'obscur');
});

test('all 48 retained cases export unchanged and preserve statuses and identities',async t=>{
  const page=await fixture(t);await ready(page);
  assert.deepEqual(JSON.parse(await page.locator('#data').textContent()),expected);
  for(const item of expected.cases){
    await page.locator('#scenario').selectOption(item.scenario);await page.locator('#variant').selectOption(item.variant);
    await page.locator('#seed').selectOption(String(item.seed));
    assert.equal(await page.locator('#planned').textContent(),`${item.planned} / ${item.events}`);
    assert.equal(await page.locator('#rejected').textContent(),String(item.rejected));
    assert.equal(await page.locator('#rows tr').count(),item.trace.length);
    assert.deepEqual(await page.locator('#rows tr').evaluateAll(rows=>rows.map(row=>[row.cells[0].textContent,row.cells[6].textContent])),
      item.trace.map(row=>[row.id,row.status==='rejected'?'rejected':row.constrained?'constrained':'planned']));
  }
  for(const mode of ['clair','obscur']){
    await page.locator('#appearance').selectOption(mode);
    const pending=page.waitForEvent('download');await page.locator('#download').click();const download=await pending;
    assert.deepEqual(JSON.parse(await readFile(await download.path(),'utf8')),expected.cases.at(-1));
  }
});

test('blocked storage does not disable controls or alter trace data',async t=>{
  const page=await fixture(t,{colorScheme:'light'},true);await ready(page);
  await page.locator('#appearance').selectOption('obscur');assert.equal(await background(page),'rgb(9, 9, 9)');
  await page.reload();assert.equal(await background(page),'rgb(248, 247, 243)');
  assert.deepEqual(JSON.parse(await page.locator('#data').textContent()),expected);
});

test('without JavaScript Auto and all saved case summaries remain readable',async t=>{
  const page=await fixture(t,{javaScriptEnabled:false,colorScheme:'dark'});await page.goto(base+'/explorer.html');
  assert.equal(await background(page),'rgb(9, 9, 9)');assert.equal(await page.locator('#appearance').isDisabled(),true);
  assert.equal(await page.locator('noscript tbody tr').count(),48);
  assert.equal(await page.locator('.controls').isVisible(),false);
  await page.emulateMedia({colorScheme:'light'});assert.equal(await background(page),'rgb(248, 247, 243)');
});

for(const mode of ['obscur','clair'])test(`${mode} keyboard, narrow and enlarged text layouts`,async t=>{
  const page=await fixture(t);await ready(page);
  await page.locator('#appearance').selectOption(mode);await capture(page,`explorer-${mode}-wide`);
  await page.setViewportSize({width:390,height:844});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
  await page.locator('#scenario').focus();await page.keyboard.press('ArrowDown');
  assert.equal(await page.locator('#scenario').inputValue(),'burst');
  await page.locator('#start').focus();await page.keyboard.press('ArrowRight');assert.equal(await page.locator('#start').inputValue(),'1');
  await page.getByRole('region',{name:'Scrollable task trace'}).focus();
  assert.equal(await page.evaluate(()=>document.activeElement.className),'tablewrap');
  await capture(page,`explorer-${mode}-narrow`);
  await page.addStyleTag({content:'body{font-size:32px!important}'});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
  await page.goto(base+'/');await page.locator('#appearance:not([disabled])').waitFor();
  assert.equal(await page.locator('#appearance').inputValue(),mode);
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
  await page.addStyleTag({content:'body{font-size:34px!important}'});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
  await capture(page,`site-${mode}-narrow`);
});

test('downloaded explorer works as a self-contained offline file',async t=>{
  const page=await fixture(t);await ready(page,'/');
  const pending=page.waitForEvent('download');await page.getByRole('link',{name:'Download the offline explorer'}).click();
  const download=await pending,dir=await mkdtemp(path.join(os.tmpdir(),'timing-offline-')),file=path.join(dir,'explorer.html');
  await download.saveAs(file);
  const context=await browser.newContext();const offline=await context.newPage(),requests=[];
  t.after(async()=>{
    await context.close();
    assert.equal(path.dirname(path.resolve(dir)),path.resolve(os.tmpdir()));
    assert.ok(path.basename(dir).startsWith('timing-offline-'));
    await rm(dir,{recursive:true,force:true});
  });
  await context.route('http**://**',route=>{requests.push(route.request().url());return route.abort();});
  await offline.goto(pathToFileURL(file).href);await offline.locator('#rows tr').first().waitFor();
  await offline.locator('#appearance').selectOption('obscur');
  assert.equal(await background(offline),'rgb(9, 9, 9)');
  assert.deepEqual(JSON.parse(await offline.locator('#data').textContent()),expected);assert.deepEqual(requests,[]);
  await page.getByRole('link',{name:'Open the trace explorer',exact:true}).click();await page.goBack();
  assert.equal(new URL(page.url()).pathname,'/');
});

test('print redraw and forced colors leave screen preference and trace intact',async t=>{
  const page=await fixture(t);await ready(page);await page.locator('#appearance').selectOption('obscur');
  const original=await visibleState(page),screen=await page.locator('canvas').evaluate(e=>e.toDataURL());
  await page.emulateMedia({media:'print'});assert.equal(await background(page),'rgb(248, 247, 243)');
  assert.notEqual(await page.locator('canvas').evaluate(e=>e.toDataURL()),screen);
  assert.equal(await page.evaluate(()=>localStorage.getItem('adaptive-timing-engine.appearance.v1')),'obscur');
  await page.emulateMedia({media:'screen',forcedColors:'active'});assert.deepEqual(await visibleState(page),original);
  await page.emulateMedia({forcedColors:'none'});assert.equal(await background(page),'rgb(9, 9, 9)');
});

async function fonts(page,selector){
  const session=await page.context().newCDPSession(page);
  try{
    await session.send('DOM.enable');await session.send('CSS.enable');const {root}=await session.send('DOM.getDocument');
    const {nodeId}=await session.send('DOM.querySelector',{nodeId:root.nodeId,selector});
    return (await session.send('CSS.getPlatformFontsForNode',{nodeId})).fonts.filter(f=>f.glyphCount>0);
  }finally{await session.detach();}
}
test('intentionally missing font remains a separate readable fallback',async t=>{
  const page=await fixture(t);await ready(page);
  await page.evaluate(()=>{const p=document.createElement('p');p.id='fallback';p.style.fontFamily='"Timing missing face",Arial,sans-serif';p.textContent='Readable fallback 123';document.body.append(p);});
  const providers=await fonts(page,'#fallback');assert.ok(providers.length);assert.ok(providers.every(f=>!f.postScriptName.startsWith('Inter')));
  console.log('Separate missing-font control:',JSON.stringify(providers));
});
test('local Inter supplies six actual faces under both appearances',
  {skip:process.env.TIMING_REQUIRE_INTER!=='1'},async t=>{
  const page=await fixture(t);await ready(page);
  const faces=[[400,'normal','Regular'],[600,'normal','SemiBold'],[700,'normal','Bold'],[400,'italic','Italic'],[600,'italic','SemiBoldItalic'],[700,'italic','BoldItalic']];
  await page.evaluate(faces=>{for(const [weight,style,name]of faces){const p=document.createElement('p');p.id='face-'+name;p.style.fontWeight=String(weight);p.style.fontStyle=style;p.textContent='Timing typography sample 123';document.body.append(p);}},faces);
  await page.evaluate(()=>document.fonts.ready);
  for(const mode of ['obscur','clair']){
    await page.locator('#appearance').selectOption(mode);
    for(const[,,name]of faces){const providers=await fonts(page,'#face-'+name);assert.ok(providers.length);assert.ok(providers.every(f=>f.postScriptName==='Inter-'+name),JSON.stringify({mode,name,providers}));console.log('Controlled specimen:',JSON.stringify({mode,name,providers}));}
    for(const [selector,name]of [['h1','Bold'],['.font-note','Regular'],['label[for="appearance"]','SemiBold']]){
      const providers=await fonts(page,selector);assert.ok(providers.some(f=>f.postScriptName==='Inter-'+name),JSON.stringify({selector,name,providers}));
    }
  }
});


test('worker figure follows effective appearance, prints Clair and downloads exact SVGs',async t=>{
  const page=await fixture(t,{colorScheme:'dark'});await ready(page,'/');
  const edition=async mode=>{
    const image=page.locator('.worker-'+mode);
    assert.equal(await image.isVisible(),true);
    assert.equal(await page.locator('.worker-'+(mode==='clair'?'obscur':'clair')).isVisible(),false);
    await image.scrollIntoViewIfNeeded();
    await page.waitForFunction(mode=>[...document.querySelectorAll('.worker-'+mode+' img')].filter(e=>e.getBoundingClientRect().width>0).every(e=>e.complete&&e.naturalWidth>0),mode);
  };
  await edition('obscur');
  await page.locator('#appearance').selectOption('clair');await edition('clair');
  await page.reload();await edition('clair');
  await page.emulateMedia({colorScheme:'light'});
  await page.locator('#appearance').selectOption('obscur');await edition('obscur');
  for(const mode of ['clair','obscur']){
    await page.locator('#appearance').selectOption(mode);
    await page.setViewportSize({width:390,height:844});await edition(mode);
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
    if(process.env.TIMING_SCREENSHOT_DIR){
      await mkdir(process.env.TIMING_SCREENSHOT_DIR,{recursive:true});
      await page.locator('#worker-figure').screenshot({path:path.join(process.env.TIMING_SCREENSHOT_DIR,'worker-'+mode+'-mobile.png')});
    }
    const pending=page.waitForEvent('download');
    await page.locator('a[download][href="worker-'+mode+'.svg"]').click();
    const download=await pending;
    assert.deepEqual(await readFile(await download.path()),await readFile(path.join(root,'worker-'+mode+'.svg')));
  }
  await page.emulateMedia({media:'print'});await edition('clair');
  await page.emulateMedia({media:'screen'});await edition('obscur');
  await page.locator('#appearance').selectOption('auto');await edition('clair');
  await page.emulateMedia({colorScheme:'dark'});await edition('obscur');
  for(const colorScheme of ['light','dark']){
    const noJS=await fixture(t,{javaScriptEnabled:false,colorScheme});await noJS.goto(base+'/');
    assert.equal(await noJS.locator('.worker-'+(colorScheme==='light'?'clair':'obscur')).isVisible(),true);
  }
});

test('causal report preserves every result across appearance and offline summary export',async t=>{
  const summary=JSON.parse(await readFile(path.join(root,'causal-summary.json'),'utf8'));
  const page=await fixture(t,{colorScheme:'dark'});await ready(page,'/causal.html');
  assert.equal(await background(page),'rgb(9, 9, 9)');
  assert.deepEqual(JSON.parse(await page.locator('#causal-data').textContent()),summary);
  assert.equal(await page.locator('#table-0 tbody tr').count(),24);
  assert.equal(await page.locator('#table-1 tbody tr').count(),48);
  assert.equal(await page.locator('#table-2 tbody tr').count(),48);
  const rows=await page.locator('table').allTextContents();
  for(const mode of ['clair','obscur']){
    await page.locator('#appearance').selectOption(mode);
    assert.deepEqual(await page.locator('table').allTextContents(),rows);
    const pending=page.waitForEvent('download');await page.locator('#download-summary').click();
    assert.deepEqual(JSON.parse(await readFile(await (await pending).path(),'utf8')),summary);
    await page.setViewportSize({width:390,height:844});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
    if(process.env.TIMING_SCREENSHOT_DIR){
      await mkdir(process.env.TIMING_SCREENSHOT_DIR,{recursive:true});
      await page.evaluate(()=>window.scrollTo(0,0));
      await page.screenshot({path:path.join(process.env.TIMING_SCREENSHOT_DIR,'causal-'+mode+'-mobile.png')});
      await page.locator('#table-0').scrollIntoViewIfNeeded();
      await page.screenshot({path:path.join(process.env.TIMING_SCREENSHOT_DIR,'causal-'+mode+'-table.png')});
    }
    if(process.env.TIMING_REQUIRE_INTER==='1'){
      for(const [selector,name]of [['h1','Bold'],['.font-note','Regular'],['label[for="appearance"]','SemiBold']]){
        const providers=await fonts(page,selector);
        assert.ok(providers.some(f=>f.postScriptName==='Inter-'+name),JSON.stringify({mode,selector,providers}));
        console.log('Causal report glyphs:',JSON.stringify({mode,selector,providers}));
      }
    }
  }
  await page.addStyleTag({content:'body{font-size:34px!important}'});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
  await page.emulateMedia({media:'print'});assert.equal(await background(page),'rgb(248, 247, 243)');
  await page.emulateMedia({media:'screen'});assert.equal(await background(page),'rgb(9, 9, 9)');
  const noJS=await fixture(t,{javaScriptEnabled:false,colorScheme:'dark'});await noJS.goto(base+'/causal.html');
  assert.equal(await noJS.locator('#table-0 tbody tr').count(),24);
  assert.equal(await noJS.locator('#appearance').isDisabled(),true);
  const blocked=await fixture(t,{},true);await ready(blocked,'/causal.html');
  await blocked.locator('#appearance').selectOption('obscur');assert.equal(await background(blocked),'rgb(9, 9, 9)');
  await page.goto(base+'/');await page.getByRole('link',{name:'Read the causal study',exact:true}).click();
  assert.equal(new URL(page.url()).pathname,'/causal.html');
  await page.goto(base+'/');
  const rawPending=page.waitForEvent('download');
  await page.getByRole('link',{name:'Download the full causal traces',exact:true}).click();
  assert.deepEqual(await readFile(await (await rawPending).path()),await readFile(path.join(root,'causal-raw.json.gz')));
  const context=await browser.newContext();t.after(()=>context.close());
  const offline=await context.newPage(),requests=[];
  await context.route('http**://**',route=>{requests.push(route.request().url());return route.abort();});
  await offline.goto(pathToFileURL(path.join(root,'causal.html')).href);
  await offline.locator('#appearance:not([disabled])').waitFor();
  assert.deepEqual(JSON.parse(await offline.locator('#causal-data').textContent()),summary);
  assert.deepEqual(requests,[]);
});


test('wide figure follows its container and preserves every delivered file', async t => {
  const page = await fixture(t, {viewport:{width:1280,height:900},colorScheme:'dark'});
  await page.goto(base+'/'); await page.locator('#appearance:not([disabled])').waitFor();
  for (const mode of ['clair','obscur']) {
    await page.locator('#appearance').selectOption(mode);
    const visible=page.locator('#worker-figure img:visible');
    assert.equal(await visible.count(),1);
    assert.equal(await visible.getAttribute('src'),`worker-${mode}-wide.png`);
    await visible.scrollIntoViewIfNeeded(); await visible.evaluate(e=>e.decode());
    assert.deepEqual(await visible.evaluate(e=>[e.naturalWidth,e.naturalHeight]),[1800,1280]);
    const destination=process.env.TIMING_SCREENSHOT_DIR;
    if(destination){await mkdir(destination,{recursive:true});await visible.screenshot({path:path.join(destination,`worker-${mode}-wide.png`)});}
    await page.locator('#worker-figure').evaluate(e=>e.style.width='400px');
    assert.equal(await visible.getAttribute('src'),`worker-${mode}.png`);
    await page.locator('#worker-figure').evaluate(e=>e.style.removeProperty('width'));
    for(const suffix of ['','-wide'])for(const ext of ['png','svg']){
      const file=`worker-${mode}${suffix}.${ext}`;
      const response=await page.request.get(base+'/'+file);assert.equal(response.status(),200);
      assert.deepEqual(await response.body(),await readFile(path.join(root,file)));
    }
  }
});
