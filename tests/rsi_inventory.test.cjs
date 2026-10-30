const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const root = path.resolve(__dirname,'..');
const I = require('../tools/rsi-connector-extension/inventory.js');
const fixture = fs.readFileSync(path.join(__dirname,'fixtures/rsi-hangar-observed.html'),'utf8');
const normalize = (html=fixture, complete=true, scan='scan') => I.normalize([{page:1,payload:html}],{complete,scanned_pages:[1],failed_pages:[],expected_pages:1,truncated:false},scan);
test('observed two-pack retains current children, insurance and package scope',()=>{
  const n=normalize(); assert.equal(n.packages.length,2); assert.equal(n.items.length,3);
  const ships=n.items.filter(x=>x.kind==='ship');
  assert.deepEqual(ships.map(x=>x.name),['Galaxy','Paladin']);
  assert.equal(ships[0].package_id,ships[1].package_id);
  assert.equal(n.packages[0].title,'Standalone Ships - Drake Dragonfly Ride Together Two-Pack');
  assert.deepEqual(ships[0].insurance.map(x=>x.label),['Lifetime Insurance','120 Month Insurance']);
  assert.equal(ships[0].identity,'uncertain'); assert.equal(ships[0].rsi_values.melt,null);
  assert.equal(n.items[2].id,'rsi-ccu:900001'); assert.equal(n.items[2].target_ship,'Hull C');
});
test('CCU copies survive reordering; scan IDs are never stable child identity',()=>{
  const extra=fixture.slice(fixture.lastIndexOf('<li>'),fixture.lastIndexOf('</ul>')).replaceAll('900001','900002');
  const a=normalize(fixture+extra), b=normalize(extra+fixture,true,'second');
  assert.deepEqual(a.items.filter(x=>x.kind==='ccu').map(x=>x.id).sort(), b.items.filter(x=>x.kind==='ccu').map(x=>x.id).sort());
  assert.equal(I.preview(a,b).missing.length,0); assert.equal(I.preview(a,b).added.length,0);
});
test('synthetic populated pledge ID is stable, child identity remains uncertain',()=>{
  const n=normalize(fixture.replace('class="js-pledge-id"','class="js-pledge-id" value="123"'));
  assert.equal(n.packages[0].id,'rsi-pledge:123'); assert.equal(n.items[0].identity,'uncertain');
});
test('duplicate IDs, missing identity and unknown status fail conservatively',()=>{
  const dupe=normalize(fixture+fixture); assert.equal(dupe.sync.complete,false);
  assert.ok(dupe.items.filter(x=>x.kind==='ccu').every(x=>x.identity==='uncertain'));
  const unknown=normalize(fixture.replaceAll('Attributed','Pending').replace('rel="900001"',''));
  assert.ok(unknown.items.every(x=>x.ownership==='unknown' && !x.upgrade_base_eligible));
  assert.equal(unknown.items[2].identity,'uncertain');
  assert.equal(normalize('<form>Sign in</form>').sync.complete,false);
});
test('preview and reconcile preserve manual data; missing does not delete',()=>{
  const old=normalize(); old.items[2].actual_paid={amount_minor:1234,currency:'USD'};
  old.items[2].protected=true; old.items[2].reservations=['goal-1'];
  const next=normalize(fixture.replaceAll('Hull C','Hull D'),true,'other');
  const r=I.reconcile(old,next); const ccu=r.inventory.items.find(x=>x.kind==='ccu');
  assert.equal(ccu.target_ship,'Hull D'); assert.equal(ccu.actual_paid.amount_minor,1234);
  assert.equal(ccu.protected,true); assert.deepEqual(ccu.reservations,['goal-1']);
  assert.equal(old.items[2].target_ship,'Hull C');
  assert.equal(r.preview.changed.length,2);
  const empty={...next,items:[],packages:[]};
  assert.equal(I.reconcile(old,empty).inventory.items.length,3);
  assert.equal(I.preview(old,empty).missing.length,2);
  empty.sync={complete:false}; assert.equal(I.preview(old,empty).missing.length,0);
});
test('scripts, cookies, coupon titles, URLs and markup never reach normalized data',()=>{
  const html=fixture+'<script>cookie=SECRET</script><li><div class="row"><div class="pledge-title-row"><h3>Coupon SECRET</h3></div></div></li>';
  const n=normalize(html); assert.ok(!JSON.stringify(n).includes('SECRET'));
  assert.ok(!JSON.stringify(n).includes('<div')); assert.ok(n.unsupported.includes('buybacks'));
});
test('ambiguous prior IDs cannot be silently matched or labeled new',()=>{
  const next=normalize(), old=normalize(); old.items.push({...old.items[2],actual_paid:17});
  const p=I.preview(old,next); assert.ok(p.uncertain.some(x=>x.reason==='ambiguous-prior-identity'));
  assert.equal(p.added.length,0); assert.equal(I.reconcile(old,next).inventory.items.length,4);
});
function background(fetch) {
  const ctx={RSIInventory:I,crypto:require('node:crypto').webcrypto,URL,AbortController,setTimeout,clearTimeout,fetch,chrome:{runtime:{onMessage:{addListener(){}}}}};
  vm.createContext(ctx); vm.runInContext(fs.readFileSync(path.join(root,'tools/rsi-connector-extension/background.js'),'utf8'),ctx);
  return ctx;
}
test('partial scan returns reviewable 206, legacy fails closed and progress finishes',async()=>{
  const ctx=background(async url=>({status:url.includes('page=2')?503:200,url,text:async()=>fixture+'<a href="?page=2">2</a>'}));
  const progress=[]; const n=await ctx.importHangar(p=>progress.push(p),false,true);
  assert.equal(n.code,206); assert.equal(n.inventory.sync.failed_pages.length,1); assert.equal(n.inventory.items.length,3);
  assert.equal(progress.at(-1).phase,'preview'); assert.equal(progress.at(-1).page,2);
  assert.equal((await ctx.importHangar(()=>{},true)).code,504);
});
test('legacy names/CCUs and new schema stay compatible; redirects fail closed',async()=>{
  const ctx=background(async url=>({status:200,url,text:async()=>fixture}));
  const ccus=await ctx.handleMessage('{"action":"importCCUs"}'); assert.equal(ccus.code,200);
  assert.equal(ccus.upgrades.length,1); assert.equal(ccus.upgrades[0].source,'600i Explorer');
  const ships=await ctx.handleMessage('{"action":"importHangar"}'); assert.deepEqual(Array.from(ships.candidates),['Galaxy','Paladin']);
  assert.equal((await ctx.handleMessage('{"action":"importInventory"}')).inventory.schema_version,1);
  const login=background(async()=>({status:200,url:'https://robertsspaceindustries.com/connect',text:async()=>fixture}));
  assert.equal((await login.importHangar()).code,401);
});
test('pagination cap and unrecognized HTML cannot claim complete inventory',async()=>{
  const ctx=background(async url=>({status:200,url,text:async()=>fixture.replace('rel="900001"',`rel="${url.match(/page=(\d+)/)?.[1] || 1}"`)+'<a href="?page=30">30</a>'}));
  const n=await ctx.importHangar(()=>{},false,true); assert.equal(n.code,206); assert.equal(n.inventory.sync.truncated,true);
  const bad=background(async url=>({status:200,url,text:async()=>'<h1>Maintenance</h1>'}));
  assert.equal((await bad.importHangar(()=>{},false,true)).code,206);
});
test('repeated page responses are partial rather than duplicate owned copies',async()=>{
  const ctx=background(async url=>({status:200,url,text:async()=>fixture+'<a href="?page=2">2</a>'}));
  const n=await ctx.importHangar(()=>{},false,true); assert.equal(n.code,206);
  assert.equal(n.inventory.sync.failed_pages[0].reason,'repeated-page-content'); assert.equal(n.inventory.items.length,3);
});
test('browser and extension shared helper copies match; manifests agree',()=>{
  assert.equal(fs.readFileSync(path.join(root,'web/rsi-inventory.js'),'utf8'),fs.readFileSync(path.join(root,'tools/rsi-connector-extension/inventory.js'),'utf8'));
  for(const name of ['manifest.json','manifest.firefox.json']) assert.equal(JSON.parse(fs.readFileSync(path.join(root,'tools/rsi-connector-extension',name))).version,'0.6.0');
});
test('local preview renders safe text and exports normalized data without applying changes',()=>{
  function element() {return {children:[],textContent:'',hidden:true,handlers:{},append(...nodes){this.children.push(...nodes);},replaceChildren(){this.children=[];},addEventListener(name,fn){this.handlers[name]=fn;}};}
  const elements=Object.fromEntries(['scan','baseline','status','download','summary','records'].map(id=>[id,element()]));
  let exported, progress;
  const inventory=normalize(); inventory.packages[0].title='<script>test</script>';
  const ctx={RSIInventory:I,crypto:require('node:crypto').webcrypto,Blob,URL:{createObjectURL(blob){exported=blob;return 'blob:local';},revokeObjectURL(){}},
    document:{getElementById:id=>elements[id],createElement:()=>element()},
    chrome:{runtime:{onMessage:{addListener(fn){progress=fn;}},sendMessage(raw,fn){
      assert.equal(JSON.parse(raw).action,'importInventory'); fn(JSON.stringify({code:200,inventory}));
    }}}};
  vm.createContext(ctx); vm.runInContext(fs.readFileSync(path.join(root,'tools/rsi-connector-extension/preview.js'),'utf8'),ctx);
  elements.scan.handlers.click(); assert.equal(elements.scan.disabled,false);
  assert.equal(elements.records.children.length,2); assert.equal(elements.records.children[0].children[0].textContent,'<script>test</script>');
  assert.match(elements.status.textContent,/No planner changes/); assert.equal(elements.download.hidden,false); assert.ok(exported instanceof Blob);
  const status=elements.status.textContent; progress({direction:'from-game-assist-rsi-progress',requestId:'unrelated',progress:{page:99,totalPages:100}});
  assert.equal(elements.status.textContent,status);
});
