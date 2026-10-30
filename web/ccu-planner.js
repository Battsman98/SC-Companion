/* Stateless guest planning; account authentication is only needed for persistence. */
(() => {
  const root = document.querySelector('#ccuPlanner');
  const workspace = document.querySelector('#ccuWorkspace');
  const notice = document.querySelector('#ccuNotice');
  let plan = {revision:0, ships:[], bases:[], upgrades:[], goals:[]}, imported = [], busy = false, dirty = false, pendingImport=null, marketInfo=null;
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const money = value => value == null ? 'Unknown' : new Intl.NumberFormat('en-US', {style:'currency', currency:'USD'}).format(value);
  const uid = () => crypto.randomUUID();
  const amount = value => value === '' ? null : Number(value);
  const field = (label, input) => `<label>${label}${input}</label>`;
  const input = (name, extra = '') => `<input name="${name}" ${extra}>`;
  const number = name => input(name, 'type="number" min="0" max="10000" step="0.01" placeholder="Unknown"');
  const choices = (name, options) => `<select name="${name}">${options}</select>`;
  const option = (value, label) => `<option value="${esc(value)}">${esc(label)}</option>`;
  const status = () => choices('status', option('owned','Owned') + option('buyback','Buyback — verify checkout cost') + option('candidate','Missing — target / quote'));
  const shipSelect = name => input(name, 'list="ccuNames" required autocomplete="off" placeholder="Type a ship name" maxlength="100"');
  const names = {cash:'Lowest additional spending', total:'Lowest total — known costs only', gaps:'Fewest missing upgrades'};
  const protection = (kind, item) => `<button type="button" data-protect="${kind}" data-id="${esc(item.id)}" aria-pressed="${!!item.protected}">${item.protected?'Unprotect':'Protect'} this copy</button>`;
  const historicalRow = d => `<li>${esc(d.source)} → ${esc(d.target)} · Historical reference ${money(d.reference_cost)} · Observed ${esc(d.observed_at)} · Availability unconfirmed<label>Acquisition quote (USD; blank uses reference)<input data-acquisition-quote type="number" min="0" max="10000" step="0.01" value="${plan.deal_quotes?.['historical:'+d.id]??''}"></label><button type="button" data-quote="${esc('historical:'+d.id)}" data-goal="${esc(plan.goals.at(-1)?.id||'')}">Update acquisition quote</button></li>`;
  const accounting = item => `${item.status==='owned'?'Actual paid / allocated child cost':'Entered purchase estimate'}: ${money(item.cost)} · RSI pledge: ${money(item.pledge_value)} · Melt: ${money(item.melt_value)} · Insurance: ${esc(item.insurance||'Unknown')}${item.lti?' · Confirmed LTI':''}${item.package_id?` · Package ${esc(item.package_id)}: pledge ${money(item.package_value)}, melt ${money(item.package_melt_value)} (shared values, excluded from child cost)`:''}${item.import_missing?' · Missing or changed import evidence; unavailable for routing':''}`;
  function announce(text, error = false) {notice.textContent = text; notice.className = error ? 'error' : 'state';}
  async function request(path, method='GET', body) {
    const response = await fetch(path, {method, headers:body ? {'Content-Type':'application/json'} : {}, body:body ? JSON.stringify(body) : undefined, signal:AbortSignal.timeout(20000)});
    const result = await response.json();
    if (response.status === 401) throw Object.assign(new Error('Sign in with Discord to save or load account plans. Your current draft is still here; you can keep building without signing in.'), {status:401});
    if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : 'Please check the entered values and references.');
    return result;
  }
  async function action(work) {
    if (busy) return;
    busy = true; root.setAttribute('aria-busy', 'true');
    root.querySelectorAll('button').forEach(b => b.disabled = true);
    try {await work();} catch(error) {
      announce(error.message, true);
      if(error.status===401){
        const link=document.createElement('a');link.href='/auth/discord/login';link.target='_blank';link.rel='noopener';
        link.textContent=' Sign in in a new tab, then return here and try again.';notice.append(link);
      }
    } finally {
      busy = false; root.removeAttribute('aria-busy');root.querySelectorAll('button').forEach(b => b.disabled = false);
    }
  }
  async function save(next, {preserveGoal = true} = {}) {
    plan = await request('/api/ccu/validate', 'POST', next);
    dirty = true;
    render({preserveGoal}); announce('Draft updated for this page session. Use Save to account to keep it. Costs are USD before tax and fees.');
  }
  const remove = (kind, id) => `<button type="button" data-edit="${kind}" data-id="${esc(id)}">Edit</button> <button type="button" data-remove="${kind}" data-id="${esc(id)}">Remove</button>`;
  function render({preserveGoal = true} = {}) {
    const goalForm = workspace.querySelector('form[data-ccu="goal"]');
    const goalDraft = preserveGoal && goalForm && {
      editId: goalForm.dataset.editId,
      controls: [...goalForm.elements].filter(c=>c.name).map(c=>({name:c.name,value:c.value,checked:c.checked}))
    };
    const openSections = [...workspace.querySelectorAll(':scope > details')].map(d=>d.open);
    workspace.hidden = false;
    const reserved = id => plan.goals.filter(g => g.reserved_base === id || g.reserved_ccus.includes(id)).map(g=>g.target).join(', ');
    workspace.innerHTML = `
      <datalist id="ccuNames">${[...new Set([...plan.ships.map(s=>s.name), ...imported.map(s=>s.name)])].map(s=>option(s,s)).join('')}</datalist>
      <h3>Goal ship</h3>
      <p>Choose the ship you want to reach. Everything else is optional. We look up its USD pledge value for you.</p>
      <form data-ccu="goal">${field('Goal ship',shipSelect('target'))}${field('Base ship you own (optional)',input('base_ship','list="ccuNames" placeholder="Leave blank to compare starting options" maxlength="100"'))}${field('Use a saved base (optional)',choices('base_id',option('','No specific base')+plan.bases.map(b=>option(b.id,b.label)).join('')))}${field('Require confirmed LTI',input('lti_only','type="checkbox"'))}<button>Build chain</button></form>
      <details><summary>Warbond savings</summary>
      <form data-ccu="deals">${field('Use active warbond offers',input('use_active_warbonds',`type="checkbox" ${plan.use_active_warbonds!==false?'checked':''}`))}${field('Include historical warbond deals (optional)',input('include_historical',`type="checkbox" ${plan.include_historical?'checked':''}`))}<button>Apply deal preferences</button></form>
      <p>Active offers require new cash; prices and availability are checked when calculating routes. Historical deals are off by default. Historical reference prices are hypothetical until you enter an acquisition quote; availability remains unconfirmed. No acquisition locations are displayed.</p>
      <button type="button" data-market>Review deal catalogue</button>
      <p>${marketInfo?`${marketInfo.offers.length} active target offers · ${marketInfo.historical.length} archived CCU references · Checked ${esc(marketInfo.checked_at||'Unknown')}. ${esc(marketInfo.warning)}`:'The deal catalogue is checked automatically when you build a route.'}</p>
      ${marketInfo?`<ul>${marketInfo.offers.map(d=>`<li>${esc(d.name)} · Current upgrade value ${money(d.standard_price)} · Discounted target value ${money(d.warbond_price)} · Potential discount ${money(d.standard_price-d.warbond_price)}</li>`).join('')}</ul>`:''}
      ${plan.include_historical?`<p>History contains offers observed by this site from this release onward, plus your references. It is not a complete record of past promotions. Only pairs with known current ship values can be routed.</p>
      <ul>${(marketInfo?.historical||[]).map(historicalRow).join('')}</ul>
      <form data-ccu="history">${field('Historical from ship',shipSelect('source'))}${field('Historical to ship',shipSelect('target'))}${field('Historical CCU price (USD)',input('reference_cost','type="number" min="0.01" max="10000" step="0.01" required'))}${field('Historical offer date (optional)',input('observed_at','type="date"'))}<button>Add historical reference</button></form>
      ${(plan.historical_deals||[]).map(d=>`<ul>${historicalRow(d)}</ul><button type="button" data-remove="historical_deals" data-id="${esc(d.id)}">Remove reference</button>`).join('')}`:''}
      ${plan.include_historical&&Object.keys(plan.deal_quotes||{}).length?`<details><summary>Saved acquisition quotes</summary>${Object.entries(plan.deal_quotes).map(([key,value])=>{const d=[...(plan.historical_deals||[]),...(marketInfo?.historical||[])].find(d=>'historical:'+d.id===key);return `<p>${d?esc(d.source+' → '+d.target):'Historical CCU quote'} · ${money(value)} <button type="button" data-clear-quote="${esc(key)}">Remove acquisition quote</button></p>`;}).join('')}</details>`:''}</details>
      <details><summary>Base ships (optional) · ${plan.bases.length}</summary>
      <p>Add a ship you already own, or a buyback / purchase you are considering. Leave this blank to compare suggested purchases. Confirm LTI only when your pledge includes it. For package children, enter only an allocated share of actual paid cost; never repeat the full package price on every ship.</p>
      <form data-ccu="base">${field('Base ship',shipSelect('ship'))}${field('Label (optional)',input('label','maxlength="150" placeholder="Pack ship 2"'))}${field('Status',status())}${field('Paid / buyback / target cost (optional)',number('cost'))}${field('Confirmed LTI',input('lti','type="checkbox"'))}<button>Add base ship</button></form>
      ${plan.bases.map(b=>`<p><strong>${esc(b.label)}</strong> — ${esc(b.ship)} · ${esc(b.status)} · ${accounting(b)} ${reserved(b.id)?`· Reserved: ${esc(reserved(b.id))}`:''} ${protection('bases',b)} ${remove('bases',b.id)}</p>`).join('')}</details>
      <details><summary>Owned CCUs (optional) · ${plan.upgrades.length}</summary>
      <p>Import unapplied upgrades using the RSI Hangar Importer, or enter them here. Each copy is tracked separately. Unknown purchase costs stay blank. ${plan.upgrades.filter(c=>c.status==='owned'&&!c.protected&&!c.import_missing&&!reserved(c.id)).length} owned copies available; ${plan.upgrades.filter(c=>c.protected).length} protected.</p>
      <button type="button" data-import-ccu>Import owned CCUs from RSI</button>
      <div id="ccuImportPreview"></div>
      <p>Requires RSI sign-in in your browser. Importer 0.6.0 includes ships, packages and CCUs; older CCU imports remain supported. Discord is only needed to save your plan. For Chrome / Edge, extract the download and load its folder using Load unpacked on the browser extensions page. <a href="/assets/rsi-connector-extension-v0.6.0.zip">Chrome / Edge importer</a> · <a href="/assets/rsi-connector-extension-firefox-v0.6.0.zip">Firefox importer</a></p>
      <form data-ccu="upgrade">${field('From ship',shipSelect('source'))}${field('To ship',shipSelect('target'))}${field('Status',status())}${field('Purchase cost (optional)',number('cost'))}${field('Note (optional)',input('note','maxlength="200"'))}<button>Add CCU copy</button></form>
      ${plan.upgrades.map(c=>`<p><strong>${esc(c.source)} → ${esc(c.target)}</strong> · Copy ${esc(c.id)} · ${esc(c.status)} · ${accounting(c)} ${reserved(c.id)?`· Reserved: ${esc(reserved(c.id))}`:''} ${esc(c.note)} ${protection('upgrades',c)} ${remove('upgrades',c.id)}</p>`).join('')}</details>
      <details><summary>Advanced: pledge values</summary><p>Values are looked up when you add ships. Correct missing or outdated values here; these are upgrade values, not what you paid.</p>
      <form data-ccu="ship">${field('Ship name',shipSelect('name'))}${field('Pledge value (USD)',input('value','type="number" min="0.01" max="10000" step="0.01" required'))}<button>Update pledge value</button></form>
      <p>${plan.ships.map(s=>`${esc(s.name)}: ${money(s.value)}`).join(' · ') || 'No ships added yet.'}</p></details>
      <h3>Your chains</h3>
      <div class="ccu-goals">${plan.goals.map(g=>`<article><h3>${esc(g.target)}</h3><p>${g.base_id?esc(plan.bases.find(b=>b.id===g.base_id)?.label):'Compare starting options'} · ${g.lti_only?'LTI required':'LTI not required'}</p>
      <p>${g.reserved_base?`Reserved: ${esc(plan.bases.find(b=>b.id===g.reserved_base)?.label)} and ${g.reserved_ccus.length} CCU copies.`:'No reservations.'}</p>
      <button type="button" data-find="${esc(g.id)}">Find routes</button> <button type="button" data-release="${esc(g.id)}">Release reservations</button> ${remove('goals',g.id)}
      <p>${(g.locked_steps||[]).length} locked steps · ${(g.excluded_steps||[]).length} excluded steps. <button type="button" data-reset-steps="${esc(g.id)}">Clear route constraints</button></p>
      ${g.saved_path?.length?`<p>Saved route: ${g.saved_path.map(esc).join(' → ')}. Find routes again to refresh costs.</p>`:''}<div data-results="${esc(g.id)}"></div></article>`).join('') || '<p>Enter a goal ship above to build your first chain. Base ship and owned CCUs can stay blank.</p>'}</div>
      <p>All gap and candidate costs are estimates, not live offers. Reservations protect individual bases and CCUs across goals. A re-import does not erase your planner.</p>`;
    workspace.querySelectorAll(':scope > details').forEach((d,i)=>{if(openSections[i]!==undefined)d.open=openSections[i];});
    if(goalDraft && (!goalDraft.editId || plan.goals.some(g=>g.id===goalDraft.editId))){
      const form=workspace.querySelector('form[data-ccu="goal"]');
      for(const saved of goalDraft.controls){
        const control=form.elements.namedItem(saved.name);
        if(control.type==='checkbox')control.checked=saved.checked;else control.value=saved.value;
      }
      if(goalDraft.editId){form.dataset.editId=goalDraft.editId;form.querySelector('button').textContent='Save changes';}
    }
  }
  async function load() {
    if (dirty && !confirm('Replace your current unsaved draft with your saved account plan?')) return;
    const [data, hangar] = await Promise.all([request('/api/me/ccu'),request('/api/me/ships')]);
    plan = data; dirty = false; imported = hangar.filter(s=>s.ownership_type==='pledged');render({preserveGoal:false});announce('Account planner loaded. Changes stay in this page until you save.');
  }
  document.querySelector('#ccuReload').addEventListener('click',()=>action(load));
  document.querySelector('#ccuSave').addEventListener('click',()=>action(async()=>{
    plan = await request('/api/me/ccu', 'PUT', plan);
    dirty = false; announce('Planner saved to your Discord account.');
  }));
  window.addEventListener('beforeunload', event=>{if(dirty){event.preventDefault();event.returnValue='';}});
  workspace.addEventListener('submit', event => {
    const form = event.target.closest('[data-ccu]');if(!form)return;event.preventDefault();
    action(async()=>{
      const d=Object.fromEntries(new FormData(form));const next=structuredClone(plan);
      const editId=form.dataset.editId;
      const collection={base:'bases',upgrade:'upgrades',goal:'goals'}[form.dataset.ccu];
      const previous=editId?next[collection].find(x=>x.id===editId):{};
      if(form.dataset.ccu==='deals'){
        next.use_active_warbonds=!!d.use_active_warbonds;next.include_historical=!!d.include_historical;
      }
      if(form.dataset.ccu==='history'){
        const source=await ensureShip(next,d.source),target=await ensureShip(next,d.target);
        next.historical_deals||=[];next.historical_deals.push({id:uid(),source,target,reference_cost:Number(d.reference_cost),observed_at:d.observed_at||'User-entered historical reference'});
      }
      if(editId) {
        const reserved=next.goals.some(g=>g.reserved_base===editId||g.reserved_ccus.includes(editId));
        if(reserved)throw new Error('Release this pledge or CCU from its goal before editing it.');
        next[collection]=next[collection].filter(x=>x.id!==editId);
      }
      if(form.dataset.ccu==='ship') {
        const existing=next.ships.find(s=>s.name.toLowerCase()===d.name.trim().toLowerCase());
        if(existing)existing.value=Number(d.value);else next.ships.push({name:d.name.trim(),value:Number(d.value)});
      }
      if(form.dataset.ccu==='base'){d.ship=await ensureShip(next,d.ship);d.label=d.label||d.ship;}
      if(form.dataset.ccu==='base')next.bases.push({...previous,id:editId||uid(),ship:d.ship,label:d.label,status:d.status,cost:amount(d.cost),lti:!!d.lti});
      if(form.dataset.ccu==='upgrade') {
        d.source=await ensureShip(next,d.source);d.target=await ensureShip(next,d.target);
        if(next.ships.find(s=>s.name===d.source)?.value && next.ships.find(s=>s.name===d.target)?.value && next.ships.find(s=>s.name===d.source).value>=next.ships.find(s=>s.name===d.target).value)throw new Error('A CCU must move to a higher pledge value.');
        next.upgrades.push({...previous,id:editId||uid(),source:d.source,target:d.target,status:d.status,cost:amount(d.cost),note:d.note});
      }
      if(form.dataset.ccu==='goal'){
        d.target=await ensureShip(next,d.target);
        if(d.base_ship?.trim()){
          if(d.base_id)throw new Error('Choose either a base ship name or a saved base.');
          const ship=await ensureShip(next,d.base_ship);d.base_id=uid();
          next.bases.push({id:d.base_id,ship,label:ship,status:'owned',cost:null,lti:false});
        }
      }
      if(form.dataset.ccu==='goal')next.goals.push({...previous,id:editId||uid(),target:d.target,base_id:d.base_id,lti_only:!!d.lti_only,reserved_base:'',reserved_ccus:[],saved_path:[]});
      await save(next,{preserveGoal:form.dataset.ccu!=='goal'});
      if(form.dataset.ccu==='goal')await showRoutes(next.goals.at(-1).id);
      if(['deals','history'].includes(form.dataset.ccu)&&next.goals.length)await showRoutes(next.goals.at(-1).id);
    });
  });
  workspace.addEventListener('click',event=>{
    const b=event.target.closest('button');if(!b)return;
    if(b.hasAttribute('data-market'))action(async()=>{marketInfo=await request('/api/ccu/deals');render();announce('Deal catalogue refreshed. Historical availability is never assumed.');});
    if(b.dataset.clearQuote)action(async()=>{const next=structuredClone(plan);delete next.deal_quotes[b.dataset.clearQuote];await save(next);if(next.goals.length)await showRoutes(next.goals.at(-1).id);});
    if(b.dataset.quote)action(async()=>{
      const value=b.closest('li').querySelector('[data-acquisition-quote]').value;
      const next=structuredClone(plan);next.deal_quotes||={};
      if(value==='')delete next.deal_quotes[b.dataset.quote];else next.deal_quotes[b.dataset.quote]=Number(value);
      await save(next);if(b.dataset.goal)await showRoutes(b.dataset.goal);
    });
    if(b.dataset.protect)action(async()=>{
      const next=structuredClone(plan),item=next[b.dataset.protect].find(x=>x.id===b.dataset.id);
      item.protected=!item.protected;await save(next);
      announce(item.protected?'Copy protected. It will not be used in new routes. Existing reservations remain recorded.':'Copy available for routing again.');
    });
    if(b.dataset.resetSteps)action(async()=>{
      const next=structuredClone(plan),g=next.goals.find(x=>x.id===b.dataset.resetSteps);
      g.locked_steps=[];g.excluded_steps=[];await save(next);await showRoutes(g.id);
    });
    if(b.dataset.constraint)action(async()=>{
      const next=structuredClone(plan),g=next.goals.find(x=>x.id===b.dataset.goal);
      g.locked_steps||=[];g.excluded_steps||=[];
      const key=b.dataset.key;
      if(b.dataset.constraint==='exclude'){
        g.locked_steps=g.locked_steps.filter(k=>k!==key);g.excluded_steps=[...new Set([...g.excluded_steps,key])];
      }else{
        const chosen=b.dataset.constraint==='replace'?b.closest('li').querySelector('select').value:key;
        if(!chosen)throw new Error('Choose an alternative first.');
        g.locked_steps=g.locked_steps.filter(k=>k!==key);
        if(b.dataset.constraint!=='unlock')g.locked_steps=[...new Set([...g.locked_steps,chosen])];
        g.excluded_steps=g.excluded_steps.filter(k=>k!==chosen);
      }
      await save(next);await showRoutes(g.id);
    });
    if(b.dataset.edit) {
      const kind={bases:'base',upgrades:'upgrade',goals:'goal'}[b.dataset.edit];
      const form=workspace.querySelector(`[data-ccu="${kind}"]`);const item=plan[b.dataset.edit].find(x=>x.id===b.dataset.id);
      form.dataset.editId=item.id;
      for(const control of form.elements)if(control.name in item) {
        if(control.type==='checkbox')control.checked=!!item[control.name];else control.value=item[control.name]??'';
      }
      const detail=form.closest('details');if(detail)detail.open=true;
      form.querySelector('button').textContent='Save changes';form.scrollIntoView({block:'center'});
      announce('Editing this entry. Editing a goal releases its old reservations. Refresh to cancel.');
    }
    if(b.dataset.remove)action(async()=>{const next=structuredClone(plan);next[b.dataset.remove]=next[b.dataset.remove].filter(x=>x.id!==b.dataset.id);await save(next);});
    if(b.dataset.release)action(async()=>{const next=structuredClone(plan);const g=next.goals.find(g=>g.id===b.dataset.release);g.reserved_base='';g.reserved_ccus=[];g.saved_path=[];await save(next);});
    if(b.dataset.find)action(()=>showRoutes(b.dataset.find));
    if(b.dataset.reserve)action(async()=>{
      const result=await request(`/api/ccu/routes/${encodeURIComponent(b.dataset.reserve)}`, 'POST', plan);
      const route=result.routes.find(r=>r.mode===b.dataset.mode);
      if(!route)throw new Error('No eligible route to reserve. Find routes again.');
      const next=structuredClone(plan), goal=next.goals.find(g=>g.id===b.dataset.reserve);
      if(!next.ships.some(s=>s.name===route.base.ship))next.ships.push({name:route.base.ship,value:route.base_value});
      if(route.base.id.startsWith('suggested:')){route.base.id=uid();next.bases.push(route.base);}
      goal.reserved_base=route.base.id;goal.reserved_ccus=route.steps.filter(e=>e.id).map(e=>e.id);
      goal.saved_path=[route.base.ship,...route.steps.map(e=>e.target)];
      await save(next);
      announce('Reserved in this draft. Other goals will not use these copies. Save to account to keep this plan.');
    });
  });
  async function showRoutes(id) {
      const result=await request(`/api/ccu/routes/${encodeURIComponent(id)}`, 'POST', plan);
      const out=workspace.querySelector(`[data-results="${CSS.escape(id)}"]`);
      const goal=plan.goals.find(g=>g.id===id);
      const control=(action,key,label)=>`<button type="button" data-constraint="${action}" data-goal="${esc(id)}" data-key="${esc(key)}">${label}</button>`;
      out.innerHTML=result.routes.map(r=>`<details open><summary>${esc(names[r.mode])}</summary><p><strong>${esc(r.base.label)}</strong> (${r.base.id.startsWith('suggested:')?'suggested purchase · availability and LTI unverified':esc(r.base.status)}) → ${r.steps.map(e=>`${esc(e.target)} [${esc(e.status)}]`).join(' → ') || (r.base.status==='owned'?'Already at target':'Standalone purchase baseline')}</p>
      <p>Estimated additional spend: <strong>${money(r.additional)}</strong> · Estimated total: ${money(r.total)} · Potential savings against current goal value: ${money(r.savings)} · Missing purchases: ${r.missing}</p>
      <p>Warbond step savings versus standard value differences: <strong>${money(r.warbond_savings||0)}</strong>. ${r.hypothetical?'Hypothetical: this route uses historical prices without acquisition quotes.':'Any historical acquisition quotes remain unconfirmed until purchased.'}</p>
      <p>${accounting(r.base)}. No availability or savings guarantee. Package costs require an explicit per-child allocation before totals are known.</p>
      ${r.unknown_costs?`<p>${r.unknown_costs} purchase prices unknown. Known cost subtotal: ${money(r.known_cost)}. Total and savings cannot be confirmed.</p>`:''}
      <ol class="ccu-steps">${r.steps.map(e=>`<li><strong>${esc(e.source)} → ${esc(e.target)}</strong><p>Current upgrade values: ${money(e.source_value)} → ${money(e.target_value)}</p><p>${e.status==='owned'?'Owned · 1 copy':e.status==='gap'?'Missing · theoretical bridge, availability unverified':e.status==='active_warbond'?'Active warbond · new cash required':e.status==='historical_warbond'?'Historical warbond · availability unconfirmed':'Missing · entered purchase estimate'} · ${e.id?`Copy ${esc(e.id)} · `:''}${e.status==='owned'?'Actual paid':'Estimated cost'}: ${money(e.cost)} · Insurance: ${esc(e.insurance||'Unknown')} · Spend remaining after step: ${money(e.remaining)}</p><p>${esc(e.note)} ${e.observed_at?'Observed: '+esc(e.observed_at):''}</p>
      ${e.status==='historical_warbond'?`<p>Historical reference: ${money(e.reference_cost)} · ${e.hypothetical?'Hypothetical price':'Your acquisition quote'}</p><label>Acquisition quote (USD; blank uses historical reference)<input data-acquisition-quote type="number" min="0" max="10000" step="0.01" value="${e.acquisition_quote??''}"></label><button type="button" data-quote="${esc(e.key)}" data-goal="${esc(id)}">Update acquisition quote</button>`:''}
      ${control((goal.locked_steps||[]).includes(e.key)?'unlock':'lock',e.key,(goal.locked_steps||[]).includes(e.key)?'Unlock step':'Lock step')}${control('exclude',e.key,'Exclude step')}
      ${(e.alternatives||[]).length?`<details><summary>Replace this step</summary><label>Alternative from ${esc(e.source)}<select>${option('','Choose an alternative')}${e.alternatives.map(a=>option(a.key,`${a.target} · ${a.status==='gap'?'theoretical':a.status} · ${money(a.cost)}${a.id?' · '+a.id:''}`)).join('')}</select></label>${control('replace',e.key,'Use alternative')}</details>`:''}</li>`).join('')}</ol>
      <button type="button" data-reserve="${esc(id)}" data-mode="${r.mode}">Reserve this base and listed CCUs</button></details>`).join('') || '<p>Your goal is added. A price may be missing, LTI may need confirmation, or protected/reserved copies and route constraints prevent a route. Check Advanced: pledge values, add a suitable base, or clear route constraints. You can leave this goal in your plan and fill details later.</p>';
      if(result.routes.length&&!result.routes.some(r=>r.mode==='total'))out.insertAdjacentHTML('afterbegin','<p>Lowest total comparison is unavailable for these constraints because actual paid costs are unknown. Additional-spend estimates are still shown.</p>');
      if(result.market_warning)out.insertAdjacentHTML('afterbegin',`<p>${esc(result.market_warning)}</p>`);

  }
  async function ensureShip(next, name) {
    name=name.trim();if(!name)throw new Error('Enter a ship name.');
    const existing=next.ships.find(s=>s.name.toLowerCase()===name.toLowerCase());
    if(existing)return existing.name;
    let value=null;
    try {
      const detail=await request('/api/ships/'+encodeURIComponent(name));
      if(detail.name)name=detail.name;
      if(detail.pledge?.currency==='USD' && Number(detail.pledge.price)>0)value=Number(detail.pledge.price);
    } catch(error) { /* A goal can be drafted even when the catalog is unavailable. */ }
    if(!next.ships.some(s=>s.name===name))next.ships.push({name,value});
    return name;
  }
  let suggestTimer;
  workspace.addEventListener('input',event=>{
    if(event.target.getAttribute('list')!=='ccuNames')return;
    clearTimeout(suggestTimer);const query=event.target.value;
    suggestTimer=setTimeout(async()=>{
      try{const names=await request('/api/autocomplete/ships?query='+encodeURIComponent(query));
        if(event.target.value===query)workspace.querySelector('#ccuNames').innerHTML=[...new Set([...names,...plan.ships.map(s=>s.name)])].map(n=>option(n,n)).join('');
      }catch(error){}
    },200);
  });
  root.addEventListener('click',event=>{
    if(!event.target.closest('[data-import-ccu]'))return;
    action(async()=>{
      announce('Reading unapplied CCUs through your RSI importer…');
      if(typeof rsiConnect!=='function')throw new Error('The RSI importer connection is not available. Reload the page.');
      const progress=p=>announce(`Reading RSI inventory: ${p.page||0} / ${p.totalPages||'?'} pages; ${p.failedPages||0} failed. No planner changes applied.`);
      let result=await rsiConnect({action:'importInventory'},120000,progress);
      if(result.code===400)result=await rsiConnect({action:'importCCUs'},120000,progress);
      if(![200,206].includes(result.code))throw new Error(result.error||'Install the RSI importer, sign into RSI, and try again.');
      if(result.inventory && result.inventory.schema_version!==1)throw new Error('Unsupported inventory version. Update the planner before importing.');
      pendingImport=result.inventory || {schema_version:0,packages:[],sync:{complete:false},items:(result.upgrades||[]).map(c=>({id:c.id,kind:'ccu',source_ship:c.source,target_ship:c.target,identity:'legacy',ownership:'owned'}))};
      previewImport();
    });
  });
  function previewImport(){
    const rows=pendingImport.items||[];
    if(rows.length>600)throw new Error('Import exceeds the planner limit. No changes applied.');
    const ids=new Set();for(const item of rows){if(ids.has(item.id))throw new Error('Duplicate import identity. No changes applied.');ids.add(item.id);}
    const missing=[...plan.bases,...plan.upgrades].filter(x=>x.import_identity==='stable'&&!ids.has(x.import_id));
    workspace.querySelector('#ccuImportPreview').innerHTML=`<h4>Review inventory import</h4><p>${pendingImport.sync.complete?'Complete scan':'Partial or legacy scan; absence will not mark items missing'}. ${rows.length} observations. ${pendingImport.sync.complete?missing.length:0} stable copies missing. Unsupported: ${esc((pendingImport.unsupported||[]).join(', ')||'See importer details')}.</p>
    <p>Uncertain identities need an explicit add or match choice. Names alone do not establish identity. Legacy IDs are not verified RSI IDs. Package amounts never become actual paid costs.</p>
    <label><input type="checkbox" id="ccuSameAccount"> This is the same RSI account as my existing imported entries, or my first import.</label>
    ${rows.map((item,i)=>{
      const kind=item.kind==='ccu'?'upgrades':'bases',eligible=item.ownership==='owned'&&(kind==='upgrades'||item.upgrade_base_eligible);
      const existing=plan[kind].find(x=>(x.import_id===item.id&&x.import_identity===item.identity)||(item.identity==='legacy'&&x.id===item.id));
      return `<label>${esc(item.kind==='ccu'?item.source_ship+' → '+item.target_ship:item.name)} · ${esc(item.identity)} · ${eligible?'owned':'ineligible / unverified ownership'}<select data-import-row="${i}">${option('skip','Skip')}${eligible?`${existing?option('match:'+existing.id,'Refresh matching copy'):''}${option('new','Add as a separate copy')}${plan[kind].filter(x=>x!==existing).map(x=>option('match:'+x.id,'Match existing: '+(x.label||x.source+' → '+x.target)+' · '+x.id)).join('')}`:''}</select></label>`;
    }).join('')}<button type="button" data-apply-import>Apply reviewed import</button><button type="button" data-cancel-import>Cancel import</button>`;
    rows.forEach((item,i)=>{if(item.identity!=='stable')return;const select=workspace.querySelector(`[data-import-row="${i}"]`);if(select.options.length>1)select.selectedIndex=1;});
    announce('Import preview ready. Review copies and account identity before applying. Your draft has not changed.');
  }
  root.addEventListener('click',event=>{
    if(event.target.closest('[data-cancel-import]')){pendingImport=null;workspace.querySelector('#ccuImportPreview').innerHTML='';announce('Import cancelled.');}
    if(!event.target.closest('[data-apply-import]'))return;
    action(async()=>{
      if(!pendingImport)throw new Error('Read inventory first.');
      if(!workspace.querySelector('#ccuSameAccount').checked)throw new Error('Confirm the RSI account before reconciling. For a different account, start a fresh draft in another tab.');
      const next=structuredClone(plan),seen=new Set(pendingImport.items.map(x=>x.id)),matched=new Set();let added=0,updated=0;
      const usd=value=>value?.currency==='USD'&&Number.isInteger(value.amount_minor)&&value.amount_minor>=0?value.amount_minor/100:null;
      for(const select of workspace.querySelectorAll('[data-import-row]')){
        if(select.value==='skip')continue;
        const item=pendingImport.items[Number(select.dataset.importRow)],kind=item.kind==='ccu'?'upgrades':'bases';
        const current=select.value.startsWith('match:')?next[kind].find(x=>x.id===select.value.slice(6)):null;
        if(current&&matched.has(current.id))throw new Error('Two observations cannot match the same copy.');
        if(current?.import_identity==='stable'&&current.import_id!==item.id)throw new Error('A verified stable copy cannot be reassigned to another import identity.');
        if(current)matched.add(current.id);
        if(select.value==='new'&&item.identity==='stable'&&next[kind].some(x=>x.import_id===item.id))throw new Error('This stable copy already exists. Match it instead.');
        const pkg=pendingImport.packages?.find(p=>p.id===item.package_id);
        const insurance=(item.insurance||[]).map(x=>x.label).join('; ').slice(0,120)||'Unknown';
        const metadata={import_id:item.id,import_identity:item.identity,import_missing:false,insurance,package_id:item.package_id||'',package_value:usd(pkg?.rsi_values?.pledge),package_melt_value:usd(pkg?.rsi_values?.melt)};
        const values=kind==='upgrades'?{source:await ensureShip(next,item.source_ship),target:await ensureShip(next,item.target_ship)}:{ship:await ensureShip(next,item.name),ownership:'pledged'};
        if(current&&Object.keys(values).some(k=>k!=='ownership'&&current[k]!==values[k])){current.import_missing=true;continue;}
        if(current){Object.assign(current,metadata);updated++;}
        else{next[kind].push({id:uid(),...values,...metadata,status:'owned',cost:null,...(kind==='bases'?{label:(pkg?.title||item.name).slice(0,150),lti:/lifetime insurance|\bLTI\b/i.test(insurance)}:{note:'RSI import; actual paid unknown'})});added++;}
      }
      if(pendingImport.sync.complete)for(const item of [...next.bases,...next.upgrades])if(item.import_identity==='stable'&&!seen.has(item.import_id))item.import_missing=true;
      for(const item of [...next.bases,...next.upgrades]){
        const observed=pendingImport.items.find(x=>x.id===item.import_id&&x.identity==='stable');
        if(observed&&(observed.ownership!=='owned'||(observed.kind!=='ccu'&&!observed.upgrade_base_eligible)))item.import_missing=true;
      }
      await save(next);pendingImport=null;
      announce(`Imported ${added} copies; refreshed ${updated}. Manual costs, protections and reservations kept. Missing or changed endpoints are unavailable for routing; nothing was deleted.`);
    });
  });
  render();
})();
