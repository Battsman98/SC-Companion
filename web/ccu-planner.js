/* Uses existing authenticated API and RSI-imported hangar. No RSI/store requests. */
(() => {
  const root = document.querySelector('#ccuPlanner');
  const workspace = document.querySelector('#ccuWorkspace');
  const notice = document.querySelector('#ccuNotice');
  let plan, imported = [], busy = false;
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
  const shipSelect = name => choices(name, plan.ships.map(s => option(s.name, `${s.name} (${money(s.value)})`)).join(''));
  const names = {cash:'Lowest additional spending', total:'Lowest total — known costs only', gaps:'Fewest missing upgrades'};
  function announce(text, error = false) {notice.textContent = text; notice.className = error ? 'error' : 'state';}
  async function request(path, method='GET', body) {
    const response = await fetch(path, {method, headers:body ? {'Content-Type':'application/json'} : {}, body:body ? JSON.stringify(body) : undefined});
    const result = await response.json();
    if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : 'Please check the entered values and references.');
    return result;
  }
  async function action(work) {
    if (busy) return;
    busy = true; root.setAttribute('aria-busy', 'true');
    root.querySelectorAll('button').forEach(b => b.disabled = true);
    try {await work();} catch(error) {announce(error.message, true);} finally {
      busy = false; root.removeAttribute('aria-busy');root.querySelectorAll('button').forEach(b => b.disabled = false);
    }
  }
  async function save(next) {
    plan = await request('/api/me/ccu', 'PUT', next);
    render(); announce('Planner saved. Costs are USD before tax and fees.');
  }
  const remove = (kind, id) => `<button type="button" data-edit="${kind}" data-id="${esc(id)}">Edit</button> <button type="button" data-remove="${kind}" data-id="${esc(id)}">Remove</button>`;
  function render() {
    const openSections = [...workspace.querySelectorAll(':scope > details')].map(d=>d.open);
    workspace.hidden = false;
    const reserved = id => plan.goals.filter(g => g.reserved_base === id || g.reserved_ccus.includes(id)).map(g=>g.target).join(', ');
    workspace.innerHTML = `
      <details ${plan.ships.length ? '' : 'open'}><summary>1. Ship values (${plan.ships.length})</summary>
      <p>Add ships used by your bases, upgrades and goals. These are editable USD pledge values, not in-game prices. Store reading is deferred.</p>
      <form data-ccu="ship">${field('Ship name',input('name','list="ccuNames" required maxlength="100"'))}${field('Pledge value (USD)',input('value','type="number" min="0.01" max="10000" step="0.01" required'))}<button>Add / update ship value</button></form>
      <datalist id="ccuNames">${[...new Set([...plan.ships.map(s=>s.name), ...imported.map(s=>s.name)])].map(s=>option(s,s)).join('')}</datalist>
      <p>${plan.ships.map(s=>`${esc(s.name)}: ${money(s.value)}`).join(' · ') || 'No ship values yet.'}</p></details>
      <details><summary>2. Starting pledges (${plan.bases.length})</summary>
      <p>Imported pledged ships: ${imported.map(s=>esc(s.name)).join(', ') || 'None. Use Import RSI Hangar in Hangar, then refresh here.'}</p>
      <p>The importer currently supplies names only. Add one row per individual pledge or pack ship. LTI unchecked means unknown/non-LTI. Never add loaners or in-game ships as upgrade bases.</p>
      <form data-ccu="base">${field('Ship',shipSelect('ship'))}${field('Pledge label / pack ship',input('label','required maxlength="150" placeholder="Dragonfly two-pack — ship 2"'))}${field('Status',status())}${field('Paid / buyback / target cost',number('cost'))}${field('Confirmed LTI',input('lti','type="checkbox"'))}<button>Add separate base</button></form>
      ${plan.bases.map(b=>`<p><strong>${esc(b.label)}</strong> — ${esc(b.ship)} · ${esc(b.status)} · ${b.lti?'LTI':'LTI not confirmed'} · ${money(b.cost)} ${reserved(b.id)?`· Reserved: ${esc(reserved(b.id))}`:''} ${remove('bases',b.id)}</p>`).join('')}</details>
      <details><summary>3. CCU inventory and targets (${plan.upgrades.length})</summary>
      <p>Add each copy separately. Cost means actual paid for owned CCUs, or a verified buyback price / target budget for missing CCUs. Leave unknown prices blank; do not substitute melt value.</p>
      <form data-ccu="upgrade">${field('From',shipSelect('source'))}${field('To',shipSelect('target'))}${field('Status',status())}${field('Cost (USD)',number('cost'))}${field('Price source / date / note',input('note','maxlength="200" placeholder="WB target, not confirmed stock"'))}<button>Add CCU copy</button></form>
      ${plan.upgrades.map(c=>`<p>${esc(c.source)} → ${esc(c.target)} · ${esc(c.status)} · ${money(c.cost)} ${reserved(c.id)?`· Reserved: ${esc(reserved(c.id))}`:''} ${esc(c.note)} ${remove('upgrades',c.id)}</p>`).join('')}</details>
      <h3>Fleet goals</h3>
      <form data-ccu="goal">${field('Target ship',shipSelect('target'))}${field('Optional starting pledge',choices('base_id',option('','Find a suitable base')+plan.bases.map(b=>option(b.id,b.label)).join('')))}${field('LTI only',input('lti_only','type="checkbox" checked'))}<button>Add goal</button></form>
      <div class="ccu-goals">${plan.goals.map(g=>`<article><h3>${esc(g.target)}</h3><p>${g.base_id?esc(plan.bases.find(b=>b.id===g.base_id)?.label):'Find suitable base'} · ${g.lti_only?'LTI required':'Any confirmed pledge base'}</p>
      <p>${g.reserved_base?`Reserved: ${esc(plan.bases.find(b=>b.id===g.reserved_base)?.label)} and ${g.reserved_ccus.length} CCU copies.`:'No reservations.'}</p>
      <button type="button" data-find="${esc(g.id)}">Find routes</button> <button type="button" data-release="${esc(g.id)}">Release reservations</button> ${remove('goals',g.id)}
      ${g.saved_path?.length?`<p>Saved route: ${g.saved_path.map(esc).join(' → ')}. Find routes again to refresh costs.</p>`:''}<div data-results="${esc(g.id)}"></div></article>`).join('') || '<p>Add ship values, a starting pledge and your first goal to begin.</p>'}</div>
      <p>All gap and candidate costs are estimates, not live offers. Reservations protect individual bases and CCUs across goals. A re-import does not erase your planner.</p>`;
    workspace.querySelectorAll(':scope > details').forEach((d,i)=>{if(openSections[i]!==undefined)d.open=openSections[i];});
  }
  async function load() {
    const [data, hangar] = await Promise.all([request('/api/me/ccu'),request('/api/me/ships')]);
    plan = data; imported = hangar.filter(s=>s.ownership_type==='pledged');render();announce('Planner loaded. Imported hangar names are available in Ship values.');
  }
  document.querySelector('#ccuReload').addEventListener('click',()=>action(load));
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
      if(form.dataset.ccu==='base')next.bases.push({id:editId||uid(),ship:d.ship,label:d.label,status:d.status,cost:amount(d.cost),lti:!!d.lti});
      if(form.dataset.ccu==='upgrade') {
        if(next.ships.find(s=>s.name===d.source)?.value>=next.ships.find(s=>s.name===d.target)?.value)throw new Error('A CCU must move to a higher pledge value.');
        next.upgrades.push({id:editId||uid(),source:d.source,target:d.target,status:d.status,cost:amount(d.cost),note:d.note});
      }
      if(form.dataset.ccu==='goal')next.goals.push({id:editId||uid(),target:d.target,base_id:d.base_id,lti_only:!!d.lti_only,reserved_base:'',reserved_ccus:[],saved_path:[]});
      await save(next);
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
    if(b.dataset.find)action(async()=>{
      const id=b.dataset.find;const result=await request(`/api/me/ccu/routes/${encodeURIComponent(id)}`);
      if(result.revision!==plan.revision)throw new Error('Plan changed in another tab. Refresh the planner.');
      const out=workspace.querySelector(`[data-results="${CSS.escape(id)}"]`);
      out.innerHTML=result.routes.map(r=>`<details open><summary>${esc(names[r.mode])}</summary><p><strong>${esc(r.base.label)}</strong> (${esc(r.base.status)}) → ${r.steps.map(e=>`${esc(e.target)} [${esc(e.status)}]`).join(' → ') || 'Already at target'}</p>
      <p>Additional: <strong>${money(r.additional)}</strong> · Total: ${money(r.total)} · Savings: ${money(r.savings)} · Missing purchases: ${r.missing}</p>
      ${r.unknown_costs?`<p>${r.unknown_costs} purchase prices unknown. Known cost subtotal: ${money(r.known_cost)}. Total and savings cannot be confirmed.</p>`:''}
      <ul>${r.steps.map(e=>`<li>${esc(e.source)} → ${esc(e.target)} — ${esc(e.status)} · ${money(e.cost)} ${esc(e.note)}</li>`).join('')}</ul>
      <button type="button" data-reserve="${esc(id)}" data-mode="${r.mode}">Reserve this base and listed CCUs</button></details>`).join('') || '<p>No eligible route. Add a priced starting base below the target, confirm LTI, or release a conflicting reservation. Equal-value upgrades are not allowed.</p>';
    });
    if(b.dataset.reserve)action(async()=>{plan=await request(`/api/me/ccu/reserve/${encodeURIComponent(b.dataset.reserve)}/${b.dataset.mode}?revision=${plan.revision}`,'POST');render();announce('Reserved for this goal. Other goals will not use these copies.');});
  });
})();
