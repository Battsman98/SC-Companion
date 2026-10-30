import asyncio
from datetime import datetime, timezone, timedelta

from src.cache import SQLiteCache
from src.ccu_deals import planner_market
from src.ccu_planner import Planner, find_routes


def market():
    return dict(fresh=True, checked_at=datetime.now(timezone.utc).isoformat(),
        catalog=[dict(name=n,value=v) for n,v in [('Base',40),('Middle',100),('Target',200)]],
        offers=[dict(name='Middle',sku_id=1,warbond_price=80,standard_price=100),
                dict(name='Target',sku_id=2,warbond_price=175,standard_price=200)],historical=[])


def plan():
    return Planner(ships=[dict(name='Target',value=200)],goals=[dict(id='g',target='Target')])


def test_goal_only_stacks_active_savings_without_inventory():
    route=find_routes(plan(),'g',market())[0]
    assert route['additional']==155 and route['total']==155
    assert route['savings']==45 and route['warbond_savings']==45
    assert all(not e['id'] for e in route['steps'])
    assert [e['status'] for e in route['steps']]==['active_warbond','active_warbond']


def test_active_off_and_stale_do_not_use_live_discounts():
    p=plan();p.use_active_warbonds=False
    assert find_routes(p,'g',market())[0]['warbond_savings']==0
    m=market();m['fresh']=False
    assert find_routes(plan(),'g',m)[0]['additional']==200


def test_historical_opt_in_quotes_and_unknown_total():
    p=plan();p.use_active_warbonds=False
    m=market();m['historical']=[dict(id='h',source='Base',target='Target',reference_cost=20,observed_at='2025-01-01')]
    assert find_routes(p,'g',m)[0]['additional']==200
    p.include_historical=True
    r=find_routes(p,'g',m)[0]
    assert r['additional']==60 and r['hypothetical'] and r['warbond_savings']==140
    p.deal_quotes={'historical:h':50}
    r=find_routes(p,'g',m)[0]
    assert r['additional']==90 and not r['hypothetical'] and r['warbond_savings']==110
    p=Planner.model_validate({**p.model_dump(),'bases':[dict(id='b',ship='Base',label='Owned',cost=None)],
        'ships':[dict(name='Base',value=40),dict(name='Target',value=200)]})
    r=find_routes(p,'g',m)[0]
    assert r['additional']==50 and r['total'] is None and r['savings'] is None


def test_historical_pairs_require_current_values_and_are_not_general_discounts():
    p=plan();p.include_historical=True
    m=market();m['historical']=[dict(id='h',source='Absent',target='Target',reference_cost=1,observed_at='2025')]
    assert all(e['status']!='historical_warbond' for r in find_routes(p,'g',m) for e in r['steps'])


def test_locks_exclusions_and_manufacturer_aliases():
    p=plan();p.ships[0].name='RSI Target';p.goals[0].target='RSI Target'
    p.goals[0].locked_steps=['active:1:Base']
    r=find_routes(p,'g',market())[0]
    assert r['steps'][0]['target']=='Middle' and r['steps'][-1]['target']=='RSI Target'
    p.goals[0].excluded_steps=['active:1:Base']
    assert find_routes(p,'g',market())==[]


def test_archive_keeps_specific_prices_sanitizes_locations_and_downgrades_stale(tmp_path):
    async def run():
        cache=await SQLiteCache.create(str(tmp_path/'market.db'))
        class Source:
            snapshot=dict(checked_at=datetime.now(timezone.utc).isoformat(),catalog=[],offers=[dict(
                name='Target',sku_id=1,warbond_price=175,standard_price=200,currency='USD',
                store_url='https://do-not-display.example',cheapest_source=dict(name='Middle',price=75))])
            async def active(self,force_refresh=False): return self.snapshot
        source=Source()
        first=await planner_market(source,cache)
        assert first['fresh'] and len(first['offers'])==1 and not first['historical']
        assert 'http' not in str(first)
        source.snapshot={**source.snapshot,'offers':[]}
        second=await planner_market(source,cache)
        assert second['historical'][0]['reference_cost']==75
        assert second['historical'][0]['source']=='Middle'
        source.snapshot['checked_at']=(datetime.now(timezone.utc)-timedelta(hours=2)).isoformat()
        result=await planner_market(source,cache)
        assert not result['fresh'] and not result['offers'] and result['warning']
    asyncio.run(run())
