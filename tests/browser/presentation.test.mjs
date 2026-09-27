import assert from 'node:assert/strict';
import {before,after,test} from 'node:test';
import {createServer} from 'node:http';
import {readFile,mkdir,mkdtemp,rm} from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {gunzipSync} from 'node:zlib';
import {chromium} from 'playwright';

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
  browser=await chromium.launch({headless:true,chromiumSandbox:true,...(channel?{channel}:{})});
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
const visibleState=page=>page.evaluate(()=>({scenario:document.querySelector('#scenario').value,
  variant:document.querySelector('#variant').value,seed:document.querySelector('#seed').value,
  start:document.querySelector('#start').value,span:document.querySelector('#span').value,
  metrics:document.querySelector('.metrics').textContent,rows:document.querySelector('#rows').textContent}));

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
    await page.waitForFunction(mode=>document.querySelector('.worker-'+mode).naturalWidth===960,mode);
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
