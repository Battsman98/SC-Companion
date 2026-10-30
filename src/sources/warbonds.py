from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

import aiohttp

from src.cache import SQLiteCache


class WarbondTrackerSource:
    """Build active warbond CCU summaries without relying on Discord messages."""

    STORE_URL = "https://robertsspaceindustries.com/en/pledge"
    UPGRADE_URL = "https://robertsspaceindustries.com/pledge-store/api/upgrade/graphql"
    CACHE_KEY = "warbonds:active:rsi:v7"
    CATALOG_QUERY = """
        query WarbondCatalog {
          app { pricing { currencyCode exponent exchangeRate taxRate isTaxInclusive } }
          ships {
            id name msrp link flyableStatus
            medias { productThumbMediumAndSmall }
          }
        }
    """

    def __init__(self, cache: SQLiteCache, timeout_seconds: int = 15) -> None:
        self._cache = cache
        self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout_seconds))
        self._refresh_lock = asyncio.Lock()
        self._last_good: dict[str, Any] | None = None

    async def active(self, force_refresh: bool = False) -> dict[str, Any]:
        cached = await self._cache.get(self.CACHE_KEY)
        if isinstance(cached, dict):
            self._last_good = cached
            if not force_refresh:
                return cached

        async with self._refresh_lock:
            if not force_refresh:
                cached = await self._cache.get(self.CACHE_KEY)
                if isinstance(cached, dict):
                    self._last_good = cached
                    return cached
            try:
                result = await self._refresh()
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
                if self._last_good is not None:
                    return {**self._last_good, "stale": True}
                raise
            self._last_good = result
            await self._cache.set(self.CACHE_KEY, result, 900)
            return result

    async def _refresh(self) -> dict[str, Any]:
        data = await self._graphql(self.CATALOG_QUERY)
        ships = data.get("ships")
        pricing = (data.get("app") or {}).get("pricing") or {}
        if not isinstance(ships, list) or not ships:
            raise ValueError("RSI returned an invalid upgrade catalog.")
        # MSRP and SKU prices are native minor units. Do not silently label a
        # localized/tax-inclusive response as USD.
        if (pricing.get("currencyCode") != "USD"
                or self._number(pricing.get("exchangeRate")) != 1
                or self._number(pricing.get("taxRate")) != 0
                or pricing.get("isTaxInclusive") is not False
                or pricing.get("exponent") != 2):
            raise ValueError("RSI did not return tax-exclusive USD upgrade prices.")
        if any(not isinstance(ship, dict) or not isinstance(ship.get("id"), int)
               or not ship.get("name") for ship in ships):
            raise ValueError("RSI returned malformed upgrade ships.")

        # The catalog's embedded SKUs include expired promotions. Only ship()
        # applies current storefront availability. Query every discovered ship,
        # with small batches to respect RSI's GraphQL complexity limits.
        semaphore = asyncio.Semaphore(4)

        async def details(batch: list[dict]) -> dict:
            fields = " ".join(
                f"s{ship['id']}: ship(id: {ship['id']}) {{ skus {{ "
                "id title price available unlimitedStock availableStock } }"
                for ship in batch
            )
            async with semaphore:
                result = await self._graphql("query CurrentUpgradeOffers { " + fields + " }")
            for ship in batch:
                detail = result.get(f"s{ship['id']}")
                if not isinstance(detail, dict) or not isinstance(detail.get("skus"), list):
                    raise ValueError("RSI returned incomplete upgrade availability.")
            return result

        batches = await asyncio.gather(*(details(ships[i:i + 5]) for i in range(0, len(ships), 5)))
        current = {key: value for batch in batches for key, value in batch.items()}
        catalog = []
        offers = []
        for ship in ships:
            skus = current[f"s{ship['id']}"]["skus"]
            available = [sku for sku in skus if isinstance(sku, dict) and sku.get("available") is True]
            standard = self._number(ship.get("msrp")) / 100
            catalog.append({
                "name": str(ship["name"]).strip(),
                "price": standard,
                "status": "flight ready" if ship.get("flyableStatus") == "Flyable" else "concept",
                "on_sale": any(sku.get("unlimitedStock") is True
                               and "warbond" not in str(sku.get("title", "")).lower()
                               for sku in available),
            })
            for sku in available:
                warbond = self._number(sku.get("price")) / 100
                if "warbond" not in str(sku.get("title", "")).lower() or not 0 < warbond < standard:
                    continue
                offers.append({
                    "name": str(ship["name"]).strip(),
                    "title": f"{str(ship['name']).strip()} - {sku['title']}",
                    "sku_id": sku["id"],
                    "warbond_price": warbond,
                    "standard_price": standard,
                    "saving": round(standard - warbond, 2),
                    "currency": "USD",
                    "image_url": self._rsi_url((ship.get("medias") or {}).get("productThumbMediumAndSmall")),
                    "store_url": self._rsi_url(ship.get("link")) or self.STORE_URL,
                    "updated_at": None,
                })
        catalog = [ship for ship in catalog if ship["price"] > 0]
        for offer in offers:
            offer["cheapest_source"] = self._best_source(catalog, offer["warbond_price"])
            offer["flight_ready_source"] = self._best_source(catalog, offer["warbond_price"], flight_ready=True)
            offer["unlimited_source"] = self._best_source(catalog, offer["warbond_price"], on_sale=True)
        offers.sort(key=lambda offer: (offer["warbond_price"], offer["name"], offer["sku_id"]))
        return {
            "offers": offers,
            "catalog": catalog,
            "updated_at": None,
            "checked_at": datetime.now(tz=timezone.utc).isoformat(),
            "source": "RSI pledge store live ship-upgrade availability",
        }

    async def _graphql(self, query: str) -> dict:
        async with self._session.post(
            self.UPGRADE_URL,
            json={"query": query},
            headers={"Accept": "application/json", "Referer": self.STORE_URL},
        ) as response:
            response.raise_for_status()
            payload = await response.json()
        if not isinstance(payload, dict) or payload.get("errors") or not isinstance(payload.get("data"), dict):
            raise ValueError("RSI upgrade availability could not be verified.")
        return payload["data"]

    @staticmethod
    def _rsi_url(value: Any) -> str | None:
        if not isinstance(value, str) or not value:
            return None
        if value.startswith("/") and not value.startswith("//"):
            return "https://robertsspaceindustries.com" + value
        if value.startswith("https://"):
            return value
        return None

    @classmethod
    def _best_source(
        cls,
        catalog: list[dict],
        warbond_price: float,
        *,
        flight_ready: bool = False,
        on_sale: bool = False,
    ) -> dict[str, Any] | None:
        candidates = []
        for ship in catalog:
            if ship["price"] >= warbond_price:
                continue
            if flight_ready and "flight" not in str(ship.get("status") or "").lower():
                continue
            if on_sale and not ship.get("on_sale"):
                continue
            candidates.append(ship)
        if not candidates:
            return None
        source = max(candidates, key=lambda ship: ship["price"])
        return {"name": source["name"], "price": warbond_price - source["price"]}

    @staticmethod
    def _number(value: Any) -> float:
        try:
            return float(value or 0)
        except (TypeError, ValueError):
            return 0.0

    async def close(self) -> None:
        await self._session.close()
