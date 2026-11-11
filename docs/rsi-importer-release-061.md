# RSI importer 0.6.1 release review

Prepared November 10, 2026. Local build; not deployed or submitted to stores.
Based on production commit 4f2877d. Planner integration already supports schema
version 1 with reviewed add/match/skip choices; HangarLink CSV is unchanged.

Changes: explicit separate buyback preview, scan timestamps, later-page discovery,
250-page and 90-second scan limits, unique uncertain duplicate observations,
120-second legacy Hangar timeout, current same-site download links, updated privacy
disclosure, and matching Chrome/Edge and Firefox 0.6.1 archives.

Buyback listing markup was inspected directly in RSI. Reacquired ship contents,
prices and availability are not inferred from the list's historical contents.
Only package titles and explicit CCU endpoints are exported. The planner does not
automatically import buybacks or treat them as owned copies.

The following work remains beyond this release:

- Verify pledge/melt units and meanings before implementing a monetary adapter.
- Obtain verified child-ship identity or retain explicit manual matching.
- Integrate one persistent inventory across Hangar and Planner; the legacy Hangar
  still consumes names, while Planner consumes structured inventory.
- Verify packaged extension installation and authenticated scans end to end.
- Prepare store-specific listing/reviewer materials if store submission is requested.
  The older names-only submission drafts in the original project are obsolete;
  they must not accompany this build. The current public policy source is
  web/rsi-hangar-importer-privacy.html and the data contract is
  docs/rsi-inventory-contract.md. No authorization from CIG is claimed.

Privacy: importing is user-initiated; local preview does not send data to SC
Companion. Website imports send normalized fields described by the contract.
Cookies, credentials and authenticated HTML stay out of all exported payloads.
