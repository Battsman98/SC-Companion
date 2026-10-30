// Isolated UI smoke test with mock account endpoints; no live RSI/Discord access.
const {chromium}=require(process.env.CCU_PLAYWRIGHT_PATH || 'playwright');
const fs=require('node:fs');
(async()=>{
 const browser=await chromium.launch({headless:true,channel:process.env.CCU_BROWSER_CHANNEL || undefined});const page=await browser.newPage({viewport:{width:1280,height:1000}});
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 let plan={revision:0,ships:[],bases:[],upgrades:[],goals:[]}, saves=0, authenticated=false;
 fs.mkdirSync('output',{recursive:true});
 await page.route('http://localhost/**',async route=>{
  const req=route.request(),path=new URL(req.url()).pathname;
  if(path==='/api/me/ships')return route.fulfill({json:[{name:'Dragonfly Black',ownership_type:'pledged'},{name:'Loaner',ownership_type:'loaner'}]});
  if(path==='/api/me/ccu'){
   saves++;
   if(!authenticated)return route.fulfill({status:401,json:{detail:'Sign in required'}});
   if(req.method()==='PUT')plan={...req.postDataJSON(),revision:plan.revision+1};
   return route.fulfill({json:plan});
  }
  if(path==='/api/ccu/validate')return route.fulfill({json:req.postDataJSON()});
  if(path.startsWith('/api/ccu/routes/')){
   plan=req.postDataJSON();
   return route.fulfill({json:{routes:[{mode:'cash',base:plan.bases[0],steps:[{id:'',source:'Dragonfly Black',target:'Mako',status:'gap',cost:55}],additional:55,total:87.5,savings:7.5,missing:1}]}});
  }
  const html=fs.readFileSync('web/index.html','utf8').match(/<article class="panel ccu-planner"[\s\S]*?<\/article>/)[0];
  return route.fulfill({contentType:'text/html',body:`<html><head><meta charset="utf-8"><style>${fs.readFileSync('web/styles.css','utf8')}\n${fs.readFileSync('web/ccu-planner.css','utf8')}</style></head><body>${html}<script>${fs.readFileSync('web/ccu-planner.js','utf8')}</script></body></html>`});
 });
 await page.goto('http://localhost/');
 await page.locator('#ccuWorkspace:not([hidden])').waitFor();
 const ship=page.locator('form[data-ccu=ship]');
 for(const [name,value] of [['Dragonfly Black',40],['Mako',95]]){
  if(!await ship.evaluate(f=>f.closest('details').open))await page.getByText(/^1\. Ship values/).click();
  await ship.locator('[name=name]').fill(name);await ship.locator('[name=value]').fill(String(value));await ship.getByRole('button').click();await page.waitForFunction(()=>document.querySelector('#ccuNotice').textContent.startsWith('Draft updated'));
 }
 await page.getByText('2. Starting pledges (0)',{exact:true}).click();
 const base=page.locator('form[data-ccu=base]');await base.locator('[name=label]').fill('Pack ship 2');await base.locator('[name=cost]').fill('32.50');await base.locator('[name=lti]').check();await base.getByRole('button').click();
 await page.waitForFunction(()=>document.querySelector('select[name=base_id]').options.length===2);
 const goal=page.locator('form[data-ccu=goal]');await goal.locator('[name=target]').selectOption('Mako');await goal.getByRole('button').click();await page.getByRole('button',{name:'Find routes'}).waitFor();
 await page.getByRole('button',{name:'Find routes'}).click();await page.getByText('Lowest additional spending',{exact:true}).waitFor();
 await page.getByRole('button',{name:'Reserve this base and listed CCUs'}).click();await page.getByText(/Reserved in this draft/).waitFor();
 if(saves!==0)throw Error('Guest building accessed account persistence');
 await page.getByRole('button',{name:'Save to account'}).click();await page.getByText(/Your current draft is still here/).waitFor();
 await page.getByRole('button',{name:'Find routes'}).click();await page.getByText('Lowest additional spending',{exact:true}).waitFor();
 if(plan.goals[0].reserved_base!==plan.bases[0].id)throw Error('Guest reservation or failed-save draft lost');
 authenticated=true;
 await page.getByRole('button',{name:'Save to account'}).click();await page.getByText('Planner saved to your Discord account.',{exact:true}).waitFor();
 if(plan.goals.length!==1||plan.bases.length!==1||plan.ships.length!==2||saves!==2)throw Error('Explicit save failed');
 if(errors.length)throw Error(errors.join('\n'));
 await page.screenshot({path:'output/ccu-planner-desktop.png',fullPage:true});
 await page.setViewportSize({width:390,height:844});await page.screenshot({path:'output/ccu-planner-mobile.png',fullPage:true});
 if(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+2))throw Error('Mobile horizontal overflow');
 await browser.close();console.log('CCU UI: guest build, routes, reservation, failed-save draft retention, explicit account save and mobile layout passed.');
})().catch(e=>{console.error(e);process.exit(1)});


