const {parse}=require('../web/hangarlink-csv.js');
const assert=require('node:assert/strict');
const header=['Pilot Type','PledgeId','Name','Value','ConfigurationValue','Date','Page','Pagesize','Contains','AlsoContains','Items','PageUrl','BuybackUrl'];
const csv=rows=>[header,...rows].map(row=>row.map(v=>'"'+String(v).replaceAll('"','""')+'"').join(',')).join('\r\n');
const pledge=(id,name,items='',insurance='')=>['PLEDGE',id,name,'99999','88888','2026-10-30',1,10,'',insurance,items,'https://example.invalid/private',''];
(async()=>{
 const text='\ufeff'+csv([
  pledge('1','Pack, "two ships"\nLTI','[KIND Ship][TITLE Dragonfly Black],[KIND Ship][TITLE Dragonfly Black]','[TITLE Lifetime Insurance]'),
  pledge('2','Upgrade - Dragonfly Black to Mako - Warbond Edition'),
  ['BUYBACK','3','Standalone Ship - Mako','','','',1,10,'','','','',''],
  pledge('4','Paint - Mako','[KIND Paint][TITLE Mako]')
 ]);
 const result=await parse(text);
 assert.equal(result.items.length,3);assert.equal(new Set(result.items.map(x=>x.id)).size,3);
 assert.equal(result.items[2].source_ship,'Dragonfly Black');assert.equal(result.items[2].target_ship,'Mako');
 assert.equal(result.items[0].insurance[0].label,'Lifetime Insurance');assert.equal(result.sync.complete,false);
 assert.equal(result.unsupported.length,2);assert(result.items.every(x=>x.identity==='uncertain'&&!('cost' in x)));
 assert(!JSON.stringify(result).includes('example.invalid'));assert(!JSON.stringify(result).includes('99999'));
 assert.deepEqual((await parse(text)).items,result.items);
 await assert.rejects(()=>parse(csv([pledge('1','Upgrade - X to Y'),pledge('1','Upgrade - X to Z')])),/repeated PledgeId/);
 await assert.rejects(()=>parse('"unclosed'),/quoted field/);
 await assert.rejects(()=>parse('a,b\n1,2'),/Expected/);
 await assert.rejects(()=>parse(csv([pledge('1','Paint')])),/No supported/);
 await assert.rejects(()=>parse(csv([pledge('1','Upgrade - X to Y')])+',extra'),/columns/);
 console.log('HangarLink CSV: quoted fields, packages, CCUs, skipped buybacks, identities and invalid files passed.');
})().catch(e=>{console.error(e);process.exit(1)});
