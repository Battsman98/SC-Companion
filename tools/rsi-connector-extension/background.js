if (typeof importScripts === "function") importScripts("inventory.js");

async function rsiHTMLGet(url) {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), 20000);
  let response;
  try {
    response = await fetch(url, {
      method: "GET",
      credentials: "include",
      headers: {
        "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "accept-language": "en-US,en;q=0.9",
        "cache-control": "max-age=0"
      },
      signal: controller.signal
    });
    return { code: response.status, payload: await response.text(), url: response.url };
  } finally {
    clearTimeout(timeoutId);
  }
}

function cleanShipName(value) {
  let name = String(value || "").replace(/[-_]+/g, " ").replace(/\s+/g, " ").trim();
  name = name.replace(/^(?:Standalone Ship|Game Package|Package|Ship)\s*[-:]?\s*/i, "");
  name = name.replace(/\b(?:with Lifetime Insurance|Lifetime Insurance|Warbond|Best In Show|BIS|ILW|IAE|LTI)\b.*$/i, "").trim();
  if (name.length < 2 || name.length > 72 || name.split(" ").length > 10) return null;
  const blocked = /\b(?:upgrade|paint|skin|flair|poster|plushie|figurine|gift card|coupon|currency|insurance|hangar|downloadable|weapon|armor)\b|\bto\b.*\b(?:year|insurance)\b|\b\d+\s*year\b/i;
  return blocked.test(name) ? null : name;
}

