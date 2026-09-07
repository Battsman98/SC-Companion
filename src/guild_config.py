from __future__ import annotations

from typing import Final


BOT_MODULES: Final[dict[str, dict[str, object]]] = {
    "ship_search": {
        "label": "Ship Search",
        "description": "Ship and vehicle lookup, filters, and autocomplete.",
        "commands": ("ship",),
    },
    "mining_tools": {
        "label": "Mining Tools",
        "description": "Mining lookup, community locations, and industry planning.",
        "commands": ("mining", "miningadd", "industry split", "industry refinery", "industry brief"),
    },
    "blueprints": {
        "label": "Blueprints",
        "description": "Blueprint search and saved blueprint lookup.",
        "commands": ("blueprint", "myblueprints"),
    },
    "missions_wikelo": {
        "label": "Missions & Wikelo",
        "description": "Mission search, blueprint rewards, and Wikelo contracts.",
        "commands": ("mission", "wikelo"),
    },
    "item_locator": {
        "label": "Item Locator",
        "description": "Buyable item and loot searches and community reports.",
        "commands": ("item search", "loot search", "loot found", "loot report"),
    },
    "inventory_search": {
        "label": "Inventory Search",
        "description": "Search inventory saved through SC Companion.",
        "commands": ("inventory search",),
    },
    "trade_tools": {
        "label": "Trade Tools",
        "description": "Commodity lookup, routes, listings, and stores.",
        "commands": ("commodity", "trade routing", "trade listing", "trade store", "trade store-refresh"),
    },
}

COMMAND_MODULE: Final[dict[str, str]] = {
    command: module_key
    for module_key, module in BOT_MODULES.items()
    for command in module["commands"]
}


def default_module_settings(enabled: bool = True) -> dict[str, dict[str, object]]:
    return {
        key: {"enabled": enabled, "channel_id": None}
        for key in BOT_MODULES
    }


def normalize_module_settings(value: object, *, enabled_default: bool = False) -> dict[str, dict[str, object]]:
    supplied = value if isinstance(value, dict) else {}
    normalized = default_module_settings(enabled_default)
    for key in BOT_MODULES:
        item = supplied.get(key)
        if not isinstance(item, dict):
            continue
        channel_id = item.get("channel_id")
        normalized[key] = {
            "enabled": bool(item.get("enabled")),
            "channel_id": int(channel_id) if channel_id else None,
        }
    return normalized


def module_for_command(command_name: str) -> str | None:
    return COMMAND_MODULE.get(command_name.strip().casefold())
