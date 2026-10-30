"""Account-owned planning data and monotonic CCU routing. No store scraping."""
from typing import Literal
from pydantic import BaseModel, Field, ConfigDict, model_validator


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, str_strip_whitespace=True)


class Ship(Record):
    name: str = Field(min_length=1, max_length=100)
    value: float | None = Field(default=None, gt=0, le=10000)


class Base(Record):
    id: str = Field(min_length=1, max_length=80)
    ship: str
    label: str = Field(min_length=1, max_length=150)
    status: Literal["owned", "buyback", "candidate"] = "owned"
    lti: bool = False
    cost: float | None = Field(default=None, ge=0, le=10000)
    protected: bool = False
    ownership: str = Field(default="pledged", max_length=40)
    insurance: str = Field(default="Unknown", max_length=120)
    package_id: str = Field(default="", max_length=160)
    package_value: float | None = Field(default=None, ge=0, le=100000)
    package_melt_value: float | None = Field(default=None, ge=0, le=100000)
    pledge_value: float | None = Field(default=None, ge=0, le=10000)
    melt_value: float | None = Field(default=None, ge=0, le=10000)
    import_missing: bool = False
    import_id: str = Field(default="", max_length=160)
    import_identity: Literal["", "stable", "uncertain", "legacy"] = ""


class Upgrade(Record):
    id: str = Field(min_length=1, max_length=80)
    source: str
    target: str
    status: Literal["owned", "buyback", "candidate"] = "owned"
    cost: float | None = Field(default=None, ge=0, le=10000)
    note: str = Field(default="", max_length=200)
    protected: bool = False
    insurance: str = Field(default="Unknown", max_length=120)
    pledge_value: float | None = Field(default=None, ge=0, le=10000)
    melt_value: float | None = Field(default=None, ge=0, le=10000)
    import_missing: bool = False
    import_id: str = Field(default="", max_length=160)
    import_identity: Literal["", "stable", "uncertain", "legacy"] = ""
    package_id: str = Field(default="", max_length=160)
    package_value: float | None = Field(default=None, ge=0, le=100000)
    package_melt_value: float | None = Field(default=None, ge=0, le=100000)


class Goal(Record):
    id: str = Field(min_length=1, max_length=80)
    target: str
    base_id: str = ""
    lti_only: bool = False
    reserved_base: str = ""
    reserved_ccus: list[str] = Field(default_factory=list, max_length=400)
    saved_path: list[str] = Field(default_factory=list, max_length=251)
    excluded_steps: list[str] = Field(default_factory=list, max_length=400)
    locked_steps: list[str] = Field(default_factory=list, max_length=30)


class Planner(Record):
    revision: int = Field(default=0, ge=0)
    ships: list[Ship] = Field(default_factory=list, max_length=250)
    bases: list[Base] = Field(default_factory=list, max_length=200)
    upgrades: list[Upgrade] = Field(default_factory=list, max_length=400)
    goals: list[Goal] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def consistent(self):
        names = {s.name for s in self.ships}
        if len({s.name.casefold() for s in self.ships}) != len(self.ships):
            raise ValueError("Ship names must be unique (including capitalization).")
        for collection in (self.bases, self.upgrades, self.goals):
            if len({x.id for x in collection}) != len(collection):
                raise ValueError("Each pledge, CCU copy and goal needs a unique ID.")
        imported_ids = [x.import_id for x in [*self.bases, *self.upgrades]
                        if x.import_identity == 'stable' and x.import_id]
        if len(set(imported_ids)) != len(imported_ids):
            raise ValueError("A stable RSI item cannot represent two planner copies.")
        bases = {b.id for b in self.bases}
        ccus = {c.id for c in self.upgrades}
        for b in self.bases:
            if b.ship not in names:
                raise ValueError("Add the base ship's pledge value first.")
        for c in self.upgrades:
            if c.source not in names or c.target not in names:
                raise ValueError("Both CCU ships need pledge values.")
        allocated = set()
        for g in self.goals:
            if g.target not in names or (g.base_id and g.base_id not in bases):
                raise ValueError("Goal refers to a missing ship or base.")
            if g.reserved_base and g.reserved_base not in bases:
                raise ValueError("Release the saved base before removing it.")
            if any(c not in ccus for c in g.reserved_ccus):
                raise ValueError("Release reserved CCUs before removing them.")
            tokens = (["base:" + g.reserved_base] if g.reserved_base else []) + ["ccu:" + c for c in g.reserved_ccus]
            for token in tokens:
                if token in allocated:
                    raise ValueError("A base or CCU copy cannot be reserved twice.")
                allocated.add(token)
        return self


