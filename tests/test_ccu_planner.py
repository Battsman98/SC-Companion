import asyncio
from types import SimpleNamespace
import pytest
from pydantic import ValidationError
from fastapi.testclient import TestClient
from src.ccu_planner import Planner, find_routes
from src.cache import SQLiteCache
from src.ccu_storage import CcuStore
import src.web as web


def sample():
    return Planner.model_validate(dict(
        ships=[dict(name=n,value=v) for n,v in [('Base',40),('Middle',100),('Equal',100),('Target',200)]],
        bases=[dict(id='b',ship='Base',label='Pack ship 2',lti=True,cost=35)],
        upgrades=[dict(id='c',source='Base',target='Middle',cost=10),
                  dict(id='deal',source='Middle',target='Target',status='candidate',cost=20)],
        goals=[dict(id='g',target='Target',lti_only=True)]))


def test_goal_only_and_unknown_value_draft():
    plan = Planner.model_validate(dict(ships=[dict(name='Target',value=200)],
        goals=[dict(id='g',target='Target',lti_only=False)]))
    route = find_routes(plan,'g')[0]
    assert route['total'] == 200 and route['additional'] == 200
    assert route['base']['status'] == 'candidate' and not route['base']['lti']
    plan.ships[0].value = None
    assert find_routes(plan,'g') == []
    assert Planner.model_validate(plan.model_dump())


def test_no_base_uses_owned_ccu_without_inventing_lti():
    plan=sample();plan.bases=[];plan.goals[0].lti_only=False
    assert find_routes(plan,'g')[0]['additional'] == 60
    plan.goals[0].lti_only=True
    assert find_routes(plan,'g') == []


def test_owned_is_zero_additional_but_counts_in_total():
    r=find_routes(sample(),'g')[0]
    assert r['additional']==20
    assert r['total']==65
    assert r['savings']==135
    assert [e['id'] for e in r['steps']]==['c','deal']


def test_reservations_protect_other_goal_but_not_self():
    p=sample();p.goals[0].reserved_base='b';p.goals[0].reserved_ccus=['c']
    assert find_routes(p,'g')
    p.goals.append(p.goals[0].model_copy(update=dict(id='other',reserved_base='',reserved_ccus=[])))
    assert find_routes(p,'other')==[]


def test_unknown_owned_price_does_not_fake_savings():
    p=sample();p.upgrades[0].cost=None
    r=find_routes(p,'g')[0]
    assert r['additional']==20 and r['total'] is None and r['savings'] is None
    assert r['unknown_costs']==1
    total=next(r for r in find_routes(p,'g') if r['mode']=='total')
    assert total['total']==115  # known-cost standard bridge replaces unknown owned CCU


def test_lti_optional_start_and_buyback_cost():
    p=sample();p.bases[0].lti=False
    assert find_routes(p,'g')==[]
    p.goals[0].lti_only=False;p.goals[0].base_id='b';p.bases[0].status='buyback'
    assert find_routes(p,'g')[0]['additional']==55
    p.bases[0].cost=None
    assert find_routes(p,'g')==[]


def test_equal_value_and_downgrades_never_used():
    p=sample();p.upgrades[1].target='Equal'
    p.upgrades[1].source='Middle';p.upgrades[1].cost=0
    for r in find_routes(p,'g'):
        assert all(e['id']!='deal' for e in r['steps'])
    p.upgrades[0].source='Target';p.upgrades[0].target='Base'
    assert all(e['id']!='c' for r in find_routes(p,'g') for e in r['steps'])


def test_validation_rejects_double_allocation_and_nonfinite_prices():
    d=sample().model_dump();d['ships'][0]['value']=float('nan')
    with pytest.raises(ValidationError):Planner.model_validate(d)
    d=sample().model_dump();d['goals'][0]['reserved_ccus']=['c','c']
    with pytest.raises(ValidationError):Planner.model_validate(d)


def test_separate_copies_are_available_to_other_goals():
    p=sample();p.upgrades.append(p.upgrades[0].model_copy(update={'id':'c2'}))
    p.goals.append(p.goals[0].model_copy(update={'id':'other','reserved_ccus':['c']}))
    assert find_routes(p,'g')[0]['steps'][0]['id']=='c2'


def test_persistence_revision_and_account_isolation(tmp_path):
    async def run():
        cache=CcuStore(await SQLiteCache.create(str(tmp_path/'test.db')))
        assert await cache.save_ccu_plan(1,0,sample().model_dump())
        assert not await cache.save_ccu_plan(1,0,{})
        assert (await cache.ccu_plan(1))['revision']==1
        assert await cache.ccu_plan(2)=={'revision':0}
        assert await cache.save_ccu_plan(1,1,sample().model_dump())
        assert not await cache.save_ccu_plan(1,1,{})
    asyncio.run(run())


