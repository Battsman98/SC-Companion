"""Website-only, location-free warbond observations. Never infer historical inventory."""
import hashlib
from datetime import datetime, timezone

from src.ccu_storage import CcuStore


def fresh(snapshot):
    try:
        checked = datetime.fromisoformat(snapshot['checked_at'])
        age = (datetime.now(timezone.utc) - checked).total_seconds()
        return not snapshot.get('stale') and 0 <= age <= 1800
    except (KeyError, TypeError, ValueError):
        return False


async def planner_market(source, cache, force_refresh=False):
    try:
        snapshot = await source.active(force_refresh=force_refresh)
    except Exception:
        snapshot = {}
    current = fresh(snapshot)
    observations = []
    if current:
        for offer in snapshot.get('offers', []):
            # Retain the observed, specific CCU pair, not a historical discount
            # transplanted onto arbitrary present-day source ships.
            start = offer.get('cheapest_source')
            if offer.get('currency') != 'USD' or not start:
                continue
            cost = start.get('price')
            if not isinstance(cost, (int, float)) or cost <= 0:
                continue
            identity = f"{offer['sku_id']}|{start['name']}|{offer['name']}|{cost}"
            observations.append(dict(id=hashlib.sha256(identity.encode()).hexdigest()[:24],
                source=start['name'], target=offer['name'], reference_cost=cost,
                observed_at=snapshot['checked_at']))
    history = await CcuStore(cache).warbond_history(observations)
    active_pairs = {(o['source'], o['target'], o['reference_cost']) for o in observations}
    return dict(offers=[{k: o[k] for k in ('name', 'sku_id', 'warbond_price', 'standard_price')}
                        for o in snapshot.get('offers', []) if o.get('currency') == 'USD'] if current else [],
                catalog=[{'name': s['name'], 'value': s['price']} for s in snapshot.get('catalog', [])] if current else [],
                historical=[h for h in history if (h['source'], h['target'], h['reference_cost']) not in active_pairs],
                checked_at=snapshot.get('checked_at'), fresh=current,
                warning='' if current else 'Active warbond availability could not be verified. Active deals are excluded.')
