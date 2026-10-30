/* Stateless guest planning; account authentication is only needed for persistence. */
(() => {
  const root = document.querySelector('#ccuPlanner');
  const workspace = document.querySelector('#ccuWorkspace');
  const notice = document.querySelector('#ccuNotice');
  let plan = {revision:0, ships:[], bases:[], upgrades:[], goals:[]}, imported = [], busy = false, dirty = false;
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
  async function save(next) {
    plan = await request('/api/ccu/validate', 'POST', next);
    dirty = true;
    render(); announce('Draft updated for this page session. Use Save to account to keep it. Costs are USD before tax and fees.');
  }
  const remove = (kind, id) => `<button type="button" data-edit="${kind}" data-id="${esc(id)}">Edit</button> <button type="button" data-remove="${kind}" data-id="${esc(id)}">Remove</button>`;
  function render() {
    const openSections = [...workspace.querySelectorAll(':scope > details')].map(d=>d.open);
    workspace.hidden = false;
    const reserved = id => plan.goals.filter(g => g.reserved_base === id || g.reserved_ccus.includes(id)).map(g=>g.target).join(', ');
    workspace.innerHTML = `
      <datalist id="ccuNames">${[...new Set([...plan.ships.map(s=>s.name), ...imported.map(s=>s.name)])].map(s=>option(s,s)).join('')}</datalist>
      <h3>Goal ship</h3>
      <p>Choose the ship you want to reach. Everything else is optional. We look up its USD pledge value for you.</p>
      <form data-ccu="goal">${field('Goal ship',shipSelect('target'))}${field('Base ship you own (optional)',input('base_ship','list="ccuNames" placeholder="Leave blank to compare starting options" maxlength="100"'))}${field('Use a saved base (optional)',choices('base_id',option('','No specific base')+plan.bases.map(b=>option(b.id,b.label)).join('')))}${field('Require confirmed LTI',input('lti_only','type="checkbox"'))}<button>Build chain</button></form>
      <details><summary>Base ships (optional) · ${plan.bases.length}</summary>
      <p>Add a ship you already own, or a buyback / purchase you are considering. Leave this blank to compare suggested purchases. Confirm LTI only when your pledge includes it.</p>
      <form data-ccu="base">${field('Base ship',shipSelect('ship'))}${field('Label (optional)',input('label','maxlength="150" placeholder="Pack ship 2"'))}${field('Status',status())}${field('Paid / buyback / target cost (optional)',number('cost'))}${field('Confirmed LTI',input('lti','type="checkbox"'))}<button>Add base ship</button></form>
      ${plan.bases.map(b=>`<p><strong>${esc(b.label)}</strong> — ${esc(b.ship)} · ${esc(b.status)} · ${b.lti?'LTI':'LTI not confirmed'} · ${money(b.cost)} ${reserved(b.id)?`· Reserved: ${esc(reserved(b.id))}`:''} ${remove('bases',b.id)}</p>`).join('')}</details>
      <details><summary>Owned CCUs (optional) · ${plan.upgrades.length}</summary>
      <p>Import unapplied upgrades using the RSI Hangar Importer, or enter them here. Each copy is tracked separately. Unknown purchase costs stay blank.</p>
      <button type="button" data-import-ccu>Import owned CCUs from RSI</button>
      <p>Requires RSI sign-in in your browser and importer version 0.5.0. Discord is only needed to save your plan. For Chrome / Edge, extract the download and load its folder using Load unpacked on the browser extensions page. <a href="/assets/rsi-connector-extension-v0.5.0.zip">Chrome / Edge importer</a> · <a href="/assets/rsi-connector-extension-firefox-v0.5.0.zip">Firefox importer</a></p>
      <form data-ccu="upgrade">${field('From ship',shipSelect('source'))}${field('To ship',shipSelect('target'))}${field('Status',status())}${field('Purchase cost (optional)',number('cost'))}${field('Note (optional)',input('note','maxlength="200"'))}<button>Add CCU copy</button></form>
      ${plan.upgrades.map(c=>`<p>${esc(c.source)} → ${esc(c.target)} · ${esc(c.status)} · ${money(c.cost)} ${reserved(c.id)?`· Reserved: ${esc(reserved(c.id))}`:''} ${esc(c.note)} ${remove('upgrades',c.id)}</p>`).join('')}</details>
      <details><summary>Advanced: pledge values</summary><p>Values are looked up when you add ships. Correct missing or outdated values here; these are upgrade values, not what you paid.</p>
      <form data-ccu="ship">${field('Ship name',shipSelect('name'))}${field('Pledge value (USD)',input('value','type="number" min="0.01" max="10000" step="0.01" required'))}<button>Update pledge value</button></form>
      <p>${plan.ships.map(s=>`${esc(s.name)}: ${money(s.value)}`).join(' · ') || 'No ships added yet.'}</p></details>
      <h3>Your chains</h3>
      <div class="ccu-goals">${plan.goals.map(g=>`<article><h3>${esc(g.target)}</h3><p>${g.base_id?esc(plan.bases.find(b=>b.id===g.base_id)?.label):'Compare starting options'} · ${g.lti_only?'LTI required':'LTI not required'}</p>
      <p>${g.reserved_base?`Reserved: ${esc(plan.bases.find(b=>b.id===g.reserved_base)?.label)} and ${g.reserved_ccus.length} CCU copies.`:'No reservations.'}</p>
      <button type="button" data-find="${esc(g.id)}">Find routes</button> <button type="button" data-release="${esc(g.id)}">Release reservations</button> ${remove('goals',g.id)}
      ${g.saved_path?.length?`<p>Saved route: ${g.saved_path.map(esc).join(' → ')}. Find routes again to refresh costs.</p>`:''}<div data-results="${esc(g.id)}"></div></article>`).join('') || '<p>Enter a goal ship above to build your first chain. Base ship and owned CCUs can stay blank.</p>'}</div>
      <p>All gap and candidate costs are estimates, not live offers. Reservations protect individual bases and CCUs across goals. A re-import does not erase your planner.</p>`;
    workspace.querySelectorAll(':scope > details').forEach((d,i)=>{if(openSections[i]!==undefined)d.open=openSections[i];});
  }
  async function load() {
    if (dirty && !confirm('Replace your current unsaved draft with your saved account plan?')) return;
    const [data, hangar] = await Promise.all([request('/api/me/ccu'),request('/api/me/ships')]);
    plan = data; dirty = false; imported = hangar.filter(s=>s.ownership_type==='pledged');render();announce('Account planner loaded. Changes stay in this page until you save.');
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
      if(form.dataset.ccu==='base')next.bases.push({id:editId||uid(),ship:d.ship,label:d.label,status:d.status,cost:amount(d.cost),lti:!!d.lti});
      if(form.dataset.ccu==='upgrade') {
        d.source=await ensureShip(next,d.source);d.target=await ensureShip(next,d.target);
        if(next.ships.find(s=>s.name===d.source)?.value && next.ships.find(s=>s.name===d.target)?.value && next.ships.find(s=>s.name===d.source).value>=next.ships.find(s=>s.name===d.target).value)throw new Error('A CCU must move to a higher pledge value.');
        next.upgrades.push({id:editId||uid(),source:d.source,target:d.target,status:d.status,cost:amount(d.cost),note:d.note});
      }
      if(form.dataset.ccu==='goal'){
        d.target=await ensureShip(next,d.target);
        if(d.base_ship?.trim()){
          if(d.base_id)throw new Error('Choose either a base ship name or a saved base.');
          const ship=await ensureShip(next,d.base_ship);d.base_id=uid();
          next.bases.push({id:d.base_id,ship,label:ship,status:'owned',cost:null,lti:false});
        }
      }
      if(form.dataset.ccu==='goal')next.goals.push({id:editId||uid(),target:d.target,base_id:d.base_id,lti_only:!!d.lti_only,reserved_base:'',reserved_ccus:[],saved_path:[]});
      await save(next);
      if(form.dataset.ccu==='goal')await showRoutes(next.goals.at(-1).id);
    });
  });
  workspace.addEventListener('click',event=>{
    const b=event.target.closest('button');if(!b)return;
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
      out.innerHTML=result.routes.map(r=>`<details open><summary>${esc(names[r.mode])}</summary><p><strong>${esc(r.base.label)}</strong> (${r.base.id.startsWith('suggested:')?'suggested purchase · availability and LTI unverified':esc(r.base.status)}) → ${r.steps.map(e=>`${esc(e.target)} [${esc(e.status)}]`).join(' → ') || (r.base.status==='owned'?'Already at target':'Standalone purchase baseline')}</p>
      <p>Additional: <strong>${money(r.additional)}</strong> · Total: ${money(r.total)} · Savings: ${money(r.savings)} · Missing purchases: ${r.missing}</p>
      ${r.unknown_costs?`<p>${r.unknown_costs} purchase prices unknown. Known cost subtotal: ${money(r.known_cost)}. Total and savings cannot be confirmed.</p>`:''}
      <ul>${r.steps.map(e=>`<li>${esc(e.source)} → ${esc(e.target)} — ${esc(e.status)} · ${money(e.cost)} ${esc(e.note)}</li>`).join('')}</ul>
      <button type="button" data-reserve="${esc(id)}" data-mode="${r.mode}">Reserve this base and listed CCUs</button></details>`).join('') || '<p>Your goal is added. A price is missing, an LTI requirement needs a confirmed base, or reservations prevent a route. Check Advanced: pledge values or add a suitable base. You can leave this goal in your plan and fill details later.</p>';

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
      const result=await rsiConnect({action:'importCCUs'},120000);
      if(result.code!==200)throw new Error(result.code===400?'Update the RSI importer to version 0.5.0 using the download links, reload this page, and try again.':(result.error||'Install importer 0.5.0, sign into RSI, and try again.'));
      const next=structuredClone(plan);let added=0;
      for(const ccu of result.upgrades||[]){
        if(next.upgrades.some(c=>c.id===ccu.id))continue;
        const source=await ensureShip(next,ccu.source),target=await ensureShip(next,ccu.target);
        next.upgrades.push({id:ccu.id,source,target,status:'owned',cost:null,note:'RSI importer; purchase cost unknown'});added++;
      }
      await save(next);announce(`Imported ${added} CCU copies. Review names and values. Costs remain unknown until entered. Existing entries were kept.`);
    });
  });
  render();
})();