def find_routes(plan: Planner, goal_id: str) -> list[dict]:
    goal = next((g for g in plan.goals if g.id == goal_id), None)
    if goal is None:
        raise ValueError("Goal not found.")
    prices = {s.name: round(s.value * 100) for s in plan.ships if s.value is not None}
    if goal.target not in prices:
        return []
    blocked_bases = {g.reserved_base for g in plan.goals if g.id != goal_id}
    blocked_ccus = {c for g in plan.goals if g.id != goal_id for c in g.reserved_ccus}
    bases = [b for b in plan.bases if not b.protected and not b.import_missing and b.ownership == "pledged" and b.ship in prices and b.id not in blocked_bases and (not goal.base_id or goal.base_id == b.id)
             and (not goal.lti_only or b.lti) and prices[b.ship] <= prices[goal.target]
             and (b.status == "owned" or b.cost is not None)]
    if not goal.base_id and not goal.lti_only:
        # No starting pledge is required. Compare buying known starting ships,
        # including the target itself as a standalone-price baseline.
        bases += [Base(id='suggested:' + str(i), ship=s.name,
                       label='Suggested purchase: ' + s.name, status='candidate',
                       cost=s.value) for i, s in enumerate(plan.ships)
                  if s.name in prices and prices[s.name] <= prices[goal.target]]
    nodes = sorted((n for n in prices if prices[n] <= prices[goal.target]), key=lambda n: (prices[n], n))
    outgoing = {n: [] for n in nodes}
    for c in plan.upgrades:
        if c.protected or c.import_missing or c.id in blocked_ccus or c.source not in outgoing or c.target not in outgoing:
            continue
        if prices[c.source] >= prices[c.target] or (c.status != "owned" and c.cost is None):
            continue
        outgoing[c.source].append(dict(id=c.id, source=c.source, target=c.target, status=c.status,
            key="ccu:" + c.id, insurance=c.insurance, pledge_value=c.pledge_value, melt_value=c.melt_value,
            cost=None if c.cost is None else round(c.cost * 100), note=c.note))
    for i, source in enumerate(nodes):
        for target in nodes[i + 1:]:
            if prices[target] > prices[source]:
                outgoing[source].append(dict(id="", source=source, target=target, status="gap",
                    key="gap:" + source + "→" + target, insurance="Unverified",
                    cost=prices[target]-prices[source], note="Theoretical value difference; not a confirmed offer."))

    outgoing = {n: [e for e in edges if e['key'] not in goal.excluded_steps] for n, edges in outgoing.items()}
    edges_by_key = {e['key']: e for edges in outgoing.values() for e in edges}
    if any(key not in edges_by_key for key in goal.locked_steps):
        return []
    locks = sorted(set(goal.locked_steps), key=lambda key: prices[edges_by_key[key]['source']])
    reachable = {}
    for node in reversed(nodes):
        reachable[node] = {node}
        for edge in outgoing[node]:
            reachable[node].update(reachable[edge['target']])

    def feasible_alternative(base, original, alternative):
        required_keys = [key for key in locks if key != original['key']]
        required_keys = sorted(set(required_keys + [alternative['key']]),
                               key=lambda key: prices[edges_by_key[key]['source']])
        node = base.ship
        for key in required_keys:
            edge = edges_by_key[key]
            if edge['source'] not in reachable[node]:
                return False
            node = edge['target']
        return goal.target in reachable[node]

    def rank(path, mode):
        cash, total, missing, unknown, steps = path
        return {"cash": (cash, missing, unknown, len(steps)),
                "total": (total, cash, missing, len(steps)),
                "gaps": (missing, cash, unknown, len(steps))}[mode]

    results = []
    for mode in ("cash", "total", "gaps"):
        winner = None
        for base in bases:
            if locks and prices[base.ship] > prices[edges_by_key[locks[0]]['source']]:
                continue
            if mode == "total" and base.cost is None:
                continue
            cost = round((base.cost or 0) * 100)
            best = {(base.ship, 0): (0 if base.status == "owned" else cost, cost, int(base.status != "owned"), int(base.cost is None), [])}
            for node in nodes:
                for progress in range(len(locks) + 1):
                    if (node, progress) not in best:
                        continue
                    cash, total, missing, unknown, steps = best[node, progress]
                    for edge in outgoing[node]:
                        advanced = progress
                        if progress < len(locks):
                            required = edges_by_key[locks[progress]]
                            if edge['key'] == required['key']:
                                advanced += 1
                            elif prices[edge['target']] > prices[required['source']]:
                                continue
                        if mode == "total" and edge["cost"] is None:
                            continue
                        ec = edge["cost"] or 0
                        path = (cash + (0 if edge["status"] == "owned" else ec), total + ec,
                                missing + int(edge["status"] != "owned"), unknown + int(edge["cost"] is None), steps + [edge])
                        old = best.get((edge["target"], advanced))
                        if old is None or rank(path, mode) < rank(old, mode):
                            best[edge["target"], advanced] = path
            path = best.get((goal.target, len(locks)))
            if path is not None and (winner is None or rank(path, mode) < rank(winner[1], mode)):
                winner = (base, path)
        if winner:
            base, (cash, total, missing, unknown, steps) = winner
            def display(edge):
                return {**edge, 'cost': None if edge['cost'] is None else edge['cost']/100,
                        'source_value': prices[edge['source']]/100, 'target_value': prices[edge['target']]/100}
            rendered = []
            remaining = cash - (0 if base.status == 'owned' else round((base.cost or 0)*100))
            for edge in steps:
                remaining -= 0 if edge['status'] == 'owned' else (edge['cost'] or 0)
                rendered.append({**display(edge), 'remaining': remaining/100,
                    'alternatives': [display(e) for e in outgoing[edge['source']]
                                     if e['key'] != edge['key'] and feasible_alternative(base, edge, e)]})
            results.append(dict(mode=mode, base=base.model_dump(), steps=rendered,
                estimated=bool(missing),
                additional=cash/100, total=None if unknown else total/100, known_cost=total/100,
                savings=None if unknown else (prices[goal.target]-total)/100, unknown_costs=unknown, missing=missing))
    return results
