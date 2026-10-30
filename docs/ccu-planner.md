# CCU planner implementation

The goal ship is the only required input. Catalog autocomplete and ship details
supply current USD upgrade values when available. Base ships and inventory are
optional; an unknown value permits a draft but cannot produce a priced route.
Guest validation and routes use the public stateless endpoints. Only explicit
account save/load uses Discord and the existing revision-checked account store.

## Routes and individual copies

The ascending-value graph compares lowest additional spending, lowest total with
known costs, and fewest missing purchases. Owned items add zero additional spend;
their actual paid costs still contribute to total cost. Unknown costs suppress
total and savings, rather than becoming zero. Suggested base purchases and gaps
are value-based estimates, never confirmed offers. No pledge-store reader exists.

Each route step has a stable planner key, displayed current endpoint values,
cost, insurance evidence, and remaining additional spend. Users can lock or
exclude a step, or replace it with an alternative that can still reach the goal
while respecting other locks. Constraints are per goal and saved with the plan.
Conflicting locks return no route; clearing constraints is explicit.

Protect excludes an individual base/CCU from new routes. Reservations exclude
copies from other goals, never their own goal. Locks cannot override protection,
missing import evidence, loaner classification, or another goal's reservation.
Existing reservations survive protection and re-import for review. This is
sequential per-goal allocation, not a globally optimal fleet solver.

## Inventory boundary

Consumes [inventory contract v1](rsi-inventory-contract.md) through
`rsiConnect(importInventory)`; falls back to `importCCUs` for legacy connectors.
No raw RSI HTML, credentials, cookies, or private APIs are consumed by the planner.
The importer implementation and its 0.6.0 packages are a separate dependency.

Imports are previewed before applying. Users confirm the RSI account and review
each observation. Stable IDs default to refresh/add; uncertain and legacy IDs
require an explicit choice. Child ships may be matched manually to an existing
planner entry, or explicitly added as new copies. Name matching never establishes
stable identity. Loaners and unverified ownership are ineligible. Duplicate
stable IDs cannot become multiple planner copies.

Refresh preserves user-entered costs, protection, internal copy IDs, constraints,
and reservations. Changed endpoints are flagged unavailable without rewriting the
old allocation. Complete scans flag absent stable copies; partial scans do not.
Nothing is automatically deleted. Unknown child identity requires manual review
on subsequent scans; switching RSI accounts requires a fresh draft/baseline.

`cost` retains the legacy semantics: actual paid for owned records, an entered
purchase estimate for missing/buyback records. RSI pledge/melt values are separate
fields. Package pledge/melt values are displayed as shared metadata and excluded
from per-child route cost. Actual paid package costs need an explicit allocation
to children; unknown allocations keep totals unknown. Imported package values
never overwrite actual paid. Insurance is evidence, not a guarantee of checkout
availability or the final insurance resulting from applying upgrades.

## Validation

- `python -m pytest tests/test_ccu_planner.py -q`: routing, constraints, accounting,
  per-copy reservations, guest endpoints, and account persistence/isolation.
- `node tests/ccu_planner_browser.cjs`: real planner HTML/JS with Python routing and
  validation, fixture catalog/import responses; guest goal-only use, controls,
  legacy/structured re-imports, partial scans, packages, loaners, explicit saves,
  desktop and 390px mobile overflow checks. Optional environment variables:
  `CCU_PYTHON`, `CCU_PLAYWRIGHT_PATH`, and `CCU_BROWSER_CHANNEL`.
- Browser screenshots are generated under `output/ccu-goal-first-*.png`.

No deploy or push is part of this implementation. Peep authentication and support
remain separate from the SC Companion public-install Discord application.
