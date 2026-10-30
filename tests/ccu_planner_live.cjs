// Exercise the full deployed page, including its actual versioned asset URL.
const {chromium}=require(process.env.CCU_PLAYWRIGHT_PATH || 'playwright');
(async()=>{
 const browser=await chromium.launch({headless:true,channel:process.env.CCU_BROWSER_CHANNEL || undefined});
 try {
  const page=await browser.newPage();
  const errors=[];page.on('pageerror',error=>errors.push(error.message));
  await page.goto(process.env.CCU_LIVE_URL || 'https://sccompanion.org/',{waitUntil:'domcontentloaded'});
  await page.waitForFunction(()=>document.querySelector('#ccuWorkspace form[data-ccu=goal]'),{},{timeout:20000});
  await page.locator('button[data-tab="lookup"]').click();
  await page.getByRole('button',{name:'CCU Planner',exact:true}).click();
  const form=page.locator('#ccuWorkspace form[data-ccu=goal]');
  await form.locator('[name=target]').fill('Perseus');
  await form.getByRole('button').click();
  await page.locator('.ccu-goals h3').filter({hasText:'Perseus'}).waitFor({timeout:30000});
  if(errors.length)throw Error(errors.join('\n'));
  console.log('Live full page: guest controls visible and goal-only planning works without sign-in.');
 } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exit(1)});
