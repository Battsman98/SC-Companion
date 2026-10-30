"use strict";
const el = id => document.getElementById(id);
let baseline = null, current = null, downloadURL = null, activeRequest = null;
function paragraph(parent, value) {const p=document.createElement("p"); p.textContent=value; parent.append(p);}
function render() {
  if (!current) return;
  const changes=RSIInventory.preview(baseline,current);
  el("summary").replaceChildren(); el("records").replaceChildren();
  paragraph(el("summary"),`${current.packages.length} packages · ${current.items.length} ships, vehicles and CCUs. ${current.sync.complete ? "Owned hangar scan complete." : "Partial scan — do not treat unobserved items as missing."}`);
  paragraph(el("summary"),Object.entries(changes).filter(([,v])=>Array.isArray(v)).map(([k,v])=>`${k}: ${v.length}`).join(" · "));
  paragraph(el("summary"),"Counts include packages and children. Uncertain identities require manual review; nothing is deleted, reserved or reassigned.");
  for(const pkg of current.packages) {
    const card=document.createElement("article"), title=document.createElement("h2"); title.textContent=pkg.title; card.append(title);
    paragraph(card,`${pkg.ownership} · ${pkg.identity} identity · RSI pledge/melt value unknown · actual paid kept separately`);
    paragraph(card,"Insurance: "+(pkg.insurance.map(i=>i.label).join(", ") || "unknown"));
    const list=document.createElement("ul");
    for(const item of current.items.filter(i=>i.package_id===pkg.id)) {const li=document.createElement("li"); li.textContent=`${item.kind === "ccu" ? item.source_ship+" → "+item.target_ship : item.name} · ${item.kind} · ${item.identity} identity`; list.append(li);}
    card.append(list); el("records").append(card);
  }
  if(downloadURL) URL.revokeObjectURL(downloadURL);
  downloadURL=URL.createObjectURL(new Blob([JSON.stringify(current,null,2)],{type:"application/json"}));
  el("download").href=downloadURL; el("download").download="rsi-inventory.json"; el("download").hidden=false;
}
el("baseline").addEventListener("change",async event=>{
  try {
    const file=event.target.files[0]; if(!file) return;
    if(file.size>5000000) throw new Error("File too large");
    const data=JSON.parse(await file.text());
    if(data.schema_version!==1 || data.source!=="rsi-hangar" || !Array.isArray(data.packages) || !Array.isArray(data.items)) throw new Error("Unsupported inventory file");
    baseline=data; render(); el("status").textContent="Previous export loaded for comparison. Confirm that it belongs to the same RSI account.";
  } catch (_) {baseline=null; el("status").textContent="Could not read that version 1 inventory export.";}
});
chrome.runtime.onMessage.addListener(message=>{
  if(message.direction!=="from-game-assist-rsi-progress" || message.requestId!==activeRequest) return;
  const p=message.progress; el("status").textContent=`Read ${p.page} of ${p.totalPages} pages${p.failedPages ? `; ${p.failedPages} failed` : ""}.`;
});
el("scan").addEventListener("click",()=>{
  el("scan").disabled=true; el("status").textContent="Reading RSI hangar…"; activeRequest=crypto.randomUUID();
  chrome.runtime.sendMessage(JSON.stringify({action:"importInventory",requestId:activeRequest}),raw=>{
    el("scan").disabled=false;
    try {
      if(chrome.runtime.lastError) throw new Error("Extension request failed");
      const result=JSON.parse(raw);
      if(!result.inventory) {el("status").textContent=result.error || "No inventory returned."; return;}
      current=result.inventory; render();
      el("status").textContent=current.sync.complete ? "Ready to review. No planner changes have been applied." : "Partial results ready. Some pages failed or were not recognized; retry before reconciling missing inventory.";
    } catch (_) {el("status").textContent="Could not complete the scan. Reload the extension and try again.";}
  });
});
