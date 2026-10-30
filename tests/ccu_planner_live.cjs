// Exercise the full deployed page, including its actual versioned asset URL.
const {chromium}=require(process.env.CCU_PLAYWRIGHT_PATH || 'playwright');
(async()=>{
 const browser=await chromium.launch({headless:true,channel:process.env.CCU_BROWSER_CHANNEL || undefined});
 try {
  const page=await browser.newPage();
  const errors=[];page.on('pageerror',error=>errors.push(error.message));
  await page.goto(process.env.CCU_LIVE_URL || 'https://sccompanion.org/',{waitUntil:'domcontentloaded'});
  await page.waitForFunction(()=>document.querySelector('#ccuWorkspace form[data-ccu=ship]'),{},{timeout:20000});
  await page.getByRole('button',{name:'Ships',exact:true}).click();
  await page.getByRole('button',{name:'CCU Planner',exact:true}).click();
  const form=page.locator('#ccuWorkspace form[data-ccu=ship]');
  await form.locator('[name=name]').fill('Guest smoke test');
  await form.locator('[name=value]').fill('40');
  await form.getByRole('button').click();
  await page.getByText('Guest smoke test: $40.00',{exact:true}).waitFor();
  if(errors.length)throw Error(errors.join('\n'));
  console.log('Live full page: guest controls visible and ship entry works without sign-in.');
 } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exit(1)});
