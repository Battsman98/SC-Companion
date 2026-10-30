# Active warbond discovery

The website reads the public RSI pledge store upgrade GraphQL endpoint at
`https://robertsspaceindustries.com/pledge-store/api/upgrade/graphql`.
It requires no Discord connection, RSI login, or stored ship allowlist.

Each scan discovers the ship catalog and pricing context, then requests current
`ship(id: ...)` upgrade SKUs for every discovered ship. The catalog's embedded
SKUs must not be used for availability: they include historical promotions.
Detail queries run in batches of five with at most four concurrent requests to
stay within RSI's GraphQL identifier limits.

Only currently available SKUs whose titles identify them as Warbond and whose
prices are positive and below the ship MSRP appear. Prices must be tax-exclusive
USD with exponent 2 and exchange rate 1. Other pricing contexts fail validation.
Images, ship links, MSRP, flight status, and unlimited-stock flags come from RSI.
The source suggestions use catalog MSRP; an unlimited suggestion additionally
requires an available non-warbond upgrade SKU with unlimited stock.

Successful results are cached for 15 minutes. The existing website maintenance
loop refreshes every 15 minutes, and the visible browser refreshes every 10
minutes. The Refresh button forces a new scan. No ship names or promotions are
maintained in source code or a curated backend list. The cache only holds the
last discovered response. The v6 cache key excludes the former fixed-list data.

Any HTTP, JSON, GraphQL, pricing, or incomplete-availability failure preserves
the last successful response with `stale: true` and its original verification
time. A fully successful empty scan replaces the previous offers with an empty
list. RSI schema or access changes require maintenance; failures must never be
presented as a successful empty scan.
