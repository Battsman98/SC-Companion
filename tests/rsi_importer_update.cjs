const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync('web/app.js','utf8');
const part=source.slice(source.indexOf('function rsiImporterUpdateLabel('),source.indexOf('function connectorInstallPrompt('));
let handler, response, fail=false, latest="0.6.1", metadataOK=true;
const panel={hidden:true,open:true,label:{},querySelector(){return this.label;}};
const ctx={fetch:async()=>({ok:metadataOK,json:async()=>({version:latest,chrome:`/assets/rsi-connector-extension-v${latest}.zip`,firefox:`/assets/rsi-connector-extension-firefox-v${latest}.zip`})}),document:{querySelector:()=>panel,querySelectorAll:()=>[]},window:{addEventListener:(name,fn)=>handler=fn},rsiConnect:async()=>{if(fail)throw Error('No extension');return response;}};
vm.createContext(ctx);vm.runInContext(part,ctx);
(async()=>{
for(const v of ['0.6.1','0.6.1.0','0.6.2','0.10.0','1.0.0']){response={code:200,version:v};await ctx.refreshRsiImporterUpdate();assert.equal(panel.hidden,true,v);}
for(const v of ['0.4.9','0.5.0','0.6.0']){response={code:200,version:v};await ctx.refreshRsiImporterUpdate();assert.equal(panel.hidden,false);assert.match(panel.label.textContent,/Update importer/);}
fail=true;await ctx.refreshRsiImporterUpdate();assert.match(panel.label.textContent,/^Install importer/);
fail=false;response={code:200};await ctx.refreshRsiImporterUpdate();assert.match(panel.label.textContent,/^Check importer/);
response={code:200,version:'0.6.1'};await handler();await new Promise(r=>setTimeout(r,0));assert.equal(panel.hidden,true);
latest='0.7.0';response={code:200,version:'0.6.1'};await ctx.refreshRsiImporterUpdate();assert.equal(panel.hidden,false);assert.match(panel.label.textContent,/0.7.0 available/);
latest='1.0.0';response={code:200,version:'1.0.0'};await ctx.refreshRsiImporterUpdate();assert.equal(panel.hidden,true);
metadataOK=false;await ctx.refreshRsiImporterUpdate();assert.equal(panel.hidden,true);
console.log('Importer version visibility: older, current, newer, missing, unknown and focus refresh passed.');
})();
