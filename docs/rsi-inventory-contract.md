# RSI inventory boundary, version 1

Implementation: `tools/rsi-connector-extension/inventory.js`; browser consumer copy:
`web/rsi-inventory.js` (`globalThis.RSIInventory`, also CommonJS). No planner files are
owned by this change. This contract is additive to `importHangar` and `importCCUs`.

Both actions retain legacy `candidates` / `upgrades` on success and add `inventory`.
New consumers should request `importInventory` and use `inventory` even for a 206
partial response. Legacy actions fail closed on partial scans. No action applies
planner changes. The returned `preview` is review data only.

`inventory` contains `schema_version: 1`, `source: "rsi-hangar"`, `packages`,
`items`, `sync`, and `unsupported`. Each package has `id`, `identity` (`stable`
or `uncertain`), `rsi_id` (nullable), `title`, `ownership`, `insurance`,
`rsi_values`, and `provenance`. Each item adds `kind` (`ship`, `vehicle`, `ccu`),
`package_id`, `name`, `source_ship`, `target_ship`, `upgrade_base_eligible` and
`value_scope: "package"`. Ships describe current contents, not the ship named
in the original package title. Insurance is package evidence, not an assertion
about which child upgrade introduced it. All observed insurance terms survive.

Stable CCU IDs use the observed `a.js-apply-upgrade[rel]` identifier. A populated
`input.js-pledge-id[value]` supplies package identity when present. Empty IDs are
unknown. Child ships lack verified per-copy IDs: their observation IDs are NOT
stable, even inside a stable package. No name/hash/ordinal is a stable RSI ID.
Uncertain IDs are scoped to one scan; do not use them to reconcile automatically.
Never promote a legacy generated CCU ID to a verified stable ID by matching names.

`ownership: "owned"` requires the observed Attributed status on a hangar pledge.
Otherwise it is `unknown`; unverified records are not eligible upgrade bases.
No loaner inference or loaner-to-owned conversion is performed. Loaner
entitlements are not collected. Owned-hangar scans do not collect buybacks; the
separate opt-in action below collects buyback list records. The presence of a buyback navigation link proves nothing about a pledge.

`rsi_values: {pledge: null, melt: null}` means unknown, not zero. Monetary adapters
must provide `{amount_minor, currency, provenance}` only with verified units and
semantics. Current adapter does not infer money from hidden field names or store
MSRP. Values belong to the package; child rows may display the package value but
must sum it once per package. `actual_paid` is exclusively user data (including
grey-market payments); the importer never writes or estimates it.

`sync` describes expected/scanned/failed pages, truncation, parse warnings and
`complete`. Complete means the bounded owned-hangar scan was recognized and all
pages succeeded; it does not imply buybacks, loaners or prices were collected.
RSI page data can change during pagination; this is not a transaction snapshot.

`RSIInventory.preview(previousInventory, nextInventory)` returns added, changed,
missing, unchanged and uncertain entries. Only unique stable IDs are compared.
Missing is reported only for complete scans and is never a deletion instruction.
`RSIInventory.reconcile(previousInventory, nextInventory)` returns a proposed
inventory plus the preview, retaining missing/uncertain old records and every
existing user field (including actual_paid, protection and reservations). It
updates only importer-owned fields of unique stable matches, adds only stable
new records, and never assigns or releases planner items. The caller must scope
the baseline to the same RSI account and obtain review before accepting a proposal.
Account identity is not collected; switching RSI accounts requires a new baseline.

Privacy: only normalized, allowlisted inventory fields leave the extension.
Cookies, credentials, authenticated HTML, billing data, gift recipients, account
identifiers, coupon codes and scripts are never returned. Non-inventory packages
are omitted. Requests are GET-only to RSI's existing pledge and optional buyback list pages.

Verified markup source: read-only inspection of RSI My Hangar on 2026-10-30,
including the expanded Dragonfly two-pack and an unapplied 600i Explorer → Hull C
pledge. Fixtures are minimized/redacted from those observed structures. Cases
with populated hidden IDs or alternate status are explicitly synthetic edge tests,
not claims of live observed data. No CCU Game implementation or data was used.


## Additive 0.6.1 changes (2026-11-10)

The deployed planner now consumes this version 1 contract and offers explicit
add/match/skip choices. Hangar's legacy name consumer remains supported.

`importBuybacks` is an explicit, separate GET-only action returning the same
schema with `source: "rsi-buyback"` and `ownership: "buyback"`. It reads only
`/account/buy-back-pledges`; it never opens Buy Back controls or store APIs.
Verified list markup supplies `article.pledge`, title attributes and either
`data-pledgeid` or `/pledge/buyback/ID`. Fixtures redact actual IDs. Only ship/package
titles and explicit upgrade titles are retained. Historical Contained text is
not a current/reacquired ship list, so ship children are not inferred. Price,
availability and reacquired contents remain unsupported. This action is exposed
in the extension preview; the planner continues to request owned inventory only.
Comparison/reconciliation rejects mismatched sources.

Sync adds `started_at`, `finished_at`, and `last_successful_at` ISO timestamps.
Partial scans do not advance the last successful timestamp; reconciliation keeps
the prior one. The extension does not persist account inventory or timestamps;
a previous exported baseline supplies history in the local preview.
Pagination now discovers later links, scans up to 250 pages with three concurrent
requests and a 90-second scan budget (in-flight requests retain their 20-second
timeout). Limits/failures produce partial results. Website timeouts are 120 seconds.
Duplicate identities become unique uncertain observation IDs so the planner can
review them instead of rejecting the entire response.

Still outstanding: a verified monetary adapter, stable child-ship identity,
loaner collection, and one persistent shared inventory for Hangar and Planner.
Do not represent these limitations as implemented features. Current package/ship
markup was rechecked against RSI on 2026-11-10; monetary inputs were still empty
in the available browser DOM. No monetary units or API fields were guessed.
