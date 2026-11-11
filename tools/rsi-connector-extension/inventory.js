/* Shared, side-effect-free inventory adapter. No network, storage, or DOM execution. */
(function (root) {
  "use strict";
  const decode = value => String(value || "").replace(/&(?:amp|quot|apos|lt|gt|nbsp);|&#(?:x[\da-f]+|\d+);/gi, entity => {
    const named = {"&amp;":"&", "&quot;":'"', "&apos;":"'", "&lt;":"<", "&gt;":">", "&nbsp;":" "};
    if (named[entity.toLowerCase()]) return named[entity.toLowerCase()];
    const hex = /^&#x/i.test(entity), n = parseInt(entity.slice(hex ? 3 : 2, -1), hex ? 16 : 10);
    return n > 0 && n <= 0x10ffff ? String.fromCodePoint(n) : "";
  });
  // Small inert tree reader, usable in an MV3 worker (which has no DOMParser).
  // It ignores scripts/comments and never executes markup or accesses resources.
  function tree(html) {
    const root = {tag:"root", attrs:{}, children:[]}, stack = [root];
    const inert = String(html).replace(/<!--[\s\S]*?-->|<(script|style)\b[^>]*>[\s\S]*?<\/\1\s*>/gi, "");
    const tokens = inert.match(/<[^>"']*(?:"[^"]*"|'[^']*')[^>]*>|<[^>]*>|[^<]+/g) || [];
    for (const token of tokens) {
      if (token.startsWith("</")) {
        const tag = token.match(/^<\/\s*([\w-]+)/)?.[1]?.toLowerCase();
        for (let i = stack.length - 1; i > 0; i--) if (stack[i].tag === tag) { stack.length = i; break; }
      } else if (/^<[a-z]/i.test(token)) {
        const tag = token.match(/^<([\w-]+)/)[1].toLowerCase(), attrs = {};
        for (const a of token.slice(tag.length + 1).matchAll(/([\w:-]+)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))/g)) attrs[a[1].toLowerCase()] = decode(a[2] ?? a[3] ?? a[4]);
        const node = {tag, attrs, children:[]}; stack.at(-1).children.push(node);
        if (!/^(input|img|br|hr|meta|link|source|area|base|embed|wbr)$/.test(tag) && !token.endsWith("/>")) stack.push(node);
      } else if (!token.startsWith("<")) stack.at(-1).children.push(decode(token));
    }
    return root;
  }
  const has = (n, cls) => (n.attrs?.class || "").split(/\s+/).includes(cls);
  const all = (n, predicate) => n.children.flatMap(c => typeof c === "string" ? [] : [...(predicate(c) ? [c] : []), ...all(c, predicate)]);
  const first = (n, cls) => all(n, c => has(c, cls))[0];
  const text = n => !n ? "" : typeof n === "string" ? n : n.children.map(text).join(" ").replace(/\s+/g, " ").trim();
  const idValue = value => /^\d{1,30}$/.test(value || "") ? value : null;
  function endpoints(title) {
    const m = title.match(/^(?:Ship\s+)?Upgrade\s*[-:–]\s*(.+?)\s+to\s+(.+?)(?:\s*[-–]\s*Warbond.*)?$/i);
    if (!m) return null;
    const source = m[1].trim(), target = m[2].replace(/\s*[-–]?\s*Standard Edition\s*$/i, "").trim();
    return source && target && source !== target ? {source, target} : null;
  }
  function parsePage(html, page, scanId) {
    const doc = tree(html), packages = [], items = [], warnings = [];
    const rows = all(doc, n => has(n, "row") && !!first(n, "pledge-title-row"));
    for (const [index, row] of rows.entries()) {
      const heading = first(row, "pledge-title-row");
      const h3 = all(heading, n => n.tag === "h3")[0];
      const title = (h3 ? h3.children.filter(c => typeof c === "string").map(text).join(" ") : text(heading)).replace(/\s+/g, " ").trim();
      const upgrade = endpoints(title);
      const typed = all(row, n => has(n, "item")).map(n => ({name:text(first(n,"title")), kind:text(first(n,"kind")).toLowerCase()}));
      const ships = typed.filter(n => /^(ship|vehicle)$/.test(n.kind));
      if (!upgrade && !ships.length) continue; // Do not transmit coupons or unrelated pledge titles.
      const rsiId = idValue(first(row, "js-pledge-id")?.attrs.value);
      const apply = first(row, "js-apply-upgrade"), ccuId = upgrade ? idValue(apply?.attrs.rel) : null;
      const packageId = rsiId ? `rsi-pledge:${rsiId}` : ccuId ? `rsi-ccu-package:${ccuId}` : `observation:${scanId}:${page}:${index}`;
      const ownership = /^attributed$/i.test(text(first(row, "availability"))) ? "owned" : "unknown";
      const insurance = typed.filter(n => n.kind === "insurance").map(n => ({label:n.name, scope:"package", provenance:"typed-insurance-item"}));
      const provenance = {source:"rsi-hangar", page, fields:"pledge-title-row/typed-items", identity:rsiId ? "js-pledge-id[value]" : ccuId ? "js-apply-upgrade[rel]" : null};
      packages.push({id:packageId, identity:rsiId || ccuId ? "stable" : "uncertain", rsi_id:rsiId, title, ownership, insurance,
        rsi_values:{pledge:null, melt:null}, provenance});
      const base = {package_id:packageId, ownership, insurance, rsi_values:{pledge:null,melt:null}, value_scope:"package", provenance};
      if (upgrade) items.push({...base, id:ccuId ? `rsi-ccu:${ccuId}` : `${packageId}:ccu`, identity:ccuId ? "stable" : "uncertain", rsi_id:ccuId,
        kind:"ccu", name:title, source_ship:upgrade.source, target_ship:upgrade.target, upgrade_base_eligible:false});
      for (const [child, ship] of ships.entries()) items.push({...base, id:`observation:${scanId}:${page}:${index}:ship:${child}`, identity:"uncertain", rsi_id:null,
        kind:ship.kind, name:ship.name, source_ship:null, target_ship:null, upgrade_base_eligible:ownership === "owned"});
    }
    const recognized = rows.length > 0;
    if (!recognized) warnings.push({page, reason:"unrecognized-or-empty-hangar"});
    return {packages, items, warnings, recognized};
  }
  function parseBuybacks(html, page, scanId) {
    const doc=tree(html), packages=[], items=[];
    const rows=all(doc,n=>n.tag==='article' && has(n,'pledge'));
    for(const [index,row] of rows.entries()) {
      const heading=all(first(row,'pledge-title-row') || row,n=>n.tag==='h1')[0];
      const title=heading?.attrs.title || text(heading).replace(/\s*- upgraded$/i,'');
      const ccu=endpoints(title);
      if(!ccu && !/^(?:Standalone Ships?|Package|Game Package)\s*[-:]/i.test(title)) continue;
      const action=first(row,'buyback-action');
      const link=action && all(action,n=>n.tag==='a')[0];
      const rsiId=idValue(link?.attrs['data-pledgeid']) || idValue((link?.attrs.href || '').match(/^\/pledge\/buyback\/(\d+)$/)?.[1]);
      const id=rsiId ? `rsi-buyback:${rsiId}` : `observation:${scanId}:${page}:${index}`;
      const provenance={source:'rsi-buyback',page,fields:'article.pledge/pledge-title-row',identity:rsiId ? 'buyback-action' : null};
      const base={identity:rsiId?'stable':'uncertain',rsi_id:rsiId,ownership:'buyback',insurance:[],rsi_values:{pledge:null,melt:null},provenance};
      packages.push({...base,id,title});
      if(ccu) items.push({...base,id:id+':ccu',package_id:id,kind:'ccu',name:title,source_ship:ccu.source,target_ship:ccu.target,value_scope:'package',upgrade_base_eligible:false});
      // The list's historical "Contained" text is not the reacquired package contents.
    }
    return {packages,items,recognized:rows.length>0,warnings:rows.length?[]:[{page,reason:'unrecognized-or-empty-buybacks'}]};
  }
  function normalize(pages, sync, scanId, source='rsi-hangar') {
    const parsed = pages.map(p => (source==='rsi-buyback'?parseBuybacks:parsePage)(p.payload, p.page, scanId));
    const inventory = {schema_version:1, source, packages:parsed.flatMap(p=>p.packages), items:parsed.flatMap(p=>p.items),
      unsupported:["buybacks", "loaner-entitlements", "actual-paid", "rsi-pledge-and-melt-values", "stable-child-ship-identity"],
      sync:{...sync, warnings:parsed.flatMap(p=>p.warnings), complete:!!sync.complete && parsed.every(p=>p.recognized)}};
    const packageOccurrences=new Map();
    for(const p of inventory.packages) {const key=p.id+':page:'+p.provenance.page;packageOccurrences.set(key,(packageOccurrences.get(key)||0)+1);}
    for (const key of ["packages", "items"]) {
      const counts = new Map();
      for (const r of inventory[key]) counts.set(r.id, (counts.get(r.id)||0)+1);
      for (const r of inventory[key]) if (counts.get(r.id) > 1) {
        const originalId=r.id;
        r.identity = "uncertain";
        inventory.sync.complete = false;
        inventory.sync.warnings.push({reason:"duplicate-identity", id:originalId});
        r.id=`observation:${scanId}:duplicate:${key}:${inventory[key].indexOf(r)}`;
        if(key==='packages') {
          const samePageCount=packageOccurrences.get(originalId+':page:'+r.provenance.page);
          for(const item of inventory.items) if(item.package_id===originalId && item.provenance.page===r.provenance.page) {
            item.package_id=samePageCount>1 ? null : r.id;
            if(samePageCount>1) item.upgrade_base_eligible=false;
          }
        }
      }
    }
    if(source==='rsi-buyback') inventory.unsupported=['loaner-entitlements','actual-paid','rsi-pledge-and-melt-values','buyback-current-price','buyback-reacquired-contents','buyback-availability'];
    inventory.sync.last_successful_at=inventory.sync.complete ? (sync.finished_at || null) : null;
    return inventory;
  }
  const ownedKeys = ["id","identity","rsi_id","title","name","kind","package_id","ownership","insurance","rsi_values","value_scope","source_ship","target_ship","upgrade_base_eligible","provenance"];
  const sourceRecord = r => Object.fromEntries(ownedKeys.filter(k => k in r).map(k=>[k,r[k]]));
  const comparable = r => JSON.stringify(Object.fromEntries(ownedKeys.filter(k => k !== "provenance" && k in r).map(k=>[k,r[k]])));
  function stableMap(records) {
    const map = new Map(), duplicates = new Set();
    for (const r of records) if (r.identity === "stable") { if (map.has(r.id)) duplicates.add(r.id); map.set(r.id,r); }
    for (const id of duplicates) map.delete(id);
    return map;
  }
  function preview(previous, next) {
    if(previous && previous.source!==next.source) throw new Error('Compare inventories from the same source. Hangar and buybacks are separate.');
    const result = {added:[], changed:[], missing:[], unchanged:[], uncertain:[], requires_review:true};
    for (const collection of ["packages", "items"]) {
      const before = previous?.[collection] || [], after = next[collection] || [];
      const old = stableMap(before), fresh = stableMap(after);
      for (const r of after) {
        const entry = {collection, id:r.id};
        if (!fresh.has(r.id) || r.identity !== "stable") result.uncertain.push({...entry, reason:"identity-not-stable"});
        else if (!old.has(r.id) && before.some(p=>p.id===r.id)) result.uncertain.push({...entry,reason:"ambiguous-prior-identity"});
        else if (!old.has(r.id)) result.added.push(entry);
        else result[comparable(old.get(r.id)) === comparable(r) ? "unchanged" : "changed"].push(entry);
      }
      for (const r of before) if (!fresh.has(r.id)) {
        const stable = old.has(r.id) && r.identity === "stable";
        result[stable && next.sync.complete ? "missing" : "uncertain"].push({collection,id:r.id,reason:stable ? "not-observed" : "prior-identity-not-stable"});
      }
    }
    return result;
  }
  function reconcile(previous, next) {
    const changes=preview(previous,next);
    const proposal = {...next,sync:{...next.sync,last_successful_at:next.sync.last_successful_at || previous?.sync?.last_successful_at || null}};
    for (const collection of ["packages", "items"]) {
      const before = previous?.[collection] || [], after = next[collection] || [];
      const old = stableMap(before), fresh = stableMap(after);
      proposal[collection] = before.map(r => old.has(r.id) && fresh.has(r.id) ? {...r, ...sourceRecord(fresh.get(r.id))} : {...r});
      for (const r of after) if (fresh.has(r.id) && !before.some(p=>p.id===r.id)) proposal[collection].push(sourceRecord(r));
    }
    return {inventory:proposal, preview:changes};
  }
  const api = {parsePage, parseBuybacks, normalize, preview, reconcile, endpoints};
  root.RSIInventory = api;
  if (typeof module !== "undefined") module.exports = api;
})(globalThis);
