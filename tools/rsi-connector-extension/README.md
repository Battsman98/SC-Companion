# RSI Hangar Importer 0.6.0

Reads RSI My Hangar locally using the browser's existing login session. Unofficial
fan extension; not affiliated with, endorsed or authorized by CIG/RSI.

## Install and preview

Chrome/Edge: extract web/rsi-connector-extension-v0.6.0.zip, enable Developer mode
on the Extensions page, and Load unpacked from the extracted folder.
Firefox: load web/rsi-connector-extension-firefox-v0.6.0.zip using Load Temporary
Add-on in about:debugging#/runtime/this-firefox. Temporary installations disappear
when Firefox closes; permanent installation requires Mozilla signing.

Sign into RSI in the same browser. Open the extension popup, select Preview RSI
inventory, then Read RSI hangar. Review packages, current ships, individual CCUs,
insurance and unknown fields. Download normalized JSON if desired. Load a previous
export from the same RSI account to compare added, changed, missing and uncertain
records. No planner changes are applied here.

Existing SC Companion import buttons remain compatible. New consumers can request
importInventory. See docs/rsi-inventory-contract.md for the versioned boundary and
reconciliation helpers. Planner integration is a separate change.

## Limits

- Stable CCU IDs come from the observed Apply upgrade control. Populated pledge ID
  inputs are used when available. Empty IDs and child ship IDs remain uncertain.
- Package membership and current ships remain separate. Galaxy and Paladin in the
  Dragonfly two-pack are not two purchases. Package values must be summed once.
- Insurance terms are package evidence. RSI pledge/melt amounts remain unknown
  until a verified monetary adapter exists. Manual actual-paid costs stay separate.
- Buybacks and loaner entitlements are not collected. Unknown ownership is never an
  eligible upgrade base. Navigation links do not establish ownership or buybacks.
- Up to three concurrent requests and 25 pages. Failures, unknown markup and
  truncation produce incomplete previews. Legacy imports fail closed.
- Comparisons never delete, reserve, release or reassign planner items. Switching
  RSI accounts requires a new baseline; account identity is not collected.

## Privacy

GET requests only to https://robertsspaceindustries.com/account/pledges and its
pagination using the local browser session. No cookies, credentials, authenticated
HTML, billing details, coupon codes or unrelated account data leave the extension.
Normalized data contains inventory titles, identities, package relationships,
insurance and scan diagnostics. Local preview sends nothing to SC Companion.
Inventory is not stored automatically.

The bridge allows https://sccompanion.org,
https://star-citizen-game-assist.onrender.com, and localhost/127.0.0.1 port 8000.
The extension's own preview page is additionally allowed.

## Validation and packaging

Run node --test tests/rsi_inventory.test.cjs and
py -3 -m pytest tests/test_rsi_connector_extension.py from the repository root.
Run py -3 scripts/package_rsi_connector.py to rebuild both ZIPs. The script checks
version agreement, shared helper equality and every packaged source file.
