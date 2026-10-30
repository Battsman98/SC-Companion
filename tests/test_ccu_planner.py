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
        goals=[dict(id='g',target='Target')]))


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