function htmlText(value) {
  return String(value || "")
    .replace(/<[^>]+>/g, " ")
    .replace(/&amp;/gi, "&")
    .replace(/&quot;/gi, '"')
    .replace(/&#(?:39|x27);/gi, "'")
    .replace(/\s+/g, " ")
    .trim();
}

function extractTypedItemShips(pageHTML) {
  const candidates = new Set();
  const kindPattern = /class=["'][^"']*\bkind\b[^"']*["'][^>]*>([\s\S]{0,240}?)<\/[^>]+>/gi;
  const titlePattern = /class=["'][^"']*\btitle\b[^"']*["'][^>]*>([\s\S]{0,240}?)<\/[^>]+>/gi;
  for (const kind of pageHTML.matchAll(kindPattern)) {
    if (!/^\s*(?:ship|vehicle)\s*$/i.test(htmlText(kind[1]))) continue;
    const kindIndex = kind.index || 0;
    const before = pageHTML.slice(Math.max(0, kindIndex - 900), kindIndex);
    const after = pageHTML.slice(kindIndex + kind[0].length, kindIndex + kind[0].length + 900);
    const precedingTitles = [...before.matchAll(titlePattern)];
    const title = precedingTitles.at(-1) || [...after.matchAll(titlePattern)][0];
    if (!title) continue;
    const cleaned = cleanShipName(htmlText(title[1]));
    if (cleaned) candidates.add(cleaned);
  }
  return candidates;
}

function extractShipCandidates(pageHTML) {
  return [...extractTypedItemShips(pageHTML)];
}

function pledgePageCount(pageHTML) {
  let count = 1;
  for (const match of String(pageHTML || "").matchAll(/[?&]page=(\d{1,3})/gi)) {
    count = Math.max(count, Number(match[1]) || 1);
  }
  return Math.min(25, count);
}

function countMatches(value, pattern) {
  return [...String(value || "").matchAll(pattern)].length;
}

function fnv1a(value) {
  let hash = 0x811c9dc5;
  for (const character of String(value || "")) {
    hash ^= character.charCodeAt(0);
    hash = Math.imul(hash, 0x01000193) >>> 0;
  }
  return hash.toString(16).padStart(8, "0");
}

function pageStructure(pageHTML) {
  return [
    /\bjs-pledge-name\b/i.test(pageHTML) ? "pledge-name" : "no-pledge-name",
    /class=["'][^"']*\bkind\b/i.test(pageHTML) ? "kind" : "no-kind",
    /class=["'][^"']*\btitle\b/i.test(pageHTML) ? "title" : "no-title",
    /class=["'][^"']*\btitle\b[\s\S]{0,900}class=["'][^"']*\bkind\b/i.test(pageHTML)
      ? "title-before-kind"
      : "no-title-before-kind",
    /class=["'][^"']*\bkind\b[\s\S]{0,900}class=["'][^"']*\btitle\b/i.test(pageHTML)
      ? "kind-before-title"
      : "no-kind-before-title",
  ].join(":");
}

function importDiagnostics(scannedPages, pageCount, typedCandidates) {
  const orderedPages = [...scannedPages].sort((left, right) => left.page - right.page);
  const structure = pageStructure(orderedPages.map((item) => item.payload).join(""));
  return {
    connector_version: "0.6.0",
    parser_version: "structured-inventory-v1",
    page_count: pageCount,
    scanned_pages: orderedPages.length,
    pledge_count: orderedPages.reduce(
      (total, item) => total + countMatches(item.payload, /\bjs-pledge-name\b/gi),
      0,
    ),
    typed_candidates: typedCandidates,
    markup_fingerprint: fnv1a(structure),
  };
}

async function importHangar(reportProgress = () => {}, ccusOnly = false, structured = false) {
  const scanId = crypto.randomUUID();
  const scannedPages = [], failedPages = [];
  let totalPages = 1, truncated = false, completed = 0;
  const candidates = new Set();
  const fetchPage = async page => {
    let response;
    try { response = await rsiHTMLGet("https://robertsspaceindustries.com/account/pledges" + (page > 1 ? "?page=" + page : "")); }
    catch (_) { failedPages.push({page, reason:"timeout-or-network"}); return; }
    let url;
    try { url = new URL(response.url); } catch (_) {}
    if (response.code !== 200 || !url || url.hostname !== "robertsspaceindustries.com" || !/\/account\/pledges\/?$/.test(url.pathname)) {
      failedPages.push({page, reason:response.code === 200 ? "sign-in-or-unexpected-page" : "http-" + response.code}); return;
    }
    if (page === 1) {
      const requested = [...response.payload.matchAll(/[?&](?:amp;)?page=(\d+)/gi)].map(m=>Number(m[1]));
      const advertised = Math.max(1, ...requested);
      totalPages = Math.min(25, advertised); truncated = advertised > totalPages;
    }
    if (scannedPages.some(p=>p.payload === response.payload)) {
      failedPages.push({page,reason:"repeated-page-content"}); return;
    }
    scannedPages.push({page, payload:response.payload});
    const parsed = RSIInventory.parsePage(response.payload, page, scanId);
    for (const item of parsed.items) if ((item.kind === "ship" || item.kind === "vehicle") && item.ownership === "owned") candidates.add(item.name);
  };
  await fetchPage(1);
  completed++;
  reportProgress({phase:"scanning", page:completed, totalPages, candidates:candidates.size, failedPages:failedPages.length});
  if (!scannedPages.length) return {code:failedPages[0]?.reason === "sign-in-or-unexpected-page" ? 401 : 504, error:"RSI hangar could not be read. Sign into RSI and retry.", failed_pages:failedPages};
  // Bounded concurrency avoids a burst of 24 authenticated requests.
  let nextPage = 2;
  await Promise.all(Array.from({length:Math.min(3,totalPages-1)}, async () => {
    while (nextPage <= totalPages) {
      const page = nextPage++;
      await fetchPage(page); completed++;
      reportProgress({phase:"scanning", page:completed, totalPages, candidates:candidates.size, failedPages:failedPages.length});
    }
  }));
  scannedPages.sort((a,b)=>a.page-b.page);
  const inventory = RSIInventory.normalize(scannedPages, {expected_pages:totalPages, scanned_pages:scannedPages.map(p=>p.page), failed_pages:failedPages,
    truncated, complete:!truncated && !failedPages.length}, scanId);
  const diagnostics = importDiagnostics(scannedPages,totalPages,candidates.size);
  const preview = RSIInventory.preview(null,inventory);
  const result = {code:inventory.sync.complete ? 200 : 206, inventory, preview, diagnostics};
  reportProgress({phase:"preview", page:completed, totalPages, records:inventory.items.length, complete:inventory.sync.complete});
  if (structured) return result;
  if (!inventory.sync.complete) return {...result, code:504, error:"The hangar scan is incomplete. No legacy import was applied; review the partial inventory or retry."};
  if (ccusOnly) {
    const counts = new Map();
    // Keep pre-v1 legacy IDs for old consumers. V1 consumers use inventory.items.
    result.upgrades = inventory.items.filter(r=>r.kind === "ccu" && r.ownership === "owned").map(item => {
      const source = cleanShipName(item.source_ship), target = cleanShipName(item.target_ship);
      if (!source || !target) return null;
      const key = source + " to " + target;
      const copy = (counts.get(key)||0)+1; counts.set(key,copy);
      return {source, target, id:"rsi-ccu:"+fnv1a(key)+":"+copy};
    }).filter(Boolean);
  } else result.candidates = [...candidates].sort((a,b)=>a.localeCompare(b));
  return result;
}

const ALLOWED_WEBSITE_ORIGINS = new Set([
  "https://sccompanion.org",
  "https://star-citizen-game-assist.onrender.com",
  "http://127.0.0.1:8000",
  "http://localhost:8000"
]);

chrome.runtime.onMessage.addListener((rawMessage, sender, sendResponse) => {
  let senderOrigin = "";
  try {
    senderOrigin = new URL(sender.url || "").origin;
  } catch (_error) {
    sendResponse(JSON.stringify({ code: 403, error: "Unrecognized website origin." }));
    return false;
  }
  const localPreview = sender.url === chrome.runtime.getURL("preview.html");
  if (!localPreview && !ALLOWED_WEBSITE_ORIGINS.has(senderOrigin)) {
    sendResponse(JSON.stringify({ code: 403, error: "Website origin is not allowed." }));
    return false;
  }
  let requestId = "";
  try {
    requestId = String(JSON.parse(rawMessage || "{}").requestId || "");
  } catch (_error) {}
  const reportProgress = (progress) => {
    if (!sender.tab?.id || !requestId) return;
    chrome.tabs.sendMessage(sender.tab.id, {
      direction: "from-game-assist-rsi-progress",
      requestId,
      progress,
    }, () => void chrome.runtime.lastError);
  };
  handleMessage(rawMessage, reportProgress).then((response) => {
    sendResponse(JSON.stringify(response));
  }).catch((error) => {
    sendResponse(JSON.stringify({ code: 500, error: String(error?.message || error) }));
  });
  return true;
});

async function handleMessage(rawMessage, reportProgress = () => {}) {
  const message = JSON.parse(rawMessage || "{}");
  if (message.action === "connect") {
    return { code: 200, version: "0.6.0", scope: "ships-vehicles-and-optional-ccus", inventory_schema_version: 1 };
  }
  if (message.action === "importInventory") return await importHangar(reportProgress, false, true);
  if (message.action === "importCCUs") return await importHangar(reportProgress, true);
  if (message.action === "importHangar") {
    return await importHangar(reportProgress);
  }
  return { code: 400, error: "Unknown action." };
}

// Only explicit upgrade pledge titles; never infer upgrades from navigation links.
function extractCCUs(pageHTML) {
  const upgrades=[];
  const titles=/class=["'][^"']*\bjs-pledge-name\b[^"']*["'][^>]*>([\s\S]{0,500}?)<\/[^>]+>/gi;
  for(const match of pageHTML.matchAll(titles)) {
    const title=htmlText(match[1]);
    const parsed=title.match(/^(?:Ship\s+)?Upgrade\s*[-:–]\s*(.+?)\s+to\s+(.+?)(?:\s*[-–]\s*Warbond.*)?$/i);
    if(!parsed)continue;
    const source=cleanShipName(parsed[1]),target=cleanShipName(parsed[2].replace(/\s*[-–]?\s*Standard Edition\s*$/i,""));
    if(source && target && source!==target)upgrades.push({source,target});
  }
  return upgrades;
}
