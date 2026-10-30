const {chromium}=require(process.env.CCU_PLAYWRIGHT_PATH || 'playwright');
const fs=require('node:fs');
const {execFileSync}=require('node:child_process');
let fixtureMarket={offers:[],historical:[]};
function backend(body,goal){
 return JSON.parse(execFileSync(process.env.CCU_PYTHON||'python',['-c','import json,sys; from src.ccu_planner import Planner,find_routes; d=json.load(sys.stdin); p=Planner.model_validate(d["plan"]); print(json.dumps({"routes":find_routes(p,d["goal"],d["market"])} if d["goal"] else p.model_dump()))'],{input:JSON.stringify({plan:body,goal,market:fixtureMarket}),encoding:'utf8',env:{...process.env,PYTHONIOENCODING:'utf-8'}}));
}
(async()=>{
 const browser=await chromium.launch({headless:true,channel:process.env.CCU_BROWSER_CHANNEL || undefined});
 try {
 const page=await browser.newPage({viewport:{width:1280,height:1000}}), errors=[];
 page.on('pageerror',e=>errors.push(e.message));
 let plan, saves=0, authenticated=false;
 await page.route('http://localhost/**',async route=>{
  const req=route.request(),path=new URL(req.url()).pathname;
  if(path==='/api/ccu/deals')return route.fulfill({json:fixtureMarket});
  if(path==='/api/me/ccu'){
   saves++;
   if(!authenticated)return route.fulfill({status:401,json:{detail:'Sign in required'}});
   plan={...req.postDataJSON(),revision:1};return route.fulfill({json:plan});
  }
  if(path==='/api/ccu/validate'){plan=backend(req.postDataJSON(),null);return route.fulfill({json:plan});}
  if(path.startsWith('/api/autocomplete/'))return route.fulfill({json:['Dragonfly Black','Mako']});
  if(path.startsWith('/api/ships/')){
   const name=decodeURIComponent(path.split('/').at(-1));
   return route.fulfill({json:{name,pledge:{currency:'USD',price:({'Mako':95,'Dragonfly Black':40})[name]||null}}});
  }
  if(path.startsWith('/api/ccu/routes/')){
   plan=req.postDataJSON();const goal=plan.goals.find(g=>g.id===path.split('/').at(-1));
   const target=plan.ships.find(s=>s.name===goal.target);
   return route.fulfill({json:backend(plan,goal.id)});
  }
  const html=fs.readFileSync('web/index.html','utf8').match(/<article class="panel ccu-planner"[\s\S]*?<\/article>/)[0];
  return route.fulfill({contentType:'text/html',body:`<html><head><meta charset="utf-8"><style>${fs.readFileSync('web/styles.css','utf8')}\n${fs.readFileSync('web/ccu-planner.css','utf8')}</style></head><body>${html}<script>async function rsiConnect(){return {code:200,upgrades:[{id:'rsi-copy-1',source:'Dragonfly Black',target:'Mako'},{id:'rsi-copy-2',source:'Dragonfly Black',target:'Mako'}]};}</script><script>${fs.readFileSync('web/ccu-planner.js','utf8')}</script></body></html>`});
 });
 await page.goto('http://localhost/');
 const goal=page.locator('form[data-ccu=goal]');
 let checkedCollapsed=false;
 async function openComparisons(){
  await page.waitForFunction(()=>!document.querySelector('#ccuPlanner').hasAttribute('aria-busy'));
  const cards=page.locator('[data-results] > details');
  if(!checkedCollapsed && await cards.count()){
   if(await page.locator('[data-results] > details[open]').count())throw Error('Routes should start collapsed');
   checkedCollapsed=true;
  }
  const closed=page.locator('[data-results] > details:not([open]) > summary');
  while(await closed.count())await closed.first().click();
 }

 await goal.locator('[name=target]').fill('Mako');
 await goal.locator('[name=base_ship]').fill('Dragonfly Black');
 await goal.locator('[name=lti_only]').check();
 await page.getByText('Warbond savings',{exact:true}).click();
 await page.getByRole('button',{name:'Review deal catalogue',exact:true}).click();
 await page.getByText('Deal catalogue refreshed.',{exact:false}).waitFor();
 await page.getByRole('button',{name:'Apply deal preferences',exact:true}).click();
 await openComparisons();
 await page.waitForFunction(()=>!document.querySelector('#ccuPlanner').hasAttribute('aria-busy'));
 if(await goal.locator('[name=target]').inputValue()!=='Mako'||await goal.locator('[name=base_ship]').inputValue()!=='Dragonfly Black'||!await goal.locator('[name=lti_only]').isChecked())throw Error('Option update erased goal draft');
 await goal.locator('[name=base_ship]').fill('');await goal.locator('[name=lti_only]').uncheck();
 await page.getByText('Warbond savings',{exact:true}).click();
 await goal.getByRole('button').click();
 await openComparisons();
 await page.getByText('Standalone purchase baseline',{exact:false}).first().waitFor();
 if(plan.bases.length||plan.upgrades.length||plan.goals.length!==1||plan.ships[0].value!==95||saves)throw Error('Goal-only planning failed');
 await page.getByRole('button',{name:'Reserve this base and listed CCUs'}).first().click();
 await page.getByText(/Reserved in this draft/).waitFor();
 if(plan.bases.length!==1||plan.bases[0].status!=='candidate')throw Error('Suggested purchase reservation failed');
 await page.getByText('Owned CCUs (optional) · 0',{exact:true}).click();
 for(let i=0;i<2;i++){
  await page.getByRole('button',{name:'Import owned CCUs from RSI'}).click();
  await page.getByText('Review inventory import',{exact:true}).waitFor();
  await page.locator('#ccuSameAccount').check();
  for(const select of await page.locator('[data-import-row]').all())await select.selectOption({index:1});
  await page.getByRole('button',{name:'Apply reviewed import'}).click();
  await page.getByText(`Imported ${i===0?2:0} copies;`,{exact:false}).waitFor();
 }
 if(plan.upgrades.length!==2||plan.upgrades.some(c=>c.cost!==null))throw Error('Import duplicates/costs failed');
 // A guest can use a copy, protect the other, replace a bridge, and reserve without account writes.
 const copy=plan.upgrades[0].id;
 await page.locator(`[data-protect=upgrades][data-id="${copy}"]`).click();
 await page.getByText('Copy protected.',{exact:false}).waitFor();
 await goal.locator('[name=target]').fill('Mako');
 await goal.locator('[name=base_ship]').fill('Dragonfly Black');
 await goal.getByRole('button').click();
 await openComparisons();
 await page.locator('.ccu-goals article').nth(1).locator('.ccu-steps li').first().waitFor();
 let chain=page.locator('.ccu-goals article').nth(1);
 await chain.locator('[data-constraint=lock]').first().click();
 await openComparisons();
 await chain.locator('[data-constraint=unlock]').first().waitFor();
 if(plan.goals[1].locked_steps[0]===`ccu:${copy}`)throw Error('Protected copy selected');
 await chain.locator('[data-constraint=exclude]').first().click();
 await openComparisons();
 await chain.getByText('Missing · theoretical bridge, availability unverified',{exact:false}).first().waitFor();
 if(plan.goals[1].excluded_steps.length!==1)throw Error('Exclusion lost');
 await chain.locator('[data-reset-steps]').click();
 await openComparisons();
 await chain.locator('[data-constraint=lock]').first().waitFor();
 await chain.locator('.ccu-steps details summary').first().click();
 await chain.locator('.ccu-steps select').first().selectOption('gap:Dragonfly Black→Mako');
 await chain.getByRole('button',{name:'Use alternative',exact:true}).first().click();
 await openComparisons();
 await chain.getByText('Missing · theoretical bridge, availability unverified',{exact:false}).first().waitFor({timeout:5000}).catch(async e=>{throw Error(e.message+' Notice: '+await page.locator('#ccuNotice').textContent()+' Goal: '+JSON.stringify(plan.goals[1]));});
 if(!plan.goals[1].locked_steps.includes('gap:Dragonfly Black→Mako'))throw Error('Replacement not locked');
 await chain.locator('[data-reset-steps]').click();
 await openComparisons();
 await chain.getByRole('button',{name:'Reserve this base and listed CCUs'}).first().click();
 await page.getByText(/Reserved in this draft/).waitFor();
 if(plan.goals[1].reserved_ccus.length!==1)throw Error('Copy reservation missing');
 // Re-import must preserve protection, manually paid cost and the reservation.
 await page.getByRole('button',{name:'Import owned CCUs from RSI'}).click();
 await page.locator('#ccuSameAccount').check();
 for(const select of await page.locator('[data-import-row]').all())await select.selectOption({index:1});
 await page.getByRole('button',{name:'Apply reviewed import'}).click();
 await page.getByText('Imported 0 copies;',{exact:false}).waitFor();
 if(!plan.upgrades[0].protected||plan.goals[1].reserved_ccus.length!==1)throw Error('Re-import lost user state');
 await goal.locator('[name=target]').fill('Unknown ship');await goal.getByRole('button').click();
 await openComparisons();
 await page.getByText(/Your goal is added/).waitFor();
 if(plan.goals.length!==3)throw Error('Unknown price prevented drafting');
 await page.getByRole('button',{name:'Save to account'}).click();await page.getByText(/Your current draft is still here/).waitFor();
 authenticated=true;await page.getByRole('button',{name:'Save to account'}).click();await page.getByText('Planner saved to your Discord account.',{exact:true}).waitFor();
 if(plan.goals.length!==3||saves!==2)throw Error('Explicit persistence failed');
 await page.goto('http://localhost/structured');
 const inventory={schema_version:1,source:'rsi-hangar',sync:{complete:true},packages:[{id:'pack',title:'Two-pack',rsi_values:{pledge:{amount_minor:10000,currency:'USD'}}}],items:[
  ...[1,2].map(i=>({id:'rsi-ccu:'+i,kind:'ccu',identity:'stable',ownership:'owned',source_ship:'Dragonfly Black',target_ship:'Mako',insurance:[{label:'120 months'}]})),
  ...['Dragonfly Black','Mako'].map((name,i)=>({id:'observation:'+i,kind:'ship',name,identity:'uncertain',ownership:'owned',upgrade_base_eligible:true,package_id:'pack',insurance:[{label:'Lifetime Insurance'}]})),
  {id:'loaner',kind:'ship',name:'Mako',identity:'uncertain',ownership:'loaner',upgrade_base_eligible:false}
 ]};
 await page.evaluate(value=>{window.inventoryResponse={code:200,inventory:value};window.rsiConnect=async()=>window.inventoryResponse;},inventory);
 await page.getByText('Owned CCUs (optional) · 0',{exact:true}).click();
 async function reviewedImport(selectChildren=false){
  await page.getByRole('button',{name:'Import owned CCUs from RSI'}).click();
  await page.getByText('Review inventory import',{exact:true}).waitFor();
  await page.locator('#ccuSameAccount').check();
  if(selectChildren)for(const i of [2,3])await page.locator(`[data-import-row="${i}"]`).selectOption('new');
  if(await page.locator('[data-import-row="4"]').count()){
   if(await page.locator('[data-import-row="4"] option').count()!==1)throw Error('Loaner offered as base');
  }
  await page.getByRole('button',{name:'Apply reviewed import'}).click();
  await page.getByText(/Imported \d+ copies; refreshed/).waitFor();
 }
 await reviewedImport(true);
 if(plan.bases.length!==2||plan.bases.some(b=>b.cost!==null||b.package_value!==100))throw Error('Package accounting lost');
 const stable=plan.upgrades[0].id;
 await page.locator(`[data-protect=upgrades][data-id="${stable}"]`).click();
 await page.getByText('Copy protected.',{exact:false}).waitFor();
 await page.locator(`[data-edit=upgrades][data-id="${stable}"]`).click();
 await page.locator('form[data-ccu=upgrade] [name=cost]').fill('12.50');
 await page.locator('form[data-ccu=upgrade]').getByRole('button').click();
 await page.getByText('Draft updated for this page session.',{exact:false}).waitFor();
 await goal.locator('[name=target]').fill('Mako');
 await goal.locator('[name=base_id]').selectOption(plan.bases.find(b=>b.ship==='Dragonfly Black').id);
 await goal.getByRole('button').click();
 await openComparisons();
 await page.getByRole('button',{name:'Reserve this base and listed CCUs'}).first().click();
 await page.getByText(/Reserved in this draft/).waitFor();
 const reserved=plan.goals[0].reserved_ccus[0];
 if(reserved===stable||!reserved)throw Error('Protection not honored');
 await reviewedImport();
 if(plan.upgrades.length!==2||plan.bases.length!==2||plan.upgrades.find(c=>c.id===stable).cost!==12.5||!plan.upgrades.find(c=>c.id===stable).protected||plan.goals[0].reserved_ccus[0]!==reserved)throw Error('Structured re-import lost edits or duplicated rows');
 await page.evaluate(()=>{window.inventoryResponse.inventory.items=window.inventoryResponse.inventory.items.filter(x=>x.id!=='rsi-ccu:2');window.inventoryResponse.code=206;window.inventoryResponse.inventory.sync.complete=false;});
 await reviewedImport();
 if(plan.upgrades.find(x=>x.id===reserved).import_missing)throw Error('Partial scan marked missing');
 await page.evaluate(()=>{window.inventoryResponse.code=200;window.inventoryResponse.inventory.sync.complete=true;});
 await reviewedImport();
 if(!plan.upgrades.find(x=>x.id===reserved).import_missing||plan.goals[0].reserved_ccus[0]!==reserved)throw Error('Complete missing scan deleted or released a copy');
 await page.locator('[data-find]').first().click();
 await openComparisons();
 await page.locator('.ccu-steps li').first().waitFor();
 if(errors.length)throw Error(errors.join('\n'));
 page.on('dialog',dialog=>dialog.accept());
 await page.goto('http://localhost/warbonds');
 fixtureMarket={fresh:true,checked_at:'2026-10-30',warning:'',catalog:[{name:'Dragonfly Black',value:40},{name:'Mid',value:70},{name:'Mako',value:95}],offers:[{name:'Mid',sku_id:1,warbond_price:60,standard_price:70},{name:'Mako',sku_id:2,warbond_price:85,standard_price:95}],historical:[{id:'history-fixture',source:'Dragonfly Black',target:'Mako',reference_cost:10,observed_at:'2025-01-01'}]};
 await goal.locator('[name=target]').fill('Mako');await goal.getByRole('button').click();
 await openComparisons();
 await page.getByText('Active warbond · new cash required',{exact:false}).first().waitFor();
 if(plan.include_historical)throw Error('History enabled by default');
 await page.getByText('Warbond savings',{exact:true}).click();
 await page.getByRole('button',{name:'Review deal catalogue',exact:true}).click();
 await page.getByText('Deal catalogue refreshed.',{exact:false}).waitFor();
 await page.locator('form[data-ccu=deals] [name=include_historical]').check();
 await page.getByRole('button',{name:'Apply deal preferences',exact:true}).click();
 await openComparisons();
 await page.getByText('Historical warbond · availability unconfirmed',{exact:false}).first().waitFor();
 await page.locator('[data-acquisition-quote]').first().fill('20');
 await page.getByRole('button',{name:'Update acquisition quote',exact:true}).first().click();
 await openComparisons();
 await page.getByText('Your acquisition quote',{exact:false}).first().waitFor();
 if(plan.deal_quotes['historical:history-fixture']!==20)throw Error('Quote not retained');
 const quoted=backend(plan,plan.goals[0].id).routes[0];
 if(quoted.additional!==60||quoted.hypothetical)throw Error('Quote did not replace reference price');
 await page.locator('form[data-ccu=deals] [name=include_historical]').uncheck();
 await page.getByRole('button',{name:'Apply deal preferences',exact:true}).click();
 await openComparisons();
 await page.getByText('Active warbond · new cash required',{exact:false}).first().waitFor();
 if(await page.locator('[data-acquisition-quote]').count())throw Error('Historical deal used after opt-out');
 await page.getByRole('button',{name:'Reserve this base and listed CCUs'}).first().click();
 await page.getByText(/Reserved in this draft/).waitFor();
 if(plan.goals[0].reserved_ccus.length)throw Error('Market offers reserved as owned copies');
 await page.locator('[data-edit="goals"]').first().click();
 const editingId=await goal.getAttribute('data-edit-id');
 await page.getByRole('button',{name:'Apply deal preferences',exact:true}).click();
 await openComparisons();
 await page.waitForFunction(()=>!document.querySelector('#ccuPlanner').hasAttribute('aria-busy'));
 if(await goal.getAttribute('data-edit-id')!==editingId||await goal.locator('[name=target]').inputValue()!=='Mako')throw Error('Option update lost goal editing state');
 await goal.getByRole('button',{name:'Save changes',exact:true}).click();
 await openComparisons();
 await page.waitForFunction(()=>!document.querySelector('#ccuPlanner').hasAttribute('aria-busy'));
 if(plan.goals.length!==1)throw Error('Preserved edit created duplicate goal');
 await page.locator('[data-find]').first().click();
 await openComparisons();
 await page.getByText('Active warbond · new cash required',{exact:false}).first().waitFor();
 if(errors.length)throw Error(errors.join('\n'));
 fs.mkdirSync('output',{recursive:true});
 await page.screenshot({path:'output/ccu-goal-first-desktop.png',fullPage:true});
 await page.setViewportSize({width:390,height:844});await page.screenshot({path:'output/ccu-goal-first-mobile.png',fullPage:true});
 if(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+2))throw Error('Mobile overflow');
 console.log('Actual Python routes: guest goal-only, locks/exclusions/replacements, per-copy protection/reservations, legacy and structured imports, package costs, loaners, partial/missing scans, account save and mobile passed.');
 } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exit(1)});