def test_routes_and_reserve_api(monkeypatch):
    class Cache:
        data={'revision':0}
        async def ccu_plan(self, user):return self.data
        async def save_ccu_plan(self,user,revision,data):
            if revision!=self.data['revision']:return False
            self.data={**data,'revision':revision+1};return True
    cache=Cache();monkeypatch.setattr(web,'ccu_store',lambda:cache)
    web.app.dependency_overrides[web.require_user]=lambda:SimpleNamespace(id=123)
    try:
        client=TestClient(web.app)
        assert client.put('/api/me/ccu',json=sample().model_dump()).status_code==200
        assert client.get('/api/me/ccu/routes/g').json()['routes'][0]['additional']==20
        assert client.post('/api/me/ccu/reserve/g/cash?revision=0').status_code==409
        saved=client.post('/api/me/ccu/reserve/g/cash?revision=1')
        assert saved.status_code==200
        assert saved.json()['goals'][0]['saved_path']==['Base','Middle','Target']
        assert saved.json()['goals'][0]['reserved_ccus']==['c','deal']
    finally:web.app.dependency_overrides.pop(web.require_user,None)


def test_guest_routes_validate_without_storage_or_login(monkeypatch):
    def forbidden_store():
        raise AssertionError('Guest planning must not access account storage')
    monkeypatch.setattr(web, 'ccu_store', forbidden_store)
    client = TestClient(web.app)
    draft = sample().model_dump()
    assert client.post('/api/ccu/validate', json=draft).json() == draft
    response = client.post('/api/ccu/routes/g', json=draft)
    assert response.status_code == 200
    assert response.json()['routes'][0]['total'] == 65
    assert client.post('/api/ccu/routes/missing', json=draft).status_code == 404
    draft['goals'][0]['reserved_base'] = 'missing'
    assert client.post('/api/ccu/validate', json=draft).status_code == 422
    monkeypatch.setattr(web, 'state', lambda: SimpleNamespace(settings=None))
    monkeypatch.setattr(web, 'current_user_from_request', lambda request, settings: None)
    assert client.put('/api/me/ccu', json=sample().model_dump()).status_code == 401


def test_default_goal_needs_no_base_or_inventory():
    p=Planner(ships=[dict(name='Target',value=200)],goals=[dict(id='g',target='Target')])
    assert find_routes(p,'g')[0]['additional']==200


def test_copy_protection_loaners_and_missing_inventory():
    p=sample();p.upgrades[0].protected=True
    assert all(e['id']!='c' for r in find_routes(p,'g') for e in r['steps'])
    p.bases[0].protected=True
    assert find_routes(p,'g')==[]
    p.bases[0].protected=False;p.bases[0].ownership='loaner'
    assert find_routes(p,'g')==[]
    p.bases[0].ownership='pledged';p.bases[0].import_missing=True
    assert find_routes(p,'g')==[]


def test_locked_excluded_and_alternative_steps():
    p=sample();g=p.goals[0]
    g.locked_steps=['ccu:c','ccu:deal']
    r=find_routes(p,'g')[0]
    assert [e['id'] for e in r['steps']]==['c','deal']
    assert not any(e['target']=='Target' for e in r['steps'][0]['alternatives'])
    g.excluded_steps=['ccu:c']
    assert find_routes(p,'g')==[]  # conflicts do not silently discard a lock
    g.locked_steps=[]
    assert all(e['id']!='c' for r in find_routes(p,'g') for e in r['steps'])
    g.locked_steps=['gap:Base→Middle']
    assert find_routes(p,'g')[0]['steps'][0]['status']=='gap'


def test_lock_does_not_override_another_goal_or_protection():
    p=sample();p.goals[0].locked_steps=['ccu:c']
    p.goals.append(p.goals[0].model_copy(update={'id':'other','reserved_ccus':['c']}))
    assert find_routes(p,'g')==[]
    p.goals.pop();p.upgrades[0].protected=True
    assert find_routes(p,'g')==[]


def test_rsi_and_package_values_never_substitute_actual_paid():
    p=sample();p.bases[0].cost=None;p.bases[0].package_id='two-pack';p.bases[0].package_value=100
    p.bases[0].pledge_value=100;p.bases[0].melt_value=80
    p.upgrades[0].cost=None;p.upgrades[0].pledge_value=60;p.upgrades[0].melt_value=5
    route=find_routes(p,'g')[0]
    assert route['total'] is None and route['savings'] is None
    assert route['additional']==20 and route['known_cost']==20
    assert route['steps'][0]['remaining']==20 and route['steps'][1]['remaining']==0
    assert route['steps'][0]['pledge_value']==60


def test_objectives_distinguish_paid_from_remaining_spend():
    p=sample();p.upgrades[0].cost=150
    routes={r['mode']:r for r in find_routes(p,'g')}
    assert routes['cash']['additional']==20 and routes['cash']['total']==205
    assert routes['total']['additional']==80 and routes['total']['total']==115


def test_all_offered_replacements_can_reach_goal_with_other_locks():
    p=sample();p.goals[0].locked_steps=['ccu:deal']
    for route in find_routes(p,'g'):
        for step in route['steps']:
            for alternative in step['alternatives']:
                proposal=p.model_copy(deep=True)
                proposal.goals[0].locked_steps=[k for k in p.goals[0].locked_steps if k!=step['key']]+[alternative['key']]
                assert find_routes(proposal,'g')


def test_stable_import_id_cannot_create_extra_copies():
    p=sample();p.upgrades[0].import_id='rsi-ccu:123';p.upgrades[0].import_identity='stable'
    p.upgrades.append(p.upgrades[0].model_copy(update={'id':'different-planner-id'}))
    with pytest.raises(ValidationError, match='stable RSI item'):
        Planner.model_validate(p.model_dump())
