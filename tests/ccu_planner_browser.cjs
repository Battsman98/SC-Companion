const {chromium}=require(process.env.CCU_PLAYWRIGHT_PATH || 'playwright');
const fs=require('node:fs');
(async()=>{
 const browser=await chromium.launch({headless:true,channel:process.env.CCU_BROWSER_CHANNEL || undefined});
 try {
 const page=await browser.newPage({viewport:{width:1280,height:1000}}), errors=[];
 page.on('pageerror',e=>errors.push(e.message));
 let plan, saves=0, authenticated=false;
 await page.route('http://localhost/**',async route=>{
  const req=route.request(),path=new URL(req.url()).pathname;
  if(path==='/api/me/ccu'){
   saves++;
   if(!authenticated)return route.fulfill({status:401,json:{detail:'Sign in required'}});
   plan={...req.postDataJSON(),revision:1};return route.fulfill({json:plan});
  }
  if(path==='/api/ccu/validate'){plan=req.postDataJSON();return route.fulfill({json:plan});}
  if(path.startsWith('/api/autocomplete/'))return route.fulfill({json:['Dragonfly Black','Mako']});
  if(path.startsWith('/api/ships/')){
   const name=decodeURIComponent(path.split('/').at(-1));
   return route.fulfill({json:{name,pledge:{currency:'USD',price:({'Mako':95,'Dragonfly Black':40})[name]||null}}});
  }
  if(path.startsWith('/api/ccu/routes/')){
   plan=req.postDataJSON();const goal=plan.goals.find(g=>g.id===path.split('/').at(-1));
   const target=plan.ships.find(s=>s.name===goal.target);
   return route.fulfill({json:{routes:target.value?[{mode:'cash',base:plan.bases[0]||{id:'suggested:0',ship:goal.target,label:'Suggested purchase: '+goal.target,status:'candidate',lti:false,cost:target.value},steps:[],additional:95,total:95,savings:0,missing:1}]:[]}});
  }
  const html=fs.readFileSync('web/index.html','utf8').match(/<article class="panel ccu-planner"[\s\S]*?<\/article>/)[0];
  return route.fulfill({contentType:'text/html',body:`<html><head><meta charset="utf-8"><style>${fs.readFileSync('web/styles.css','utf8')}\n${fs.readFileSync('web/ccu-planner.css','utf8')}</style></head><body>${html}<script>async function rsiConnect(){return {code:200,upgrades:[{id:'rsi-copy-1',source:'Dragonfly Black',target:'Mako'},{id:'rsi-copy-2',source:'Dragonfly Black',target:'Mako'}]};}</script><script>${fs.readFileSync('web/ccu-planner.js','utf8')}</script></body></html>`});
 });
 await page.goto('http://localhost/');
 const goal=page.locator('form[data-ccu=goal]');
 await goal.locator('[name=target]').fill('Mako');await goal.getByRole('button').click();
 await page.getByText('Standalone purchase baseline',{exact:false}).waitFor();
 if(plan.bases.length||plan.upgrades.length||plan.goals.length!==1||plan.ships[0].value!==95||saves)throw Error('Goal-only planning failed');
 await page.getByRole('button',{name:'Reserve this base and listed CCUs'}).click();
 await page.getByText(/Reserved in this draft/).waitFor();
 if(plan.bases.length!==1||plan.bases[0].status!=='candidate')throw Error('Suggested purchase reservation failed');
 await page.getByText('Owned CCUs (optional) · 0',{exact:true}).click();
 for(let i=0;i<2;i++){
  await page.getByRole('button',{name:'Import owned CCUs from RSI'}).click();
  await page.getByText(`Imported ${i===0?2:0} CCU copies.`,{exact:false}).waitFor();
 }
 if(plan.upgrades.length!==2||plan.upgrades.some(c=>c.cost!==null))throw Error('Import duplicates/costs failed');
 await goal.locator('[name=target]').fill('Unknown ship');await goal.getByRole('button').click();
 await page.getByText(/Your goal is added/).waitFor();
 if(plan.goals.length!==2)throw Error('Unknown price prevented drafting');
 await page.getByRole('button',{name:'Save to account'}).click();await page.getByText(/Your current draft is still here/).waitFor();
 authenticated=true;await page.getByRole('button',{name:'Save to account'}).click();await page.getByText('Planner saved to your Discord account.',{exact:true}).waitFor();
 if(plan.goals.length!==2||saves!==2)throw Error('Explicit persistence failed');
 if(errors.length)throw Error(errors.join('\n'));
 fs.mkdirSync('output',{recursive:true});
 await page.screenshot({path:'output/ccu-goal-first-desktop.png',fullPage:true});
 await page.setViewportSize({width:390,height:844});await page.screenshot({path:'output/ccu-goal-first-mobile.png',fullPage:true});
 if(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+2))throw Error('Mobile overflow');
 console.log('Goal-only planning, suggested reservation, optional CCU import, repeat import, unknown price, guest/account save and mobile passed.');
 } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exit(1)});
