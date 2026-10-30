const fs=require('fs'),vm=require('vm'),assert=require('node:assert/strict');
const context={chrome:{runtime:{onMessage:{addListener(){}}}},AbortController,setTimeout,clearTimeout};
vm.createContext(context);vm.runInContext(fs.readFileSync('tools/rsi-connector-extension/background.js','utf8'),context);
const fixture='<span class="js-pledge-name">Upgrade - Dragonfly Black to Mako</span><span class="js-pledge-name">Upgrade - Dragonfly Black to Mako</span><span class="js-pledge-name">Upgrade - Mako to Cutlass Black - Warbond Edition</span><span class="js-pledge-name">Paint - Mako</span><a href="/upgrade">Upgrade - Wrong to Wrong</a>';
const result=JSON.parse(JSON.stringify(context.extractCCUs(fixture)));
assert.equal(result.length,3);assert.deepEqual(result[2],{source:'Mako',target:'Cutlass Black'});
context.rsiHTMLGet=async()=>({code:200,url:'https://robertsspaceindustries.com/account/pledges',payload:fixture});
(async()=>{const r=await context.importHangar(()=>{},true);assert.equal(r.code,200);assert.equal(r.upgrades.length,3);assert.notEqual(r.upgrades[0].id,r.upgrades[1].id);const again=await context.importHangar(()=>{},true);assert.equal(r.upgrades[0].id,again.upgrades[0].id);assert.equal(r.upgrades[0].cost,undefined);console.log('CCU importer: explicit titles, separate copies, repeat import IDs, no invented costs passed.');})().catch(e=>{console.error(e);process.exit(1)});
