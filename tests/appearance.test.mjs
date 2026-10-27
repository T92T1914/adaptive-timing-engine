import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import {test} from 'node:test';

const script=readFileSync(new URL('../adaptive_timing/appearance.js',import.meta.url),'utf8');
function run(saved, blocked=false) {
  const events=new Map(), root={dataset:{}}, state={saved, notifications:0};
  const control={disabled:true,addEventListener:(name,fn)=>events.set(name,fn)};
  const document={documentElement:root,getElementById:()=>control,
    addEventListener:(name,fn)=>events.set(name,fn),dispatchEvent:()=>state.notifications++};
  const store={getItem:()=>{if(blocked)throw Error('blocked');return state.saved;},
    setItem:(_,value)=>{if(blocked)throw Error('blocked');state.saved=value;},
    removeItem:()=>{if(blocked)throw Error('blocked');state.saved=null;}};
  vm.runInNewContext(script,{document,localStorage:store,Event:class{}});
  return {root,control,state,ready:()=>events.get('DOMContentLoaded')(),
    choose:value=>{control.value=value;events.get('change')();}};
}
for(const saved of [null,'invalid']) test(`unrecognized or absent preference ${saved} uses Obscur before paint`,()=>{
  const f=run(saved);assert.equal(f.root.dataset.appearance,'obscur');f.ready();assert.equal(f.control.disabled,false);
});
test('an explicit Auto choice survives a fresh document',()=>{
  const f=run('clair');f.ready();f.choose('auto');
  assert.equal(f.state.saved,'auto');assert.equal(run(f.state.saved).root.dataset.appearance,'auto');
});
test('explicit saved mode is resolved before DOM readiness',()=>{
  const f=run('obscur');assert.equal(f.root.dataset.appearance,'obscur');f.ready();assert.equal(f.control.value,'obscur');
  f.choose('clair');assert.equal(f.state.saved,'clair');assert.equal(f.state.notifications,1);
  f.choose('auto');assert.equal(f.state.saved,'auto');assert.equal(f.root.dataset.appearance,'auto');
});
test('blocked reads and writes do not prevent in-memory switching',()=>{
  const f=run('clair',true);f.ready();assert.equal(f.root.dataset.appearance,'obscur');f.choose('clair');
  assert.equal(f.root.dataset.appearance,'clair');assert.equal(f.state.notifications,1);
});
