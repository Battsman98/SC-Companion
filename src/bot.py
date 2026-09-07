import logging
import asyncio
import io
import os
import re
import time
from contextlib import suppress
from datetime import timedelta
from pathlib import Path

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

from src.cache import SQLiteCache
from src.config import Settings
from src.guild_config import BOT_MODULES, module_for_command, normalize_module_settings
from src.security import SlidingWindowLimiter, install_secret_redaction
from src.sources.base import (
    BlueprintIngredient,
    BlueprintMission,
    BlueprintResult,
    CommodityMarket,
    CommodityResult,
    ItemLocatorResult,
    ItemPurchaseLocation,
    LootItemResult,
    MiningLocationResult,
    MiningSystemLocations,
    MissionResult,
    WikeloMissionResult,
    ShipResult,
    TradeRouteLeg,
    TradeRouteResult,
    TradeItemResult,
)
from src.sources.registry import SourceRegistry, build_default_registry
from src.timers import (
    ExecHangarStatus,
    calculate_countdown_end_unix,
    calculate_cycle_start_from_phase,
    calculate_exec_hangar_status,
    fetch_exec_cycle_start_unix,
)
from src.trade_stores import ParsedStoreInventory, StoreInventoryItem, google_sheet_csv_url, parse_store_inventory_csv, parse_store_inventory_xlsx


EXEC_OVERRIDE_CACHE_KEY = "exec:cycle-start-override"
CZ_TIMERS_CACHE_KEY = "cz:dashboard:timers"


def exec_override_cache_key(guild_id: int | None) -> str:
    return f"{EXEC_OVERRIDE_CACHE_KEY}:guild:{guild_id}" if guild_id else EXEC_OVERRIDE_CACHE_KEY


def cz_timers_cache_key(guild_id: int | None) -> str:
    return f"{CZ_TIMERS_CACHE_KEY}:guild:{guild_id}" if guild_id else CZ_TIMERS_CACHE_KEY


def timer_scope(bot: "GameAssistBot", guild_id: int | None) -> int | None:
    return None if guild_id == bot.settings.discord_guild_id else guild_id
BLUEPRINT_PAGE_SIZE = 25
BLUEPRINT_MISSION_LINES_PER_PAGE = 25
MINING_LOCATION_LINES_PER_PAGE = 25
MINING_COMMUNITY_LOCATIONS_CACHE_KEY = "mining:community-locations:v1"
CZ_TIMER_DEFINITIONS = {
    "blue_keycard": ("Blue Keycards", 15 * 60),
    "compboard": ("Compboards / Tablets", 30 * 60),
    "red_keycard": ("Red Keycards", 30 * 60),
    "timer_door": ("Timer Doors", 20 * 60),
}
INVENTORY_CHANNEL_ID = 1533075934603772004
LOOT_CHANNEL_ID = 1533075933441822830
FEEDBACK_TEMPLATE_CACHE_PREFIX = "discord:feedback-template-thread"
VISITOR_ROLE_NAME = "Visitor"
BOT_MANAGER_ROLE_NAME = "Bot Manager"
VISITOR_CATEGORY_NAME = "SC Companion Hub"
LEGACY_VISITOR_CATEGORY_NAMES = {"Visitor Bot Hub", "Discord Bot Hub"}
VISITOR_ARCHIVE_CATEGORY_NAME = "Bot Hub Archive"
VISITOR_ARCHIVE_CHANNEL_NAMES = {
    "bot-commands",
    "industry-operations",
    "blueprints-and-missions",
    "executive-hangar-status",
    "contested-zone-timers",
}
VISITOR_REMOVED_CHANNEL_NAMES = {"bot-start-here", "bot-status"}
AUDIT_LOG_CATEGORY_ID = 1516295744603164732
AUDIT_LOG_CATEGORY_NAME = "audit log"
LOOT_REVIEW_CHANNEL_NAME = "loot-report-reviews"
WEBSITE_CHANGELOG_CHANNEL_NAME = "website-changelog"
DISCORD_CHANGELOG_CHANNEL_NAME = "discord-changelog"
CHANGELOG_GITHUB_REPOSITORY = "Battsman98/SC-discord-Bot-Website"
WEBSITE_HEALTH_URL = os.getenv(
    "WEBSITE_HEALTH_URL",
    "https://star-citizen-game-assist.onrender.com/api/health",
).strip()
VISITOR_CHANNEL_SPECS = {
    "member-applications": "text",
    "ship-search": "text",
    "trade-tools": "text",
    "mining-tools": "text",
    "blueprints": "text",
    "missions-wikelo": "text",
    "item-locator": "text",
    "inventory-search": "text",
    "timers": "text",
    "general-chat": "text",
    "visitor-lounge": "voice",
}
VISITOR_CHANNEL_TOPICS = {
    "member-applications": "Apply to become a full community member.",
    "bot-commands": "Permanent command directory for the Star Citizen Companion bot.",
    "ship-search": "Search Star Citizen ships and vehicles with /ship.",
    "trade-tools": "Commodity prices and trade-route planning commands.",
    "mining-tools": "Mining locations, community reports, crew splits, refinery orders, and operation briefs.",
    "blueprints": "Blueprint search, ownership, ingredients, and quality calculations.",
    "missions-wikelo": "Mission search, blueprint rewards, and Wikelo contracts.",
    "item-locator": "Find in-game items and current purchase locations.",
    "inventory-search": "Search your linked Star Citizen inventory.",
    "timers": "Executive Hangar status and persistent Contested Zone timers.",
    "general-chat": "General conversation for Discord Bot Hub visitors.",
}
FEEDBACK_FORUM_TOPIC = "Submit website feedback, bug reports, screenshots, and reproducible examples."
FEEDBACK_FORUM_TAGS = (
    ("completed", True),
    ("in-progress", True),
    ("bug", False),
    ("request", False),
)
FEEDBACK_FORUM_DEFAULT_REACTION = "👍"
TRADING_FORUM_TOPIC = "Trade in-game items. Select WTS, WTB, or WTT; use the item name as the title and include your price in aUEC."
TRADING_FORUM_TAGS = ("WTS", "WTB", "WTT")
TRADING_GUIDE_TAG = "GUIDE"
TRADING_STORE_TAG = "STORE"
TRADE_STORE_SYNC_INTERVAL_SECONDS = 24 * 60 * 60
HUB_PROTECTED_ROLE_NAMES = {VISITOR_ROLE_NAME, BOT_MANAGER_ROLE_NAME}
HUB_RECOVERY_COOLDOWN_SECONDS = 10
HUB_PERMANENT_MESSAGE_PREFIXES = (
    "discord:visitor-welcome",
    "discord:membership-application-panel",
    "discord:visitor-example",
    "discord:commands-reference-message",
    "discord:exec-status-message",
    "discord:cz-timers-message",
)
MEMBER_ROLE_ID = 1409117152795168799
MEMBER_ROLE_NAME = "Members"
ANNIVERSARY_ROLE_NAME = "1 Year Member"
ANNIVERSARY_CHANNEL_NAME = "welcome"
ANNIVERSARY_AGE = timedelta(days=365)
ANNIVERSARY_CHECK_INTERVAL_SECONDS = 24 * 60 * 60
APPLICATION_REVIEW_CHANNEL_NAME = "membership-application-reviews"
APPLICATION_PENDING_CACHE_PREFIX = "discord:membership-application-pending"
VISITOR_COMMAND_CHANNELS = {
    "ship": "ship-search",
    "commodity": "trade-tools",
    "trade listing": "trade-tools",
    "trade store": "trade-tools",
    "trade store-refresh": "trade-tools",
    "trade routing": "trade-tools",
    "mining": "mining-tools",
    "miningadd": "mining-tools",
    "industry split": "mining-tools",
    "industry refinery": "mining-tools",
    "industry brief": "mining-tools",
    "blueprint": "blueprints",
    "myblueprints": "blueprints",
    "mission": "missions-wikelo",
    "wikelo": "missions-wikelo",
    "item locator": "item-locator",
    "item search": "item-locator",
    "inventory search": "inventory-search",
    "exec": "timers",
    "execset": "timers",
    "execclear": "timers",
    "cztimer": "timers",
}


def _hub_role_permissions(role_name: str) -> discord.Permissions:
    permissions = discord.Permissions.none()
    common = (
        "view_channel", "send_messages", "send_messages_in_threads", "read_message_history",
        "embed_links", "attach_files", "add_reactions", "use_application_commands",
    )
    visitor_extra = ("create_public_threads", "connect", "speak", "stream")
    for name in common + (visitor_extra if role_name == VISITOR_ROLE_NAME else ()):
        setattr(permissions, name, True)
    return permissions


def _hub_category_overwrites(
    guild: discord.Guild,
    visitor_role: discord.Role,
    bot_member: discord.Member,
) -> dict[discord.Role | discord.Member, discord.PermissionOverwrite]:
    public_access = discord.PermissionOverwrite(
        view_channel=True, send_messages=True, send_messages_in_threads=True,
        create_public_threads=True, read_message_history=True, embed_links=True,
        attach_files=True, add_reactions=True, use_application_commands=True,
        connect=True, speak=True, stream=True,
    )
    return {
        guild.default_role: public_access,
        visitor_role: public_access,
        bot_member: discord.PermissionOverwrite(
            view_channel=True, send_messages=True, send_messages_in_threads=True,
            create_public_threads=True, manage_threads=True, manage_channels=True,
            read_message_history=True, embed_links=True, attach_files=True,
        ),
    }


def _deployment_targets_for_files(files: list[str]) -> set[str]:
    normalized = {name.replace("\\", "/").lower() for name in files}
    website = any(
        name.startswith("web/") or name in {"src/web.py", "src/web_auth.py"}
        for name in normalized
    )
    discord_bot = "src/bot.py" in normalized or "src/timers.py" in normalized
    targets: set[str] = set()
    if website:
        targets.add(WEBSITE_CHANGELOG_CHANNEL_NAME)
    if discord_bot:
        targets.add(DISCORD_CHANGELOG_CHANNEL_NAME)
    return targets or {WEBSITE_CHANGELOG_CHANNEL_NAME, DISCORD_CHANGELOG_CHANNEL_NAME}


def _message_records_deployment(message: object, revision: str) -> bool:
    marker = revision[:12]
    return any(
        marker in (embed.description or "") and (embed.title or "").endswith(" deployed")
        for embed in getattr(message, "embeds", [])
    )


def build_feedback_template_embed() -> discord.Embed:
    embed = discord.Embed(
        title="Example: Guide button does not display the selected information",
        description="Use this structure so maintainers can understand and reproduce your report quickly.",
        color=discord.Color.from_rgb(54, 188, 232),
    )
    embed.add_field(name="Report type", value="Issue / Bug", inline=True)
    embed.add_field(name="Website area", value="Overview → Website Guide", inline=True)
    embed.add_field(
        name="Issue / Feedback",
        value="Selecting the Trade guide button leaves the Getting Started information visible.",
        inline=False,
    )
    embed.add_field(
        name="Expected action or result",
        value="The Getting Started information should hide and the Trade guide should appear below the buttons.",
        inline=False,
    )
    embed.add_field(
        name="Steps to reproduce",
        value="1. Open the Overview page.\n2. Scroll to Website Guide.\n3. Select Trade.\n4. Observe the information below.",
        inline=False,
    )
    embed.add_field(
        name="Improvement recommendations",
        value="Highlight the selected button and automatically show only its matching guide section.",
        inline=False,
    )
    embed.add_field(
        name="Helpful attachments",
        value="Add screenshots or a short video showing the problem. Never include passwords, tokens, or private account information.",
        inline=False,
    )
    embed.set_footer(text="This is an example. Create a new forum post for your own report.")
    return embed


def build_trading_item_embed(
    item: TradeItemResult,
    listing_type: str,
    seller_terms: str | None = None,
) -> discord.Embed:
    colors = {
        "WTS": discord.Color.from_rgb(46, 204, 113),
        "WTB": discord.Color.from_rgb(52, 152, 219),
        "WTT": discord.Color.from_rgb(241, 196, 15),
    }
    descriptions = {
        "WTS": "Want to sell",
        "WTB": "Want to buy",
        "WTT": "Want to trade",
    }
    description = " ".join((item.description or "No catalog description is currently available.").split())
    if len(description) > 500:
        description = f"{description[:497].rstrip()}..."
    embed = discord.Embed(
        title=item.name,
        url=item.wiki_url,
        description=description,
        color=colors.get(listing_type, discord.Color.blurple()),
    )
    embed.add_field(name="Listing", value=f"**{listing_type}** — {descriptions.get(listing_type, 'Trade listing')}", inline=True)
    if item.manufacturer:
        embed.add_field(name="Manufacturer", value=item.manufacturer, inline=True)
    if item.category:
        embed.add_field(name="Category", value=item.category, inline=True)
    if item.size:
        embed.add_field(name="Size", value=item.size, inline=True)
    embed.add_field(
        name="Seller's terms",
        value=(seller_terms or "See the original post above for the user's aUEC price and trade details.")[:1024],
        inline=False,
    )
    embed.add_field(
        name="Item links",
        value=f"[Star Citizen Wiki]({item.wiki_url}) • [View on UEX]({item.uex_url})",
        inline=False,
    )
    if item.image_url:
        embed.set_image(url=item.image_url)
    embed.set_footer(text="Item details: Star Citizen Wiki • Player listing: verify terms before trading")
    return embed


def _trade_seller_terms(content: str) -> str | None:
    fields = []
    for label in ("Price", "Quantity", "Details"):
        match = re.search(
            rf"\*\*{label}:\*\*\s*(.+?)(?=\n\*\*[A-Za-z][^\n]*?:\*\*|\n\n|$)",
            content or "",
            flags=re.IGNORECASE | re.DOTALL,
        )
        if match:
            value = " ".join(match.group(1).strip().split())
            if value:
                fields.append(f"**{'Notes' if label == 'Details' else label}:** {value}")
    return "\n".join(fields) or None


def build_marketplace_guide_embed(command_channel_id: int | None = None, forum_id: int | None = None) -> discord.Embed:
    command_location = f"<#{command_channel_id}>" if command_channel_id else "your server's Trade Tools channel"
    forum_location = f"<#{forum_id}>" if forum_id else "this marketplace forum"
    embed = discord.Embed(
        title="How to Use the Trading Forum",
        description=("The easiest way to create a listing is the `/trade listing` command. It provides item-name "
                     "suggestions and creates a correctly formatted post for you."),
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="Where to run the command",
        value=(f"Go to {command_location} and type `/trade listing`. The finished listing will be created "
               f"automatically here in {forum_location}."),
        inline=False,
    )
    embed.add_field(
        name="Create a listing",
        value=("1. Type `/trade listing` in Discord and select the command.\n"
               "2. Choose **WTS** (want to sell), **WTB** (want to buy), or **WTT** (want to trade).\n"
               "3. Begin typing the in-game item name and select the exact suggestion.\n"
               "4. Enter the price per item in aUEC.\n"
               "5. Enter the quantity. The default is 1.\n"
               "6. Add optional notes such as condition, availability, meetup location, or desired trade.\n"
               "7. Submit. The bot creates the post and adds the item image and description."),
        inline=False,
    )
    embed.add_field(
        name="Example",
        value="`/trade listing listing_type:WTS item:Overlord Core Polarity price:100000 quantity:1 notes:Meet at Seraphim Station`",
        inline=False,
    )
    embed.add_field(
        name="Posting manually",
        value=("You may also create a forum post yourself. Select exactly one **WTS**, **WTB**, or **WTT** tag, "
               "use the item's exact in-game name as the title, and include the price in aUEC. The command is "
               "recommended because its name suggestions prevent catalog-match errors."),
        inline=False,
    )
    embed.add_field(
        name="Trading safety",
        value=("• Confirm the item, quantity, price, and meetup location before exchanging anything.\n"
               "• Never share passwords, authentication codes, API keys, or account credentials.\n"
               "• Keep negotiations in the listing thread when practical.\n"
               "• This forum is for in-game items and aUEC; follow server rules and Star Citizen's terms."),
        inline=False,
    )
    embed.set_footer(text="Need help? Ask a moderator or Bot Manager in the server.")
    return embed


def build_marketplace_store_guide_embed(command_channel_id: int | None = None) -> discord.Embed:
    command_location = f"<#{command_channel_id}>" if command_channel_id else "your server's Trade Tools channel"
    embed = discord.Embed(
        title="Listing a player store",
        description=(f"Run `/trade store` in {command_location}. Add the store name, description, and exactly one "
                     "inventory source."),
        color=discord.Color.from_rgb(155, 89, 182),
    )
    embed.add_field(
        name="Google Sheets",
        value=("• Add a view-only Google Sheets link in `sheet_url`.\n"
               "• The bot checks the sheet for changes once per day.\n"
               "• Use `/trade store-refresh` for an immediate update."),
        inline=False,
    )
    embed.add_field(
        name="Inventory Scanner Excel download",
        value=("• On the website Inventory Scanner, export the inventory as an `.xlsx` file.\n"
               "• Enable **Prepare this Excel file for selling items** so the file includes UEX selling costs.\n"
               "• Attach that file in `inventory_file`.\n"
               "• The store imports item name, quantity, quality, notes, category, location, and costs.\n"
               "• Pricing uses Average UEX Player Seller Price first, then Average UEX Terminal Sell Price.\n"
               "• An uploaded workbook is a snapshot. Upload a new workbook when inventory changes."),
        inline=False,
    )
    embed.add_field(
        name="Optional details",
        value=("Add a default meetup location and availability. The bot creates the **STORE**-tagged forum post "
               "and attaches the uploaded workbook when one is used."),
        inline=False,
    )
    return embed


def build_trade_store_content(store: dict) -> str:
    uploaded = store.get("source_type") == "inventory_workbook"
    inventory_label = "Download the uploaded inventory workbook" if uploaded else "Open the live Google Sheet"
    lines = [
        f"# {str(store['store_name'])[:100]}",
        "",
        str(store["description"])[:1500],
        "",
        f"**Store owner:** <@{int(store['owner_id'])}>",
    ]
    if store.get("location"):
        lines.append(f"**Default meetup:** {str(store['location'])[:300]}")
    if store.get("availability"):
        lines.append(f"**Availability:** {str(store['availability'])[:300]}")
    lines.extend(("", f"[{inventory_label}]({store['sheet_url']})"))
    return "\n".join(lines)


def build_trade_store_embed(
    store: dict,
    items: list[StoreInventoryItem],
    synced_at: int,
) -> discord.Embed:
    uploaded = store.get("source_type") == "inventory_workbook"
    inventory_label = "Download inventory workbook" if uploaded else "Open live inventory"
    embed = discord.Embed(
        title=str(store["store_name"])[:256],
        url=str(store["sheet_url"]),
        description=(
            f"{str(store['description'])[:1200]}\n\n"
            f"**{len(items):,} item{'s' if len(items) != 1 else ''} available** · "
            f"[{inventory_label}]({store['sheet_url']})"
        ),
        color=discord.Color.from_rgb(155, 89, 182),
    )
    for start in range(0, min(len(items), 15), 5):
        lines = []
        for item in items[start : start + 5]:
            details = []
            if item.price:
                price = item.price if "auec" in item.price.casefold() else f"{item.price} aUEC"
                details.append(price)
            if item.quantity:
                details.append(f"Qty {item.quantity}")
            if item.quality:
                details.append(f"Quality {item.quality}")
            if item.location:
                details.append(item.location)
            line = f"• **{item.name}**"
            if details:
                line += f" — {' · '.join(details)}"
            if item.notes:
                line += f"\n  {item.notes[:180]}"
            lines.append(line)
        embed.add_field(
            name="Inventory preview" if start == 0 else "More inventory",
            value="\n".join(lines)[:1024],
            inline=False,
        )
    if len(items) > 15:
        source_name = "uploaded workbook" if uploaded else "Google Sheet"
        embed.add_field(
            name="Complete inventory",
            value=f"{len(items) - 15:,} more item(s) are available in the [{source_name}]({store['sheet_url']}).",
            inline=False,
        )
    if store.get("location"):
        embed.add_field(name="Default meetup", value=str(store["location"])[:1024], inline=True)
    if store.get("availability"):
        embed.add_field(name="Availability", value=str(store["availability"])[:1024], inline=True)
    source_name = "Uploaded Inventory Scanner workbook" if uploaded else "Google Sheets inventory"
    embed.set_footer(text=f"{source_name} · Inventory timestamp {synced_at}")
    return embed


class GameAssistCommandTree(app_commands.CommandTree):
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        bot = self.client
        if not isinstance(bot, GameAssistBot):
            return True

        if interaction.type == discord.InteractionType.autocomplete:
            return True

        if interaction.guild_id is None:
            await interaction.response.send_message("Use this command inside a Discord server.", ephemeral=True)
            return False

        allowed, retry_after = bot.command_limiter.allow(str(interaction.user.id))
        if not allowed:
            await interaction.response.send_message(
                f"You're using commands too quickly. Try again in {retry_after}s.", ephemeral=True
            )
            return False

        command_name = _interaction_command_name(interaction)
        configured = await bot.cache.guild_bot_settings(interaction.guild_id)
        is_primary_guild = interaction.guild_id == bot.settings.discord_guild_id
        if configured is None and not is_primary_guild and not command_name.startswith("admin "):
            await interaction.response.send_message(
                "This server has not configured SC Companion yet. A server manager can finish setup at sccompanion.org.",
                ephemeral=True,
            )
            return False

        allowed_channel_ids: set[int] = set()
        module_key = module_for_command(command_name)
        shared_utility = command_name == "status" or command_name.startswith("admin ")
        if configured is not None and not is_primary_guild and module_key is None and not shared_utility:
            await interaction.response.send_message(
                f"`/{command_name}` is not available in the shared bot yet.",
                ephemeral=True,
            )
            return False
        if configured is not None and module_key:
            modules = normalize_module_settings(configured.get("modules"))
            module = modules[module_key]
            if not module["enabled"]:
                await interaction.response.send_message(
                    f"`/{command_name}` is disabled for this server. A server manager can enable it at sccompanion.org.",
                    ephemeral=True,
                )
                return False
            channel_id = module["channel_id"]
            if not channel_id and configured.get("channel_setup_mode") == "automatic":
                channel_id = _automatic_module_channel_id(interaction.guild, module_key)
            if channel_id:
                allowed_channel_ids.add(int(channel_id))
            elif configured.get("channel_setup_mode") == "automatic":
                await interaction.response.send_message(
                    f"The command channel for `/{command_name}` is still being set up. "
                    "A server manager can reopen `/admin panel` to repair the setup.",
                    ephemeral=True,
                )
                return False
        elif is_primary_guild:
            allowed_channel_ids = bot.allowed_command_channel_ids(command_name)
        if allowed_channel_ids and interaction.channel_id not in allowed_channel_ids:
            allowed_channel_id = min(allowed_channel_ids)
            await interaction.response.send_message(
                f"`/{command_name}` can only be used in <#{allowed_channel_id}>.",
                ephemeral=True,
            )
            asyncio.create_task(
                bot.log_audit_event(
                    "Command Blocked",
                    {
                        "Command": f"/{command_name}",
                        "User": _audit_user(interaction.user),
                        "Used In": _audit_channel(interaction.channel_id),
                        "Allowed Channel": f"<#{allowed_channel_id}>",
                        "Options": _format_interaction_options(interaction) or "None",
                    },
                    color=discord.Color.red(),
                )
            )
            return False

        asyncio.create_task(
            bot.log_audit_event(
                "Command Used",
                {
                    "Command": f"/{command_name}",
                    "User": _audit_user(interaction.user),
                    "Channel": _audit_channel(interaction.channel_id),
                    "Options": _format_interaction_options(interaction) or "None",
                },
            )
        )
        return True

    async def on_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        original = getattr(error, "original", error)
        logging.error(
            "Application command failed: %s",
            _interaction_command_name(interaction),
            exc_info=(type(original), original, original.__traceback__),
        )
        try:
            if interaction.type == discord.InteractionType.autocomplete:
                if not interaction.response.is_done():
                    await interaction.response.autocomplete([])
                return
            message = "That command encountered an error. Please try again in a moment."
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
        except discord.HTTPException:
            logging.warning("Could not send application-command error response")


def _allowed_command_channel_id(bot: "GameAssistBot", command_name: str) -> int | None:
    allowed_channel_id = bot.settings.command_channel_ids.get(command_name)
    if allowed_channel_id is None and command_name in {"mission", "myblueprints"}:
        allowed_channel_id = bot.settings.command_channel_ids.get("blueprint")
    if allowed_channel_id is None and command_name == "item search":
        allowed_channel_id = bot.settings.command_channel_ids.get("item locator")
    if command_name == "inventory search" and bot.inventory_channel_id:
        allowed_channel_id = bot.inventory_channel_id
    if command_name.startswith("loot "):
        allowed_channel_id = LOOT_CHANNEL_ID
    return allowed_channel_id


class MembershipDetailsModal(discord.ui.Modal, title="Membership application — Questions 3–4 of 4"):
    rsi_handle = discord.ui.TextInput(
        label="What is your RSI Handle?",
        placeholder="Enter your Star Citizen RSI handle",
        min_length=1,
        max_length=32,
        required=True,
    )
    organizations = discord.ui.TextInput(
        label="What organizations are you a part of?",
        placeholder="List your Star Citizen organizations, or enter None",
        style=discord.TextStyle.paragraph,
        min_length=1,
        max_length=500,
        required=True,
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        bot = interaction.client
        if not isinstance(bot, GameAssistBot) or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("This application is unavailable.", ephemeral=True)
            return
        rsi_handle = str(self.rsi_handle).strip()
        if not rsi_handle:
            await interaction.response.send_message("Enter a valid RSI handle and try again.", ephemeral=True)
            return
        organizations = str(self.organizations).strip()
        if not organizations:
            await interaction.response.send_message("List your organizations, or enter None.", ephemeral=True)
            return
        await bot.submit_membership_application(interaction, rsi_handle, organizations)


class MembershipQuestionTwoView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=300)

    @discord.ui.button(label="Yes, I will follow the rules", style=discord.ButtonStyle.success)
    async def accept_rules(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        bot = interaction.client
        if not isinstance(bot, GameAssistBot) or not isinstance(interaction.user, discord.Member):
            return
        await interaction.response.send_modal(MembershipDetailsModal())

    @discord.ui.button(label="No", style=discord.ButtonStyle.danger)
    async def decline_rules(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.edit_message(
            embed=discord.Embed(
                title="Application not submitted",
                description="Community members must agree to follow the server rules.",
                color=discord.Color.red(),
            ),
            view=None,
        )


class MembershipQuestionOneView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=300)

    @discord.ui.button(label="Yes", style=discord.ButtonStyle.success)
    async def become_member(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        embed = discord.Embed(
            title="Membership application — Question 2 of 4",
            description="**Will you follow the rules in place?**",
            color=discord.Color.blurple(),
        )
        await interaction.response.edit_message(embed=embed, view=MembershipQuestionTwoView())

    @discord.ui.button(label="No", style=discord.ButtonStyle.secondary)
    async def stay_visitor(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.edit_message(
            embed=discord.Embed(
                title="Application closed",
                description="No problem — you can remain a visitor and apply later if you change your mind.",
                color=discord.Color.greyple(),
            ),
            view=None,
        )


class MembershipApplicationPanelView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Apply for Membership",
        style=discord.ButtonStyle.primary,
        custom_id="membership_application:start",
        emoji="📝",
    )
    async def start_application(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("Applications are only available inside the server.", ephemeral=True)
            return
        member_role = interaction.guild.get_role(MEMBER_ROLE_ID) if interaction.guild else None
        already_member = member_role is not None and member_role in interaction.user.roles
        if already_member:
            await interaction.response.send_message("You are already a community member.", ephemeral=True)
            return
        embed = discord.Embed(
            title="Membership application — Question 1 of 4",
            description="**Do you want to become a member of the community?**",
            color=discord.Color.blurple(),
        )
        await interaction.response.send_message(embed=embed, view=MembershipQuestionOneView(), ephemeral=True)


class MembershipReviewView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(label="Approve", style=discord.ButtonStyle.success, custom_id="membership_application:approve")
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        bot = interaction.client
        if isinstance(bot, GameAssistBot):
            await bot.review_membership_application(interaction, approved=True)

    @discord.ui.button(label="Deny", style=discord.ButtonStyle.danger, custom_id="membership_application:deny")
    async def deny(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        bot = interaction.client
        if isinstance(bot, GameAssistBot):
            await bot.review_membership_application(interaction, approved=False)


class GameAssistBot(commands.Bot):
    def __init__(self, settings: Settings, cache: SQLiteCache, sources: SourceRegistry) -> None:
        intents = discord.Intents.default()
        intents.message_content = False
        intents.members = True

        super().__init__(
            command_prefix=settings.command_prefix,
            intents=intents,
            help_command=None,
            tree_cls=GameAssistCommandTree,
        )
        self.settings = settings
        self.cache = cache
        self.sources = sources
        self.started_at_unix = int(discord.utils.utcnow().timestamp())
        self._commands_reference_synced = False
        self.inventory_channel_id: int = INVENTORY_CHANNEL_ID
        self.visitor_channels: dict[str, int] = {}
        self.visitor_category_id: int | None = None
        self.application_review_channel_id: int | None = None
        self.loot_review_channel_id: int | None = None
        self._membership_application_lock = asyncio.Lock()
        self._visitor_access_lock = asyncio.Lock()
        self.hub_role_ids: dict[str, int] = {}
        self.changelog_channels: dict[str, int] = {}
        self._exec_status_task: asyncio.Task | None = None
        self._cz_timers_task: asyncio.Task | None = None
        self._hub_recovery_task: asyncio.Task | None = None
        self._item_catalog_task: asyncio.Task | None = None
        self._website_deployment_task: asyncio.Task | None = None
        self._anniversary_task: asyncio.Task | None = None
        self._trade_store_sync_task: asyncio.Task | None = None
        self._guild_sync_task: asyncio.Task | None = None
        self._shared_setup_locks: dict[int, asyncio.Lock] = {}
        self._shared_recovery_tasks: dict[int, asyncio.Task] = {}
        self._hub_last_recovery_monotonic = 0.0
        self._hub_incident_count = 0
        self._hub_pending_incident: tuple[str, discord.AuditLogAction | None, int | None] | None = None
        self.command_limiter = SlidingWindowLimiter(
            settings.discord_rate_limit_per_10_seconds, 10
        )

    async def setup_hook(self) -> None:
        self.add_view(CZTimerDashboardView())
        self.add_view(MembershipApplicationPanelView())
        self.add_view(MembershipReviewView())
        self.add_view(FirstRunSetupView())
        self.tree.add_command(ship_command)
        self.tree.add_command(commodity_command)
        self.tree.add_command(mining_command)
        self.tree.add_command(industry_group)
        self.tree.add_command(miningadd_command)
        self.tree.add_command(blueprint_command)
        self.tree.add_command(my_blueprints_command)
        self.tree.add_command(mission_command)
        self.tree.add_command(wikelo_command)
        self.tree.add_command(item_group)
        self.tree.add_command(loot_group)
        self.tree.add_command(inventory_group)
        self.tree.add_command(exec_command)
        self.tree.add_command(execset_command)
        self.tree.add_command(execclear_command)
        self.tree.add_command(cztimer_command)
        self.tree.add_command(trade_group)
        self.tree.add_command(admin_group)

        # Primary-community maintenance commands are deliberately excluded
        # from the global command registration.
        primary_admin_commands = [
            command
            for name in PRIMARY_GUILD_ADMIN_COMMAND_NAMES
            if (command := admin_group.remove_command(name)) is not None
        ]

        await self.tree.sync()
        logging.info("Synced global slash commands")
        if self.settings.discord_guild_id:
            guild = discord.Object(id=self.settings.discord_guild_id)
            for command in primary_admin_commands:
                admin_group.add_command(command)
            self.tree.copy_global_to(guild=guild)
            self.tree.add_command(audit_group, guild=guild)
            await self.tree.sync(guild=guild)
            logging.info("Synced slash commands to guild %s", self.settings.discord_guild_id)

    async def on_ready(self) -> None:
        for guild in self.guilds:
            await self._run_startup_step(
                f"publish first-run setup notice in {guild.id}",
                lambda guild=guild: self.sync_first_run_notice(guild),
            )
            await self._run_startup_step(
                f"publish About panel in {guild.id}", lambda guild=guild: self.ensure_about_panel(guild)
            )
        if self._commands_reference_synced:
            return

        await self._run_startup_step("create Bot Manager role", self.ensure_bot_manager_role)
        await self._run_startup_step("provision loot report reviews", self.ensure_loot_review_channel)
        await self._run_startup_step("provision changelog channels", self.ensure_changelog_channels)
        await self._run_startup_step("record deployment changelog", self.record_deployment)
        await self._run_startup_step("provision Visitor hub", self.ensure_visitor_access)
        await self._run_startup_step("provision membership applications", self.ensure_membership_applications)
        await self._run_startup_step("refresh Bot Manager channel access", self.ensure_bot_manager_role)
        await self._run_startup_step("prepare feedback forum", self.ensure_feedback_forum)
        await self._run_startup_step("backfill shared feedback tickets", self.backfill_feedback_tickets)
        await self._run_startup_step("prepare trading forum", self.ensure_trading_forum)
        await self._run_startup_step("verify inventory channel", self.ensure_inventory_search_channel)
        await self._run_startup_step("sync command references", self.sync_commands_reference_message)
        await self._run_startup_step("sync Visitor command examples", self.sync_visitor_command_examples)
        await self._run_startup_step("sync loot command example", self.sync_loot_command_example)
        await self._run_startup_step("restore pending loot reviews", self.restore_pending_loot_reviews)
        await self._run_startup_step("sync Executive Hangar status", self.sync_exec_status_message)
        await self._run_startup_step("sync contested-zone timers", self.sync_cz_timers_message)
        await self._run_startup_step("assign one-year member roles", self.sync_anniversary_roles)
        self._commands_reference_synced = True

        if self._exec_status_task is None:
            self._exec_status_task = asyncio.create_task(self._exec_status_loop())
        if self._cz_timers_task is None:
            self._cz_timers_task = asyncio.create_task(self._cz_timers_loop())
        if self._item_catalog_task is None:
            self._item_catalog_task = asyncio.create_task(self._item_catalog_sync_loop())
        if self._website_deployment_task is None:
            self._website_deployment_task = asyncio.create_task(self._website_deployment_monitor_loop())
        if self._anniversary_task is None:
            self._anniversary_task = asyncio.create_task(self._anniversary_role_loop())
        if self._trade_store_sync_task is None:
            self._trade_store_sync_task = asyncio.create_task(self._trade_store_sync_loop())
        if self._guild_sync_task is None:
            self._guild_sync_task = asyncio.create_task(self._guild_sync_loop())

    async def on_guild_join(self, guild: discord.Guild) -> None:
        await self.cache.record_guild_installation(guild.id, guild.name, guild.member_count)
        await self.sync_first_run_notice(guild)
        await self.ensure_about_panel(guild)

    async def on_guild_remove(self, guild: discord.Guild) -> None:
        await self.cache.record_guild_installation(guild.id, guild.name, guild.member_count, active=False)
        await self.cache.purge_guild_data(guild.id)
        logging.info("Removed saved configuration and server-owned data for uninstalled guild %s", guild.id)

    async def on_thread_create(self, thread: discord.Thread) -> None:
        if thread.guild.id == self.settings.discord_guild_id or not isinstance(thread.parent, discord.ForumChannel):
            return
        if thread.parent.name != "feedback-and-issues":
            return
        await self.mirror_feedback_thread(thread)

    def primary_feedback_forum(self) -> discord.ForumChannel | None:
        guild = self.get_guild(self.settings.discord_guild_id or 0)
        if guild is None:
            return None
        candidates: list[discord.ForumChannel] = []
        tracked = guild.get_channel(self.visitor_channels.get("feedback-and-issues", 0))
        if isinstance(tracked, discord.ForumChannel):
            candidates.append(tracked)
        category = guild.get_channel(self.visitor_category_id or 0)
        if isinstance(category, discord.CategoryChannel):
            candidates.extend(
                item for item in category.channels
                if isinstance(item, discord.ForumChannel) and item.name == "feedback-and-issues"
            )
        configured = guild.get_channel(self.settings.feedback_forum_channel_id or 0)
        if isinstance(configured, discord.ForumChannel):
            candidates.append(configured)
        candidates.extend(item for item in guild.forums if item.name == "feedback-and-issues")
        return next(iter(dict.fromkeys(candidates)), None)

    async def mirror_feedback_thread(self, thread: discord.Thread) -> None:
        if await self.cache.feedback_mirror_for_origin_thread(thread.id):
            return
        try:
            starter = await thread.fetch_message(thread.id)
            central = self.primary_feedback_forum()
            if not isinstance(central, discord.ForumChannel):
                logging.error("Could not find the current primary feedback forum")
                return
            mirror_embed = discord.Embed(
                title=f"Mirrored ticket from {thread.guild.name}",
                description=(starter.content or "Discord forum ticket")[:4000],
                color=discord.Color.blurple(),
            )
            mirror_embed.add_field(name="Origin", value=f"[{thread.name}]({thread.jump_url})", inline=False)
            if starter.embeds:
                mirror_embed.add_field(name="Original report", value=(starter.embeds[0].description or starter.embeds[0].title or "Embedded report")[:1024], inline=False)
            created = await central.create_thread(name=f"[{thread.guild.name}] {thread.name}"[:100], embed=mirror_embed)
            central_thread = getattr(created, "thread", created)
            await self.cache.save_feedback_mirror(f"discord:{thread.id}", central_thread.id, thread.guild.id, thread.id)
        except (discord.Forbidden, discord.HTTPException, discord.NotFound):
            logging.exception("Could not mirror feedback thread %s", thread.id)

    async def backfill_feedback_tickets(self) -> None:
        for guild in self.guilds:
            if guild.id == self.settings.discord_guild_id:
                continue
            for forum in guild.forums:
                if forum.name != "feedback-and-issues":
                    continue
                for thread in forum.threads:
                    await self.mirror_feedback_thread(thread)

    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or message.author.id not in self.settings.bot_admin_user_ids:
            return
        if not isinstance(message.channel, discord.Thread) or message.guild is None or message.guild.id != self.settings.discord_guild_id:
            return
        mirror = await self.cache.feedback_mirror_for_central_thread(message.channel.id)
        if not mirror or not mirror.get("origin_thread_id"):
            return
        try:
            origin = await self.fetch_channel(int(mirror["origin_thread_id"]))
            if isinstance(origin, discord.Thread):
                content = message.content.strip() or "The SC Companion team posted an update."
                embed = discord.Embed(title="Official SC Companion response", description=content[:4000],
                                      color=discord.Color.green(), timestamp=message.created_at)
                if message.attachments:
                    embed.add_field(name="Attachments", value="\n".join(item.url for item in message.attachments)[:1024], inline=False)
                await origin.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        except (discord.Forbidden, discord.HTTPException, discord.NotFound):
            logging.exception("Could not sync official feedback response from thread %s", message.channel.id)

    async def _guild_sync_loop(self) -> None:
        await self.wait_until_ready()
        while not self.is_closed():
            for guild in self.guilds:
                with suppress(Exception):
                    await self.cache.record_guild_installation(guild.id, guild.name, guild.member_count)
                    await self.sync_first_run_notice(guild)
                    await self.ensure_about_panel(guild)
            await asyncio.sleep(60)

    async def ensure_about_panel(self, guild: discord.Guild) -> None:
        if guild.me is None or not guild.me.guild_permissions.manage_channels:
            logging.warning("Manage Channels is required to create the About page in guild %s", guild.id)
            return
        tracked_channel_id = await self.cache.get(f"guild:{guild.id}:about-channel")
        channel = guild.get_channel(tracked_channel_id) if isinstance(tracked_channel_id, int) else None
        if not isinstance(channel, discord.TextChannel):
            channel = discord.utils.find(lambda item: item.name == "about-the-bot", guild.text_channels)
        if channel is None:
            overwrites = {
                guild.default_role: discord.PermissionOverwrite(view_channel=True, send_messages=False),
                guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, embed_links=True),
            }
            channel = await guild.create_text_channel(
                "about-the-bot", overwrites=overwrites, reason="Create the SC Companion information page"
            )
        elif channel.name != "about-the-bot":
            await channel.edit(name="about-the-bot", reason="Recover the SC Companion information page")
        cache_key = f"guild:{guild.id}:about-panel-message"
        message_id = await self.cache.get(cache_key)
        message = None
        if message_id:
            with suppress(discord.NotFound, discord.Forbidden, discord.HTTPException):
                message = await channel.fetch_message(int(message_id))
        if message is None:
            message = await channel.send(embed=build_about_bot_embed(guild))
            await self.cache.set(cache_key, message.id, 315360000)
        else:
            await message.edit(embed=build_about_bot_embed(guild))
        await self.cache.set(f"guild:{guild.id}:about-channel", channel.id, 315360000)
        await self.ensure_guild_feedback_forum(guild)
        await self.ensure_automatic_module_channels(guild)
        configured = await self.cache.guild_bot_settings(guild.id)
        if configured is not None:
            await self.ensure_guild_marketplace(guild, normalize_module_settings(configured.get("modules")))
        await self.sync_guild_command_examples(guild)

    async def sync_first_run_notice(self, guild: discord.Guild) -> None:
        configured = await self.cache.guild_bot_settings(guild.id)
        setup_complete = configured is not None and any(
            bool(item["enabled"]) for item in normalize_module_settings(configured.get("modules")).values()
        )
        if guild.id != self.settings.discord_guild_id and not setup_complete:
            await self.ensure_first_run_notice(guild)
        else:
            await self.remove_first_run_notice(guild)

    async def ensure_first_run_notice(self, guild: discord.Guild) -> None:
        if guild.me is None:
            return
        preferred_names = ("welcome", "welcome-and-rules", "introductions", "general", "general-chat")
        preferred = [discord.utils.find(lambda item, name=name: item.name == name, guild.text_channels)
                     for name in preferred_names]
        candidates = [*preferred, guild.system_channel, *guild.text_channels]
        channel = next((candidate for candidate in candidates if isinstance(candidate, discord.TextChannel)
                        and candidate.name != "about-the-bot"
                        and candidate.permissions_for(guild.me).send_messages
                        and candidate.permissions_for(guild.me).embed_links), None)
        if channel is None:
            channel = discord.utils.find(lambda item: item.name == "about-the-bot", guild.text_channels)
        if not isinstance(channel, discord.TextChannel):
            return
        cache_key = f"guild:{guild.id}:first-run-setup"
        stored = await self.cache.get(cache_key)
        message = None
        if isinstance(stored, dict) and int(stored.get("channel_id") or 0) == channel.id:
            with suppress(discord.NotFound, discord.Forbidden, discord.HTTPException):
                message = await channel.fetch_message(int(stored.get("message_id") or 0))
        embed = build_first_run_setup_embed(can_manage_channels=guild.me.guild_permissions.manage_channels)
        if message is None:
            message = await channel.send(embed=embed, view=FirstRunSetupView())
            await self.cache.set(cache_key, {"channel_id": channel.id, "message_id": message.id}, 315360000)
        else:
            await message.edit(embed=embed, view=FirstRunSetupView())

    async def remove_first_run_notice(self, guild: discord.Guild) -> None:
        cache_key = f"guild:{guild.id}:first-run-setup"
        stored = await self.cache.get(cache_key)
        if not isinstance(stored, dict):
            return
        channel = guild.get_channel(int(stored.get("channel_id") or 0))
        if isinstance(channel, discord.TextChannel):
            with suppress(discord.NotFound, discord.Forbidden, discord.HTTPException):
                message = await channel.fetch_message(int(stored.get("message_id") or 0))
                await message.delete()
        await self.cache.delete(cache_key)

    async def ensure_automatic_module_channels(self, guild: discord.Guild) -> None:
        lock = self._shared_setup_locks.setdefault(guild.id, asyncio.Lock())
        async with lock:
            await self._ensure_automatic_module_channels(guild)

    async def _ensure_automatic_module_channels(self, guild: discord.Guild) -> None:
        configured = await self.cache.guild_bot_settings(guild.id)
        if configured is None or configured.get("channel_setup_mode") != "automatic" or guild.me is None:
            return
        if not guild.me.guild_permissions.manage_channels:
            logging.warning("Manage Channels is required for automatic setup in guild %s", guild.id)
            return
        modules = normalize_module_settings(configured.get("modules"))
        category = discord.utils.find(lambda item: item.name == "SC Companion", guild.categories)
        if category is None:
            category = await guild.create_category("SC Companion", reason="Set up SC Companion feature channels")
        changed = False
        for key, item in modules.items():
            if not item["enabled"]:
                continue
            channel_name = key.replace("_", "-")
            channel = guild.get_channel(int(item["channel_id"] or 0))
            if not isinstance(channel, discord.TextChannel):
                channel = discord.utils.find(
                    lambda candidate, name=channel_name: candidate.name == name and candidate.category_id == category.id,
                    guild.text_channels,
                )
            if channel is None:
                channel = await guild.create_text_channel(
                    channel_name, category=category, topic=f"SC Companion {BOT_MODULES[key]['label']} commands and examples.",
                    reason="Automatic SC Companion channel setup",
                )
            elif (
                channel.name != channel_name
                or channel.category_id != category.id
                or channel.topic != f"SC Companion {BOT_MODULES[key]['label']} commands and examples."
            ):
                await channel.edit(
                    name=channel_name,
                    category=category,
                    topic=f"SC Companion {BOT_MODULES[key]['label']} commands and examples.",
                    sync_permissions=True,
                    reason="Recover SC Companion automatic channel",
                )
            if item["channel_id"] != channel.id:
                item["channel_id"] = channel.id
                changed = True
            if key == "trade_tools":
                forum = guild.get_channel(int(item["resource_channel_id"] or 0))
                if not isinstance(forum, discord.ForumChannel):
                    forum = discord.utils.find(
                        lambda candidate: candidate.name == "marketplace" and candidate.category_id == category.id,
                        guild.forums,
                    )
                if forum is None:
                    forum = await guild.create_forum("marketplace", category=category,
                                                     topic="Marketplace listings for this Discord server.",
                                                     reason="Automatic SC Companion marketplace setup")
                elif forum.name != "marketplace" or forum.category_id != category.id:
                    await forum.edit(
                        name="marketplace",
                        category=category,
                        sync_permissions=True,
                        reason="Recover SC Companion marketplace forum",
                    )
                if item["resource_channel_id"] != forum.id:
                    item["resource_channel_id"] = forum.id
                    changed = True
                await self.configure_marketplace_forum(forum, channel.id)
        if changed:
            await self.cache.save_guild_bot_settings(
                guild.id, guild.name, modules, int(configured["configured_by"]), "automatic"
            )

    async def ensure_guild_feedback_forum(self, guild: discord.Guild) -> None:
        if guild.id == self.settings.discord_guild_id or guild.me is None or not guild.me.guild_permissions.manage_channels:
            return
        tracked_forum_id = await self.cache.get(f"guild:{guild.id}:feedback-forum")
        forum = guild.get_channel(tracked_forum_id) if isinstance(tracked_forum_id, int) else None
        if not isinstance(forum, discord.ForumChannel):
            forum = discord.utils.find(lambda item: item.name == "feedback-and-issues", guild.forums)
        if forum is None:
            forum = await guild.create_forum(
                "feedback-and-issues",
                topic=FEEDBACK_FORUM_TOPIC,
                default_reaction_emoji=FEEDBACK_FORUM_DEFAULT_REACTION,
                reason="Create the SC Companion feedback ticket forum",
            )
        elif forum.name != "feedback-and-issues" or forum.topic != FEEDBACK_FORUM_TOPIC:
            await forum.edit(
                name="feedback-and-issues",
                topic=FEEDBACK_FORUM_TOPIC,
                reason="Recover the SC Companion feedback forum",
            )
        await self.configure_feedback_forum(forum)
        await self.sync_feedback_template(forum)
        await self.cache.set(f"guild:{guild.id}:feedback-forum", forum.id, 315360000)

    async def configure_feedback_forum(self, forum: discord.ForumChannel) -> None:
        required_names = {name for name, _ in FEEDBACK_FORUM_TAGS}
        for attempt in range(2):
            existing = {tag.name.casefold() for tag in forum.available_tags}
            missing = required_names - existing
            reaction_name = getattr(forum.default_reaction_emoji, "name", None)
            if not missing and reaction_name == FEEDBACK_FORUM_DEFAULT_REACTION:
                return
            tags = list(forum.available_tags)
            tags.extend(
                discord.ForumTag(name=name, moderated=moderated)
                for name, moderated in FEEDBACK_FORUM_TAGS
                if name not in existing
            )
            if len(tags) > 20:
                tags = [tag for tag in tags if tag.name.casefold() in required_names] + [
                    tag for tag in tags if tag.name.casefold() not in required_names
                ][: 20 - len(required_names)]
            await forum.edit(
                available_tags=tags,
                default_reaction_emoji=FEEDBACK_FORUM_DEFAULT_REACTION,
                reason="Create the SC Companion feedback and issue tags",
            )
            fetched = await self.fetch_channel(forum.id)
            if isinstance(fetched, discord.ForumChannel):
                forum = fetched
        missing = required_names - {tag.name.casefold() for tag in forum.available_tags}
        if missing:
            raise RuntimeError(
                f"Discord did not save feedback tags in guild {forum.guild.id}: {', '.join(sorted(missing))}"
            )
        if getattr(forum.default_reaction_emoji, "name", None) != FEEDBACK_FORUM_DEFAULT_REACTION:
            raise RuntimeError(f"Discord did not save the feedback default reaction in guild {forum.guild.id}")

    async def sync_guild_command_examples(self, guild: discord.Guild) -> None:
        configured = await self.cache.guild_bot_settings(guild.id)
        if configured is None:
            return
        modules = normalize_module_settings(configured.get("modules"))
        grouped: dict[int, list[str]] = {}
        for key, settings in modules.items():
            if settings["enabled"] and settings["channel_id"]:
                grouped.setdefault(int(settings["channel_id"]), []).append(key)
        for channel_id, module_keys in grouped.items():
            channel = guild.get_channel(channel_id)
            if not isinstance(channel, discord.TextChannel) or guild.me is None:
                continue
            permissions = channel.permissions_for(guild.me)
            if not permissions.send_messages or not permissions.embed_links:
                continue
            cache_key = f"guild:{guild.id}:command-guide:{channel_id}"
            message_id = await self.cache.get(cache_key)
            message = None
            if message_id:
                with suppress(discord.NotFound, discord.Forbidden, discord.HTTPException):
                    message = await channel.fetch_message(int(message_id))
            embed = build_guild_command_guide_embed(module_keys)
            if message is None:
                message = await channel.send(embed=embed)
                await self.cache.set(cache_key, message.id, 315360000)
            else:
                await message.edit(embed=embed)
        await self.sync_guild_timer_dashboard(guild, modules)

    async def sync_guild_timer_dashboard(
        self,
        guild: discord.Guild,
        modules: dict[str, dict[str, object]] | None = None,
    ) -> None:
        if modules is None:
            configured = await self.cache.guild_bot_settings(guild.id)
            if configured is None:
                return
            modules = normalize_module_settings(configured.get("modules"))
        channel_id = timer_dashboard_channel_id(modules)
        if channel_id is None:
            return
        try:
            status_context = await self.resolve_exec_status_context(timer_scope(self, guild.id))
            await self._sync_exec_status_channel(channel_id, build_exec_status_embed(status_context))
        except Exception:
            logging.warning("Could not populate Executive Hangar timer in guild %s", guild.id)
        timers = await get_cz_dashboard_timers(self.cache, timer_scope(self, guild.id))
        await self._sync_cz_timers_channel(channel_id, build_cz_dashboard_embed(timers))

    async def _website_deployment_monitor_loop(self) -> None:
        """Record website-only Render revisions without restarting Discord."""
        while not self.is_closed():
            try:
                await self._record_current_website_deployment()
            except Exception:
                logging.exception("Could not monitor the website deployment revision")
            await asyncio.sleep(60)

    async def _record_current_website_deployment(self) -> None:
        if not WEBSITE_HEALTH_URL:
            return
        try:
            timeout = aiohttp.ClientTimeout(total=10)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(WEBSITE_HEALTH_URL) as response:
                    if response.status != 200:
                        return
                    payload = await response.json()
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
            return
        revision = str(payload.get("revision") or "").strip()
        if not re.fullmatch(r"[0-9a-fA-F]{7,40}", revision):
            return
        cache_key = "changelog:last-observed-website-revision:v1"
        if await self.cache.get(cache_key) == revision:
            return
        if not await self._deployment_already_recorded(WEBSITE_CHANGELOG_CHANNEL_NAME, revision):
            summary = await self._deployment_change_summary(revision)
            await self._send_changelog(
                WEBSITE_CHANGELOG_CHANNEL_NAME,
                "Website deployed",
                f"**Summary:** {summary}\n\nRevision: `{revision[:12]}`\nApplication: `Website`",
            )
        await self.cache.set(cache_key, revision, 315360000)

    async def _anniversary_role_loop(self) -> None:
        """Run the membership anniversary reconciliation once per day."""
        while not self.is_closed():
            await asyncio.sleep(ANNIVERSARY_CHECK_INTERVAL_SECONDS)
            try:
                await self.sync_anniversary_roles()
            except Exception:
                logging.exception("One-year member role synchronization failed")

    async def _trade_store_sync_loop(self) -> None:
        while not self.is_closed():
            try:
                now = int(discord.utils.utcnow().timestamp())
                for store in await self.cache.trade_stores():
                    if store.get("source_type") != "google_sheet":
                        continue
                    if now - int(store.get("last_synced_at") or 0) >= TRADE_STORE_SYNC_INTERVAL_SECONDS:
                        await self.sync_trade_store(store)
            except Exception:
                logging.exception("Trading store synchronization cycle failed")
            await asyncio.sleep(TRADE_STORE_SYNC_INTERVAL_SECONDS)

    async def sync_anniversary_roles(self) -> None:
        """Create and award the one-year role, announcing each new milestone."""
        guild = self.get_guild(self.settings.discord_guild_id or 0)
        if guild is None or guild.me is None:
            logging.error("Could not resolve the configured guild for anniversary roles")
            return
        if not guild.me.guild_permissions.manage_roles:
            logging.warning("Manage Roles is required to assign anniversary roles")
            return

        role = discord.utils.find(
            lambda item: item.name.casefold() == ANNIVERSARY_ROLE_NAME.casefold(),
            guild.roles,
        )
        if role is None:
            role = await guild.create_role(
                name=ANNIVERSARY_ROLE_NAME,
                permissions=discord.Permissions.none(),
                colour=discord.Colour.default(),
                hoist=False,
                mentionable=False,
                reason="Create automatic one-year membership milestone role",
            )
        if role >= guild.me.top_role:
            logging.error(
                "Cannot assign anniversary role %s because it is not below the bot's top role",
                role.id,
            )
            return

        welcome = discord.utils.find(
            lambda channel: channel.name.casefold() == ANNIVERSARY_CHANNEL_NAME.casefold(),
            guild.text_channels,
        )
        cutoff = discord.utils.utcnow() - ANNIVERSARY_AGE
        try:
            members = [member async for member in guild.fetch_members(limit=None)]
        except (discord.Forbidden, discord.HTTPException):
            logging.exception(
                "Could not fetch the complete guild member list; using the local cache"
            )
            members = list(guild.members)

        awarded = 0
        for member in members:
            if member.bot or member.joined_at is None or member.joined_at > cutoff or role in member.roles:
                continue
            try:
                await member.add_roles(role, reason="Member reached one year in the server")
                awarded += 1
            except (discord.Forbidden, discord.HTTPException):
                logging.exception("Could not award the one-year role to member %s", member.id)
                continue

            if welcome is not None:
                try:
                    await welcome.send(
                        f"{member.mention} just hit one year in the server — welcome to the **{ANNIVERSARY_ROLE_NAME}** role! 🎉",
                        allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
                    )
                except (discord.Forbidden, discord.HTTPException):
                    logging.exception(
                        "Awarded the one-year role but could not announce member %s",
                        member.id,
                    )
        logging.info(
            "Anniversary role scan complete: checked=%s awarded=%s cutoff=%s",
            len(members),
            awarded,
            cutoff.isoformat(),
        )

    async def _item_catalog_sync_loop(self) -> None:
        while not self.is_closed():
            try:
                if not await self.cache.get("loot:data:daily-sync:v1"):
                    status = await self.sources.refresh_loot_data()
                    await self.cache.set("loot:data:daily-sync:v1", True, 86400)
                    logging.info(
                        "Daily loot data status=%s items=%s marketplace_prices=%s",
                        status.get("status"),
                        status.get("item_count"),
                        status.get("marketplace_price_count"),
                    )
            except Exception:
                logging.exception("Star Citizen item catalog synchronization failed")
            await asyncio.sleep(60 * 60)

    async def _run_startup_step(self, label: str, operation) -> None:
        try:
            await operation()
        except Exception:
            logging.exception("Discord startup step failed: %s", label)

    async def on_member_join(self, member: discord.Member) -> None:
        """Give new humans only the Visitor role after membership screening."""
        if member.bot or member.guild.id != self.settings.discord_guild_id:
            return
        if member.pending:
            return
        await self._assign_new_visitor(member)

    async def on_member_update(self, before: discord.Member, after: discord.Member) -> None:
        if after.bot or after.guild.id != self.settings.discord_guild_id:
            return
        if before.pending and not after.pending:
            await self._assign_new_visitor(after)

    async def _assign_new_visitor(self, member: discord.Member) -> None:
        role = discord.utils.find(
            lambda item: item.name.casefold() == VISITOR_ROLE_NAME.casefold(),
            member.guild.roles,
        )
        if role is None:
            logging.error("Could not assign Visitor to %s: role is missing", member.id)
            return
        try:
            await member.edit(roles=[role], reason="New member Visitor onboarding")
        except (discord.Forbidden, discord.HTTPException):
            logging.exception("Could not assign Visitor-only access to member %s", member.id)

    async def ensure_changelog_channels(self) -> None:
        """Ensure private website and Discord changelogs exist under Audit Log."""
        guild = self.get_guild(self.settings.discord_guild_id or 0)
        if guild is None:
            logging.error("Could not resolve the configured guild for changelog provisioning")
            return
        me = guild.me
        if me is None or not me.guild_permissions.manage_channels:
            logging.warning("Manage Channels is required to provision changelog channels")
            return

        category = guild.get_channel(AUDIT_LOG_CATEGORY_ID)
        if not isinstance(category, discord.CategoryChannel):
            logging.error("AUDIT_LOG_CATEGORY_ID %s is not a Discord category", AUDIT_LOG_CATEGORY_ID)
            return
        if category.name != AUDIT_LOG_CATEGORY_NAME:
            await category.edit(name=AUDIT_LOG_CATEGORY_NAME, reason="Standardize audit log category name")

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            me: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
        }
        manager_role = discord.utils.find(lambda role: role.name == BOT_MANAGER_ROLE_NAME, guild.roles)
        if manager_role:
            overwrites[manager_role] = discord.PermissionOverwrite(
                view_channel=True, send_messages=False, read_message_history=True
            )
        for user_id in self.settings.bot_admin_user_ids:
            try:
                owner = guild.get_member(user_id) or await guild.fetch_member(user_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                logging.warning("Could not resolve changelog notification user %s", user_id)
                continue
            overwrites[owner] = discord.PermissionOverwrite(
                view_channel=True, send_messages=False, read_message_history=True
            )

        topics = {
            WEBSITE_CHANGELOG_CHANNEL_NAME: "Website deployment history from pushed revisions.",
            DISCORD_CHANGELOG_CHANNEL_NAME: "Discord bot deployment history from pushed revisions.",
        }
        for name, topic in topics.items():
            channel = discord.utils.find(lambda item: item.name == name, guild.text_channels)
            if channel is None:
                channel = await guild.create_text_channel(
                    name, category=category, topic=topic, overwrites=overwrites, reason="Create audit changelog"
                )
            elif (
                channel.category_id != category.id
                or channel.topic != topic
                or channel.overwrites != overwrites
            ):
                await channel.edit(
                    category=category,
                    topic=topic,
                    overwrites=overwrites,
                    reason="Refresh changelog location and access",
                )
            self.changelog_channels[name] = channel.id

    async def _send_changelog(self, channel_name: str, title: str, description: str) -> None:
        channel_id = self.changelog_channels.get(channel_name)
        channel = self.get_channel(channel_id or 0)
        if not isinstance(channel, discord.TextChannel):
            return
        embed = discord.Embed(
            title=title,
            description=_truncate_audit_value(description),
            color=discord.Color.dark_teal(),
            timestamp=discord.utils.utcnow(),
        )
        await channel.send(embed=embed, silent=True)

    async def record_deployment(self) -> None:
        revision = next((os.getenv(name, "").strip() for name in ("RENDER_GIT_COMMIT", "COMMIT_SHA", "GITHUB_SHA") if os.getenv(name, "").strip()), "")
        if not revision:
            return
        cache_key = "changelog:last-deployment-revision"
        previous_revision = await self.cache.get(cache_key)
        if previous_revision is None:
            previous_revision = await self.cache.get("changelog:last-website-revision")
        if previous_revision == revision:
            return
        if isinstance(previous_revision, str):
            await self._relocate_misclassified_deployment(previous_revision)
        await self._relocate_recent_misclassified_deployments()

        summary = await self._deployment_change_summary(revision)
        targets = await self._deployment_targets(revision)
        for target in targets:
            if await self._deployment_already_recorded(target, revision):
                continue
            application = "Website" if target == WEBSITE_CHANGELOG_CHANNEL_NAME else "Discord bot"
            await self._send_changelog(
                target,
                f"{application} deployed",
                f"**Summary:** {summary}\n\nRevision: `{revision[:12]}`\nApplication: `{application}`",
            )
        await self.cache.set(cache_key, revision, 315360000)

    async def _deployment_already_recorded(self, channel_name: str, revision: str) -> bool:
        channel = self.get_channel(self.changelog_channels.get(channel_name, 0))
        if not isinstance(channel, discord.TextChannel):
            return False
        try:
            async for message in channel.history(limit=100):
                if _message_records_deployment(message, revision):
                    return True
        except (discord.Forbidden, discord.HTTPException):
            logging.info("Could not inspect %s for an existing deployment entry", channel_name)
        return False

    async def _deployment_targets(self, revision: str) -> set[str]:
        files = await self._git_lines("diff-tree", "--no-commit-id", "--name-only", "-r", revision)
        if not files:
            files = await self._github_commit_files(revision)
        return _deployment_targets_for_files(files)

    async def _github_commit_files(self, revision: str) -> list[str]:
        """Resolve changed files when the deployed runtime has no local Git history."""
        url = f"https://api.github.com/repos/{CHANGELOG_GITHUB_REPOSITORY}/commits/{revision}"
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "SCCompanion-Changelog",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        try:
            timeout = aiohttp.ClientTimeout(total=10)
            async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
                async with session.get(url) as response:
                    if response.status != 200:
                        logging.info("GitHub commit lookup returned %s for %s", response.status, revision[:12])
                        return []
                    payload = await response.json()
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
            logging.info("GitHub commit lookup failed for deployment changelog")
            return []
        return [
            str(item.get("filename"))
            for item in payload.get("files", [])
            if isinstance(item, dict) and item.get("filename")
        ]

    async def _relocate_misclassified_deployment(self, revision: str) -> None:
        # Version the marker whenever relocation classification changes so an
        # entry previously marked under older logic is safely reconsidered.
        marker_key = f"changelog:deployment-relocated:v2:{revision}"
        if await self.cache.get(marker_key):
            return
        targets = await self._deployment_targets(revision)
        if targets != {DISCORD_CHANGELOG_CHANNEL_NAME}:
            await self.cache.set(marker_key, True, 315360000)
            return
        website_channel = self.get_channel(self.changelog_channels.get(WEBSITE_CHANGELOG_CHANNEL_NAME, 0))
        if isinstance(website_channel, discord.TextChannel):
            async for message in website_channel.history(limit=25):
                if any(
                    embed.title == "Website deployed" and revision[:12] in (embed.description or "")
                    for embed in message.embeds
                ):
                    try:
                        # PartialMessage.delete() does not accept an audit-log reason in
                        # discord.py.  Supplying one prevents the cleanup from running.
                        await message.delete()
                    except discord.HTTPException:
                        logging.info(
                            "Could not remove misclassified website changelog entry for %s",
                            revision[:12],
                        )
                    break
        if not await self._deployment_already_recorded(DISCORD_CHANGELOG_CHANNEL_NAME, revision):
            summary = await self._deployment_change_summary(revision)
            await self._send_changelog(
                DISCORD_CHANGELOG_CHANNEL_NAME,
                "Discord bot deployed",
                f"**Summary:** {summary}\n\nRevision: `{revision[:12]}`\nApplication: `Discord bot`",
            )
        await self.cache.set(marker_key, True, 315360000)

    async def _relocate_recent_misclassified_deployments(self) -> None:
        """Repair Discord-only revisions left in the website changelog."""
        website_channel = self.get_channel(self.changelog_channels.get(WEBSITE_CHANGELOG_CHANNEL_NAME, 0))
        if not isinstance(website_channel, discord.TextChannel):
            return
        try:
            async for message in website_channel.history(limit=25):
                for embed in message.embeds:
                    if embed.title != "Website deployed":
                        continue
                    match = re.search(r"Revision:\s*`([0-9a-fA-F]{7,40})`", embed.description or "")
                    if match:
                        await self._relocate_misclassified_deployment(match.group(1))
        except (discord.Forbidden, discord.HTTPException):
            logging.info("Could not inspect recent website changelog entries for relocation")

    async def _git_lines(self, *arguments: str) -> list[str]:
        try:
            process = await asyncio.create_subprocess_exec(
                "git",
                *arguments,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await asyncio.wait_for(process.communicate(), timeout=5)
            if process.returncode == 0:
                return [line.strip() for line in stdout.decode("utf-8", errors="replace").splitlines() if line.strip()]
        except (FileNotFoundError, OSError, asyncio.TimeoutError):
            logging.info("Git metadata is unavailable for deployment changelog")
        return []

    async def _deployment_change_summary(self, revision: str) -> str:
        """Read the deployed commit subject for a concise changelog summary."""
        subjects = await self._git_lines("show", "-s", "--format=%s", revision)
        if subjects:
            subject = subjects[0]
            return subject if len(subject) <= 300 else f"{subject[:297].rstrip()}..."
        return "Application code and services were updated to the latest deployed revision."

    async def on_guild_channel_create(self, channel: discord.abc.GuildChannel) -> None:
        return

    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel) -> None:
        if self._is_discord_bot_hub_channel(channel):
            self._schedule_hub_recovery(
                f"deleted channel {channel.name}", discord.AuditLogAction.channel_delete, channel.id
            )
            return
        if await self._is_shared_setup_channel(channel):
            self._schedule_shared_channel_recovery(channel.guild.id, f"deleted #{channel.name}")

    async def _is_shared_setup_channel(self, channel: discord.abc.GuildChannel) -> bool:
        guild = channel.guild
        if guild.id == self.settings.discord_guild_id:
            return False
        configured = await self.cache.guild_bot_settings(guild.id)
        if configured is None:
            return False
        tracked_ids: set[int] = set()
        for cache_key in (f"guild:{guild.id}:about-channel", f"guild:{guild.id}:feedback-forum"):
            tracked = await self.cache.get(cache_key)
            if isinstance(tracked, int):
                tracked_ids.add(tracked)
        if channel.id in tracked_ids or channel.name in {"about-the-bot", "feedback-and-issues"}:
            return True
        if configured.get("channel_setup_mode") != "automatic":
            return False
        modules = normalize_module_settings(configured.get("modules"))
        automatic_ids = automatic_cleanup_channel_ids(modules)
        automatic_names = {
            key.replace("_", "-") for key, item in modules.items() if item["enabled"]
        }
        automatic_names.update({"SC Companion", "marketplace"})
        return channel.id in automatic_ids or channel.name in automatic_names

    def _schedule_shared_channel_recovery(self, guild_id: int, reason: str) -> None:
        running = self._shared_recovery_tasks.get(guild_id)
        if running is not None and not running.done():
            return
        self._shared_recovery_tasks[guild_id] = asyncio.create_task(
            self._recover_shared_channels(guild_id, reason)
        )

    async def _recover_shared_channels(self, guild_id: int, reason: str) -> None:
        await asyncio.sleep(2)
        guild = self.get_guild(guild_id)
        if guild is None:
            return
        logging.warning("Recovering SC Companion channels in guild %s after %s", guild_id, reason)
        await self.ensure_about_panel(guild)

    def _is_discord_bot_hub_channel(self, channel: discord.abc.GuildChannel) -> bool:
        if self.settings.discord_guild_id and channel.guild.id != self.settings.discord_guild_id:
            return False
        if channel.name == APPLICATION_REVIEW_CHANNEL_NAME:
            return False
        if channel.id == self.visitor_category_id or channel.id in self.visitor_channels.values():
            return True
        if isinstance(channel, discord.CategoryChannel):
            return channel.name.casefold() == VISITOR_CATEGORY_NAME.casefold()
        return bool(
            channel.category
            and channel.category.name.casefold() == VISITOR_CATEGORY_NAME.casefold()
        )

    def _schedule_hub_recovery(
        self,
        reason: str,
        audit_action: discord.AuditLogAction | None = None,
        target_id: int | None = None,
    ) -> None:
        self._hub_incident_count += 1
        self._hub_pending_incident = (reason, audit_action, target_id)
        if self._hub_recovery_task is not None and not self._hub_recovery_task.done():
            return
        self._hub_recovery_task = asyncio.create_task(self._recover_discord_bot_hub())

    async def _recover_discord_bot_hub(self) -> None:
        # Discord emits a burst of child deletion events when a category is removed.
        # Debouncing and a cooldown prevent a deletion loop from flooding Discord.
        wait_seconds = max(
            1.0,
            self._hub_last_recovery_monotonic + HUB_RECOVERY_COOLDOWN_SECONDS - time.monotonic(),
        )
        await asyncio.sleep(wait_seconds)
        reason, audit_action, target_id = self._hub_pending_incident or ("hub integrity check", None, None)
        incident_count = self._hub_incident_count
        self._hub_pending_incident = None
        self._hub_incident_count = 0
        logging.warning("Restoring protected Discord Bot Hub content after %s", reason)
        await self._restore_hub_components()
        self._hub_last_recovery_monotonic = time.monotonic()
        actor = await self._find_hub_incident_actor(audit_action, target_id)
        fields: dict[str, object] = {
            "Scope": VISITOR_CATEGORY_NAME,
            "Trigger": reason,
            "Events grouped": incident_count,
            "Actor": actor or "Unavailable (bot needs View Audit Log)",
            "Result": "Protected hub configuration and permanent messages restored",
        }
        await self.log_audit_event("Discord Bot Hub failsafe activated", fields, color=discord.Color.red())

    async def _restore_hub_components(self) -> None:
        await self._run_startup_step("restore Discord Bot Hub channels", self.ensure_visitor_access)
        await self._run_startup_step("restore membership applications", self.ensure_membership_applications)
        await self._run_startup_step("restore feedback forum", self.ensure_feedback_forum)
        await self._run_startup_step("restore command references", self.sync_commands_reference_message)
        await self._run_startup_step("restore command examples", self.sync_visitor_command_examples)
        await self._run_startup_step("restore Executive Hangar status", self.sync_exec_status_message)
        await self._run_startup_step("restore contested-zone timers", self.sync_cz_timers_message)
        await self._run_startup_step("refresh Bot Manager access", self.ensure_bot_manager_role)

    async def _find_hub_incident_actor(
        self,
        action: discord.AuditLogAction | None,
        target_id: int | None,
    ) -> str | None:
        guild = self.get_guild(self.settings.discord_guild_id or 0)
        if guild is None or guild.me is None or not guild.me.guild_permissions.view_audit_log:
            return None
        try:
            async for entry in guild.audit_logs(limit=10, action=action):
                entry_target_id = getattr(entry.target, "id", None)
                if target_id is not None and entry_target_id not in (None, target_id):
                    continue
                return f"{entry.user} ({entry.user.id})"
        except (discord.Forbidden, discord.HTTPException):
            logging.info("Could not inspect the Discord audit log for a hub incident")
        return None

    async def on_raw_message_delete(self, payload: discord.RawMessageDeleteEvent) -> None:
        if payload.guild_id != self.settings.discord_guild_id:
            return
        if not await self._is_protected_hub_message(payload.channel_id, payload.message_id):
            return
        self._schedule_hub_recovery(
            f"deleted permanent message {payload.message_id}",
            discord.AuditLogAction.message_delete,
            None,
        )

    async def on_raw_bulk_message_delete(self, payload: discord.RawBulkMessageDeleteEvent) -> None:
        if payload.guild_id != self.settings.discord_guild_id:
            return
        for message_id in payload.message_ids:
            if await self._is_protected_hub_message(payload.channel_id, message_id):
                self._schedule_hub_recovery(
                    "bulk-deleted permanent hub messages",
                    discord.AuditLogAction.message_bulk_delete,
                    None,
                )
                return

    async def _is_protected_hub_message(self, channel_id: int, message_id: int) -> bool:
        if channel_id not in self.visitor_channels.values():
            return False
        keys = tuple(f"{prefix}:{channel_id}" for prefix in HUB_PERMANENT_MESSAGE_PREFIXES)
        for key in keys:
            stored = await self.cache.get(key)
            if stored == message_id or isinstance(stored, list) and message_id in stored:
                return True
        return False

    async def inspect_discord_bot_hub(self) -> list[str]:
        guild = self.get_guild(self.settings.discord_guild_id or 0)
        if guild is None:
            return ["Configured Discord server is unavailable"]
        issues: list[str] = []
        category = guild.get_channel(self.visitor_category_id or 0)
        if not isinstance(category, discord.CategoryChannel) or category.name != VISITOR_CATEGORY_NAME:
            issues.append("Discord Bot Hub category is missing or renamed")
            category = discord.utils.find(lambda item: item.name == VISITOR_CATEGORY_NAME, guild.categories)

        roles: dict[str, discord.Role] = {}
        for role_name in HUB_PROTECTED_ROLE_NAMES:
            role = guild.get_role(self.hub_role_ids.get(role_name, 0))
            if role is None:
                role = discord.utils.find(lambda item: item.name == role_name, guild.roles)
            if role is None:
                issues.append(f"{role_name} role is missing")
            elif (
                role.permissions != _hub_role_permissions(role_name)
                or role.colour != discord.Colour.default()
                or role.hoist
                or role.mentionable
            ):
                issues.append(f"{role_name} role settings were changed")
                roles[role_name] = role
            else:
                roles[role_name] = role

        visitor_role = roles.get(VISITOR_ROLE_NAME)
        if (
            isinstance(category, discord.CategoryChannel)
            and visitor_role is not None
            and guild.me is not None
            and category.overwrites != _hub_category_overwrites(guild, visitor_role, guild.me)
        ):
            issues.append("Discord Bot Hub category permissions were changed")

        for name, channel_type in VISITOR_CHANNEL_SPECS.items():
            channel = guild.get_channel(self.visitor_channels.get(name, 0))
            expected_type = discord.ChannelType.voice if channel_type == "voice" else discord.ChannelType.text
            if channel is None or channel.name != name or channel.type != expected_type:
                issues.append(f"#{name} is missing, renamed, or has the wrong type")
            elif category is None or channel.category_id != category.id:
                issues.append(f"#{name} is outside Discord Bot Hub")
            elif not channel.permissions_synced:
                issues.append(f"#{name} permissions are not synchronized with Discord Bot Hub")
            elif isinstance(channel, discord.TextChannel) and channel.topic != VISITOR_CHANNEL_TOPICS.get(name):
                issues.append(f"#{name} topic was changed")

        forum = guild.get_channel(self.visitor_channels.get("feedback-and-issues", 0))
        if not isinstance(forum, discord.ForumChannel) or forum.name != "feedback-and-issues":
            issues.append("feedback-and-issues forum is missing or changed")
        elif category is None or forum.category_id != category.id:
            issues.append("feedback-and-issues is outside Discord Bot Hub")
        elif not forum.permissions_synced:
            issues.append("feedback-and-issues permissions are not synchronized with Discord Bot Hub")
        elif forum.topic != FEEDBACK_FORUM_TOPIC:
            issues.append("feedback-and-issues topic was changed")

        for channel_id in set(self.visitor_channels.values()):
            channel = guild.get_channel(channel_id)
            if not isinstance(channel, discord.abc.Messageable) or not hasattr(channel, "fetch_message"):
                continue
            for prefix in HUB_PERMANENT_MESSAGE_PREFIXES:
                stored = await self.cache.get(f"{prefix}:{channel_id}")
                message_ids = stored if isinstance(stored, list) else [stored] if isinstance(stored, int) else []
                for message_id in message_ids:
                    try:
                        await channel.fetch_message(message_id)
                    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                        issues.append(f"Permanent bot message {message_id} is missing from <#{channel_id}>")
        if isinstance(forum, discord.ForumChannel):
            template_id = await self.cache.get(f"{FEEDBACK_TEMPLATE_CACHE_PREFIX}:{forum.id}")
            if isinstance(template_id, int):
                try:
                    template = self.get_channel(template_id) or await self.fetch_channel(template_id)
                    if not isinstance(template, discord.Thread):
                        issues.append("Feedback example thread is missing")
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    issues.append("Feedback example thread is missing")
        return issues

    async def on_raw_thread_delete(self, payload: discord.RawThreadDeleteEvent) -> None:
        if payload.guild_id != self.settings.discord_guild_id:
            return
        if payload.parent_id == self.settings.trading_forum_channel_id:
            store = await self.cache.trade_store(payload.thread_id)
            if store is not None:
                await self.cache.update_trade_store_sync(
                    payload.thread_id,
                    content_hash=store.get("content_hash"),
                    last_synced_at=store.get("last_synced_at"),
                    last_error="The store forum post was deleted.",
                    active=False,
                )
        forum_id = self.visitor_channels.get("feedback-and-issues") or self.settings.feedback_forum_channel_id
        if forum_id != payload.parent_id:
            return
        stored = await self.cache.get(f"{FEEDBACK_TEMPLATE_CACHE_PREFIX}:{forum_id}")
        if stored == payload.thread_id:
            self._schedule_hub_recovery(
                f"deleted feedback template {payload.thread_id}",
                discord.AuditLogAction.thread_delete,
                payload.thread_id,
            )

    async def on_thread_create(self, thread: discord.Thread) -> None:
        forum_id = await self.marketplace_forum_id(thread.guild.id)
        if thread.parent_id != forum_id:
            return
        await self.enrich_trading_post(thread)

    async def marketplace_forum_id(self, guild_id: int | None) -> int | None:
        if guild_id is None:
            return None
        configured = await self.cache.guild_bot_settings(guild_id)
        if configured is not None:
            modules = normalize_module_settings(configured.get("modules"))
            channel_id = modules["trade_tools"].get("resource_channel_id")
            if channel_id:
                return int(channel_id)
            if guild_id == self.settings.discord_guild_id:
                return self.settings.trading_forum_channel_id
            return None
        if guild_id == self.settings.discord_guild_id:
            return self.settings.trading_forum_channel_id
        return None

    async def ensure_trading_forum(self) -> None:
        channel_id = self.settings.trading_forum_channel_id
        if not channel_id:
            return
        channel = self.get_channel(channel_id)
        if channel is None:
            try:
                channel = await self.fetch_channel(channel_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                logging.exception("Could not resolve trading forum %s", channel_id)
                return
        if not isinstance(channel, discord.ForumChannel):
            logging.error("TRADING_FORUM_CHANNEL_ID %s is not a Discord forum channel", channel_id)
            return
        await self.configure_marketplace_forum(channel, self.visitor_channels.get("trade-tools"))

    async def ensure_guild_marketplace(
        self,
        guild: discord.Guild,
        modules: dict[str, dict[str, object]],
    ) -> None:
        trade = modules.get("trade_tools")
        if not trade or not trade.get("enabled") or not trade.get("resource_channel_id"):
            return
        forum = guild.get_channel(int(trade["resource_channel_id"]))
        if not isinstance(forum, discord.ForumChannel):
            with suppress(discord.NotFound, discord.Forbidden, discord.HTTPException):
                fetched = await self.fetch_channel(int(trade["resource_channel_id"]))
                forum = fetched if isinstance(fetched, discord.ForumChannel) else None
        if isinstance(forum, discord.ForumChannel):
            await self.configure_marketplace_forum(forum, int(trade["channel_id"]) if trade.get("channel_id") else None)

    async def configure_marketplace_forum(
        self, channel: discord.ForumChannel, command_channel_id: int | None = None,
    ) -> None:
        required = (*TRADING_FORUM_TAGS, TRADING_STORE_TAG, TRADING_GUIDE_TAG)
        required_names = set(required)

        def desired_tags(forum: discord.ForumChannel) -> list[discord.ForumTag]:
            existing = {tag.name.upper() for tag in forum.available_tags}
            tags = list(forum.available_tags)
            for name in required:
                if name not in existing:
                    tags.append(discord.ForumTag(name=name, moderated=name in {TRADING_STORE_TAG, TRADING_GUIDE_TAG}))
            if len(tags) > 20:
                tags = [tag for tag in tags if tag.name.upper() in required_names] + [
                    tag for tag in tags if tag.name.upper() not in required_names
                ][:15]
            return tags

        # Discord has occasionally accepted a combined forum edit while dropping
        # available_tags. Save tags on their own, fetch the channel again, and retry
        # once so automatic setup is self-healing instead of looking complete while
        # the forum's required marketplace tags are still absent.
        for attempt in range(2):
            missing = required_names - {tag.name.upper() for tag in channel.available_tags}
            if not missing:
                break
            await channel.edit(
                available_tags=desired_tags(channel),
                reason="Create the SC Companion marketplace tags",
            )
            fetched = await self.fetch_channel(channel.id)
            if isinstance(fetched, discord.ForumChannel):
                channel = fetched
        missing = required_names - {tag.name.upper() for tag in channel.available_tags}
        if missing:
            raise RuntimeError(
                f"Discord did not save marketplace tags in guild {channel.guild.id}: {', '.join(sorted(missing))}"
            )

        if channel.topic != TRADING_FORUM_TOPIC or not channel.flags.require_tag:
            edited_channel = await channel.edit(
                topic=TRADING_FORUM_TOPIC,
                require_tag=True,
                reason="Configure the in-game item trading forum",
            )
            if isinstance(edited_channel, discord.ForumChannel):
                channel = edited_channel
        guide_tag = discord.utils.find(
            lambda tag: tag.name.casefold() == TRADING_GUIDE_TAG.casefold(), channel.available_tags
        )
        cache_key = f"guild:{channel.guild.id}:marketplace-guide"
        thread_id = await self.cache.get(cache_key)
        thread = None
        if isinstance(thread_id, int):
            with suppress(discord.NotFound, discord.Forbidden, discord.HTTPException):
                candidate = self.get_channel(thread_id) or await self.fetch_channel(thread_id)
                thread = candidate if isinstance(candidate, discord.Thread) else None
        embed = build_marketplace_guide_embed(command_channel_id, channel.id)
        if thread is None:
            created = await channel.create_thread(
                name="How to Use the Trading Forum",
                embed=embed,
                applied_tags=[guide_tag] if guide_tag else [],
                reason="Create the SC Companion marketplace guide",
            )
            thread = created.thread
            await self.cache.set(cache_key, thread.id, 315360000)
        else:
            if thread.archived:
                await thread.edit(archived=False, reason="Refresh the marketplace guide")
            if thread.name != "How to Use the Trading Forum" or guide_tag not in thread.applied_tags:
                await thread.edit(
                    name="How to Use the Trading Forum",
                    applied_tags=[guide_tag] if guide_tag else thread.applied_tags,
                    reason="Refresh the marketplace guide",
                )
            starter = await thread.fetch_message(thread.id)
            await starter.edit(embed=embed)
        store_cache_key = f"{cache_key}:store-instructions"
        store_message_id = await self.cache.get(store_cache_key)
        store_message = None
        if isinstance(store_message_id, int):
            with suppress(discord.NotFound, discord.Forbidden, discord.HTTPException):
                store_message = await thread.fetch_message(store_message_id)
        store_embed = build_marketplace_store_guide_embed(command_channel_id)
        if store_message is None:
            store_message = await thread.send(embed=store_embed)
            await self.cache.set(store_cache_key, store_message.id, 315360000)
        else:
            await store_message.edit(embed=store_embed)
        with suppress(discord.Forbidden, discord.HTTPException):
            await thread.edit(pinned=True, reason="Keep the marketplace guide visible")

    async def enrich_trading_post(self, thread: discord.Thread) -> None:
        if self.user is not None and thread.owner_id == self.user.id:
            return
        if any(tag.name.casefold() in {TRADING_GUIDE_TAG.casefold(), TRADING_STORE_TAG.casefold()} for tag in thread.applied_tags):
            return
        selected = [tag.name.upper() for tag in thread.applied_tags if tag.name.upper() in TRADING_FORUM_TAGS]
        if len(selected) != 1:
            await thread.send(
                "Please select exactly one listing type: **WTS** (want to sell), **WTB** (want to buy), or **WTT** (want to trade)."
            )
            return
        item_query = re.sub(r"^\s*(?:WTS|WTB|WTT)\s*[-:|]?\s*", "", thread.name, flags=re.IGNORECASE).strip()
        try:
            item = await self.sources.lookup_trade_item(item_query)
        except Exception:
            logging.exception("Could not enrich trading post %s", thread.id)
            await thread.send("I couldn't load item details right now. Your listing is still available; please try again later.")
            return
        if item is None:
            await thread.send(
                f"I couldn't match **{item_query or thread.name}** to the current Star Citizen item catalog. "
                "Use the item's exact in-game name as the post title."
            )
            return
        seller_terms = None
        try:
            starter = await thread.fetch_message(thread.id)
            seller_terms = _trade_seller_terms(starter.content)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            logging.exception("Could not read starter message for trading post %s", thread.id)
        await thread.send(embed=build_trading_item_embed(item, selected[0], seller_terms))

    async def fetch_trade_store_inventory(self, sheet_url: str) -> ParsedStoreInventory:
        export_url = google_sheet_csv_url(sheet_url)
        timeout = aiohttp.ClientTimeout(total=max(15, self.settings.http_timeout_seconds))
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(export_url, headers={"Accept": "text/csv"}) as response:
                response.raise_for_status()
                return parse_store_inventory_csv(await response.read())

    async def sync_trade_store(self, store: dict, force: bool = False) -> tuple[bool, str]:
        thread_id = int(store["thread_id"])
        if store.get("source_type") == "inventory_workbook":
            return False, "Uploaded inventory workbooks are snapshots. Upload a new workbook to publish updated inventory."
        try:
            inventory = await self.fetch_trade_store_inventory(str(store["sheet_url"]))
            now = int(discord.utils.utcnow().timestamp())
            changed = inventory.content_hash != store.get("content_hash")
            if changed or force:
                channel = self.get_channel(thread_id) or await self.fetch_channel(thread_id)
                if not isinstance(channel, discord.Thread):
                    raise RuntimeError("The store forum post no longer exists.")
                message = await channel.fetch_message(int(store["message_id"]))
                await message.edit(
                    content=build_trade_store_content(store),
                    embed=build_trade_store_embed(store, inventory.items, now),
                    allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
                )
            await self.cache.update_trade_store_sync(
                thread_id,
                content_hash=inventory.content_hash,
                last_synced_at=now,
                last_error=None,
            )
            return True, f"Synchronized {len(inventory.items)} inventory item(s)."
        except (discord.NotFound, discord.Forbidden):
            await self.cache.update_trade_store_sync(
                thread_id,
                content_hash=store.get("content_hash"),
                last_synced_at=store.get("last_synced_at"),
                last_error="The store forum post is missing or inaccessible.",
                active=False,
            )
            return False, "The store forum post is missing or inaccessible."
        except Exception as exc:
            message = str(exc).strip() or type(exc).__name__
            await self.cache.update_trade_store_sync(
                thread_id,
                content_hash=store.get("content_hash"),
                last_synced_at=store.get("last_synced_at"),
                last_error=message[:500],
            )
            logging.warning("Store sync failed for thread %s: %s", thread_id, message)
            return False, message[:500]

    async def on_guild_channel_update(self, before: discord.abc.GuildChannel, after: discord.abc.GuildChannel) -> None:
        # Channel order is intentionally owner-controlled. Only recover settings
        # that affect the Hub's identity, placement, instructions, or access.
        protected_settings_changed = any(
            getattr(before, attribute, None) != getattr(after, attribute, None)
            for attribute in ("name", "category_id", "topic", "overwrites")
        )
        if protected_settings_changed and (
            self._is_discord_bot_hub_channel(before) or self._is_discord_bot_hub_channel(after)
        ):
            self._schedule_hub_recovery(
                f"modified channel {before.name}", discord.AuditLogAction.channel_update, after.id
            )
        elif protected_settings_changed and (
            await self._is_shared_setup_channel(before) or await self._is_shared_setup_channel(after)
        ):
            self._schedule_shared_channel_recovery(after.guild.id, f"modified #{before.name}")

    async def on_guild_role_create(self, role: discord.Role) -> None:
        return

    async def on_guild_role_delete(self, role: discord.Role) -> None:
        if role.name in HUB_PROTECTED_ROLE_NAMES or role.id in self.hub_role_ids.values():
            self._schedule_hub_recovery(
                f"deleted protected role {role.name}", discord.AuditLogAction.role_delete, role.id
            )

    async def on_guild_role_update(self, before: discord.Role, after: discord.Role) -> None:
        changes = []
        if before.name != after.name:
            changes.append(f"Name: `{before.name}` → `{after.name}`")
        if before.permissions != after.permissions:
            changes.append("Permissions changed")
        if before.color != after.color:
            changes.append(f"Color: `{before.color}` → `{after.color}`")
        if (
            before.name in HUB_PROTECTED_ROLE_NAMES
            or after.name in HUB_PROTECTED_ROLE_NAMES
            or after.id in self.hub_role_ids.values()
        ):
            self._schedule_hub_recovery(
                f"modified protected role {before.name}", discord.AuditLogAction.role_update, after.id
            )

    def allowed_command_channel_ids(self, command_name: str) -> set[int]:
        visitor_name = VISITOR_COMMAND_CHANNELS.get(command_name)
        visitor_id = self.visitor_channels.get(visitor_name or "")
        if visitor_id:
            return {visitor_id}
        configured = _allowed_command_channel_id(self, command_name)
        return {configured} if configured else set()

    async def ensure_visitor_access(self) -> None:
        async with self._visitor_access_lock:
            await self._ensure_visitor_access()

    async def _ensure_visitor_access(self) -> None:
        guild = self.get_guild(self.settings.discord_guild_id or 0)
        if guild is None:
            logging.error("Could not resolve the configured guild for Visitor onboarding")
            return
        me = guild.me
        if me is None or not me.guild_permissions.manage_roles or not me.guild_permissions.manage_channels:
            logging.error("Bot requires Manage Roles and Manage Channels to provision Visitor access")
            return

        role = guild.get_role(self.hub_role_ids.get(VISITOR_ROLE_NAME, 0))
        if role is None:
            role = discord.utils.find(lambda item: item.name.casefold() == VISITOR_ROLE_NAME.casefold(), guild.roles)
        expected_role_permissions = _hub_role_permissions(VISITOR_ROLE_NAME)
        if role is None:
            role = await guild.create_role(
                name=VISITOR_ROLE_NAME,
                permissions=expected_role_permissions,
                reason="Website Visitor onboarding",
            )
        elif (
            role.name != VISITOR_ROLE_NAME
            or role.permissions != expected_role_permissions
            or role.colour != discord.Colour.default()
            or role.hoist
            or role.mentionable
        ):
            await role.edit(
                name=VISITOR_ROLE_NAME,
                permissions=expected_role_permissions,
                colour=discord.Colour.default(),
                hoist=False,
                mentionable=False,
                reason="Restore Discord Bot Hub role settings",
            )
        self.hub_role_ids[VISITOR_ROLE_NAME] = role.id

        category = guild.get_channel(self.visitor_category_id or 0)
        if not isinstance(category, discord.CategoryChannel):
            category = discord.utils.find(
                lambda item: item.name.casefold() == VISITOR_CATEGORY_NAME.casefold()
                or item.name.casefold() in {name.casefold() for name in LEGACY_VISITOR_CATEGORY_NAMES},
                guild.categories,
            )
        category_overwrites = _hub_category_overwrites(guild, role, me)
        if category is None:
            category = await guild.create_category(
                VISITOR_CATEGORY_NAME,
                overwrites=category_overwrites,
                reason="Create isolated Visitor bot access",
            )
        elif category.name != VISITOR_CATEGORY_NAME or category.overwrites != category_overwrites:
            await category.edit(
                name=VISITOR_CATEGORY_NAME,
                overwrites=category_overwrites,
                reason="Restore Discord Bot Hub category settings",
            )
        self.visitor_category_id = category.id

        await self._remove_legacy_visitor_categories(guild, category)
        await self._archive_replaced_visitor_channels(guild, category, role, me)

        about = discord.utils.find(lambda item: item.name == "about-the-bot", guild.text_channels)
        if isinstance(about, discord.TextChannel):
            if about.category_id != category.id or not about.permissions_synced:
                await about.edit(
                    category=category,
                    sync_permissions=True,
                    reason="Move the bot information page into Discord Bot Hub",
                )
            self.visitor_channels["about-the-bot"] = about.id

        for name, channel_type in VISITOR_CHANNEL_SPECS.items():
            existing = guild.get_channel(self.visitor_channels.get(name, 0))
            expected_type = discord.ChannelType.voice if channel_type == "voice" else discord.ChannelType.text
            if existing is None or existing.type != expected_type:
                existing = discord.utils.find(
                    lambda item: item.name == name and item.type == expected_type, category.channels
                )
            if existing is None:
                if channel_type == "voice":
                    existing = await guild.create_voice_channel(name, category=category, reason="Visitor access channel")
                else:
                    existing = await guild.create_text_channel(
                        name,
                        category=category,
                        topic=VISITOR_CHANNEL_TOPICS.get(name),
                        reason="Visitor bot channel",
                    )
            elif (
                existing.name != name
                or existing.category_id != category.id
                or not existing.permissions_synced
                or isinstance(existing, discord.TextChannel)
                and existing.topic != VISITOR_CHANNEL_TOPICS.get(name)
            ):
                changes = {
                    "name": name,
                    "category": category,
                    "sync_permissions": True,
                    "reason": "Restore Discord Bot Hub channel settings",
                }
                if isinstance(existing, discord.TextChannel):
                    changes["topic"] = VISITOR_CHANNEL_TOPICS.get(name)
                await existing.edit(**changes)
            self.visitor_channels[name] = existing.id

        feedback = discord.utils.find(lambda item: item.name == "feedback-and-issues", category.channels)
        if feedback is None:
            feedback = self.get_channel(self.settings.feedback_forum_channel_id or 0)
        if isinstance(feedback, discord.ForumChannel) and (
            feedback.name != "feedback-and-issues"
            or feedback.category_id != category.id
            or feedback.topic != FEEDBACK_FORUM_TOPIC
            or not feedback.permissions_synced
        ):
            await feedback.edit(
                name="feedback-and-issues",
                category=category,
                topic=FEEDBACK_FORUM_TOPIC,
                sync_permissions=True,
                reason="Move feedback forum into Visitor hub",
            )
            self.visitor_channels["feedback-and-issues"] = feedback.id

        await self.remove_legacy_star_citizen_bot_channels(guild)

        for removed_name in VISITOR_REMOVED_CHANNEL_NAMES:
            for removed_channel in list(guild.text_channels):
                if removed_channel.name == removed_name:
                    await removed_channel.delete(
                        reason="Consolidate Discord Bot Hub information into about-the-bot"
                    )
            self.visitor_channels.pop(removed_name, None)

        allowed_ids = {category.id, *self.visitor_channels.values()}
        if isinstance(feedback, discord.ForumChannel):
            allowed_ids.add(feedback.id)
        for channel in guild.channels:
            if channel.id in allowed_ids:
                continue
            current_overwrite = channel.overwrites_for(role)
            overwrite = discord.PermissionOverwrite.from_pair(*current_overwrite.pair())
            if overwrite.view_channel is not False:
                overwrite.view_channel = False
                await channel.set_permissions(role, overwrite=overwrite, reason="Isolate Visitor access")

        welcome = self.get_channel(self.visitor_channels.get("about-the-bot", 0))
        if isinstance(welcome, discord.TextChannel):
            await self.sync_visitor_welcome(welcome, role)

    async def _archive_replaced_visitor_channels(
        self,
        guild: discord.Guild,
        active_category: discord.CategoryChannel,
        visitor_role: discord.Role,
        me: discord.Member,
    ) -> None:
        replaced_by_id = {
            channel.id: channel for channel in active_category.channels
            if channel.name in VISITOR_ARCHIVE_CHANNEL_NAMES
        }
        for name in VISITOR_CHANNEL_SPECS:
            matches = sorted(
                (channel for channel in active_category.channels if channel.name == name),
                key=lambda channel: channel.id,
            )
            for duplicate in matches[1:]:
                replaced_by_id[duplicate.id] = duplicate
        replaced = list(replaced_by_id.values())
        if not replaced:
            return
        archive = discord.utils.find(
            lambda item: item.name.casefold() == VISITOR_ARCHIVE_CATEGORY_NAME.casefold(),
            guild.categories,
        )
        manager = guild.get_role(self.hub_role_ids.get(BOT_MANAGER_ROLE_NAME, 0))
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            visitor_role: discord.PermissionOverwrite(view_channel=False),
            me: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                manage_channels=True,
            ),
        }
        if manager is not None:
            overwrites[manager] = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=False,
                read_message_history=True,
            )
        if archive is None:
            archive = await guild.create_category(
                VISITOR_ARCHIVE_CATEGORY_NAME,
                overwrites=overwrites,
                reason="Preserve replaced Discord Bot Hub channels",
            )
        elif archive.overwrites != overwrites:
            await archive.edit(
                overwrites=overwrites,
                reason="Protect preserved Discord Bot Hub history",
            )
        for channel in replaced:
            await channel.edit(
                category=archive,
                sync_permissions=True,
                reason="Archive replaced Discord Bot Hub channel",
            )

    async def remove_legacy_star_citizen_bot_channels(
        self,
        guild: discord.Guild,
    ) -> None:
        star_citizen = discord.utils.find(
            lambda item: item.name.casefold() == "star citizen",
            guild.categories,
        )
        if star_citizen is None:
            return
        managed_ids = {
            INVENTORY_CHANNEL_ID,
            *self.settings.command_channel_ids.values(),
        }
        for channel_id in (
            self.settings.commands_channel_id,
            self.settings.exec_status_channel_id,
            self.settings.cz_timers_channel_id,
        ):
            if channel_id:
                managed_ids.add(channel_id)
        visitor_ids = set(self.visitor_channels.values())
        for channel in list(star_citizen.text_channels):
            if channel.id not in managed_ids or channel.id in visitor_ids:
                continue
            await channel.delete(reason="Consolidate bot commands into Discord Bot Hub")
            logging.info("Deleted legacy Star Citizen bot channel %s (%s)", channel.name, channel.id)

    async def _remove_legacy_visitor_categories(
        self,
        guild: discord.Guild,
        destination: discord.CategoryChannel,
    ) -> None:
        legacy_categories = [
            category
            for category in guild.categories
            if category.id != destination.id
            and category.name.casefold() in {name.casefold() for name in LEGACY_VISITOR_CATEGORY_NAMES}
        ]
        for legacy in legacy_categories:
            for channel in list(legacy.channels):
                destination_match = discord.utils.find(
                    lambda item: item.name == channel.name and item.type == channel.type,
                    destination.channels,
                )
                if channel.name in VISITOR_CHANNEL_SPECS and destination_match is not None:
                    await channel.delete(reason="Remove duplicate channel from legacy Visitor Bot Hub")
                else:
                    await channel.edit(
                        category=destination,
                        sync_permissions=True,
                        reason="Move channel into renamed Discord Bot Hub",
                    )
            await legacy.delete(reason="Remove replaced Visitor Bot Hub category")

    async def sync_visitor_welcome(self, channel: discord.TextChannel, role: discord.Role) -> None:
        cache_key = f"discord:visitor-welcome:{channel.id}"
        message_id = await self.cache.get(cache_key)
        embed = discord.Embed(
            title="Welcome to the Star Citizen Companion Bot Hub",
            description=(
                f"Members joining through the website receive the {role.mention} role. "
                "Use the topic channels for public bot commands, general-chat for conversation, "
                "and feedback-and-issues for website reports. Administrative tools are not available here."
            ),
            color=discord.Color.from_rgb(54, 188, 232),
        )
        embed.add_field(name="Getting started", value="Open a topic channel and type `/` to see the available commands.", inline=False)
        embed.add_field(name="Website", value="https://sccompanion.org", inline=False)
        message = None
        if isinstance(message_id, int):
            with suppress(discord.NotFound, discord.Forbidden, discord.HTTPException):
                message = await channel.fetch_message(message_id)
        if message is None:
            message = await self.find_recent_embed_message(channel, embed.title or "")
        if message:
            await message.edit(embed=embed)
        else:
            message = await channel.send(embed=embed)
        await self.cache.set(cache_key, message.id, 315360000)
        await self.delete_recent_duplicate_embed_messages(channel, embed.title or "", message.id, limit=250)

    async def ensure_membership_applications(self) -> None:
        """Serialize startup and recovery provisioning to prevent duplicate channels/messages."""
        async with self._membership_application_lock:
            await self._ensure_membership_applications()

    async def _ensure_membership_applications(self) -> None:
        guild = self.get_guild(self.settings.discord_guild_id or 0)
        category = guild.get_channel(self.visitor_category_id or 0) if guild else None
        if guild is None or guild.me is None or not isinstance(category, discord.CategoryChannel):
            logging.error("Could not resolve the Discord Bot Hub for membership applications")
            return

        member_role = guild.get_role(MEMBER_ROLE_ID)
        if member_role is None:
            logging.error(
                "Existing membership role %s (%s) is missing; application provisioning stopped",
                MEMBER_ROLE_NAME,
                MEMBER_ROLE_ID,
            )
            return

        application_channel = guild.get_channel(self.visitor_channels.get("member-applications", 0))
        if not isinstance(application_channel, discord.TextChannel):
            logging.error("Could not resolve the public membership application channel")
            return
        public_overwrite = application_channel.overwrites_for(guild.default_role)
        public_overwrite.send_messages = False
        public_overwrite.create_public_threads = False
        await application_channel.set_permissions(
            guild.default_role,
            overwrite=public_overwrite,
            reason="Keep the membership application panel read-only",
        )
        visitor_role = discord.utils.find(
            lambda role: role.name.casefold() == VISITOR_ROLE_NAME.casefold(), guild.roles
        )
        if visitor_role:
            visitor_overwrite = application_channel.overwrites_for(visitor_role)
            visitor_overwrite.send_messages = False
            visitor_overwrite.create_public_threads = False
            await application_channel.set_permissions(
                visitor_role,
                overwrite=visitor_overwrite,
                reason="Keep the Visitor membership application panel read-only",
            )

        owner = guild.owner
        if owner is None:
            logging.error("Could not resolve the server owner for application reviews")
            return
        review_overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            owner: discord.PermissionOverwrite(
                view_channel=True, send_messages=True, read_message_history=True
            ),
            guild.me: discord.PermissionOverwrite(
                view_channel=True, send_messages=True, read_message_history=True,
                embed_links=True, manage_messages=True,
            ),
        }
        if visitor_role:
            review_overwrites[visitor_role] = discord.PermissionOverwrite(view_channel=False)
        audit_category = guild.get_channel(AUDIT_LOG_CATEGORY_ID)
        if not isinstance(audit_category, discord.CategoryChannel):
            audit_category = discord.utils.find(
                lambda item: item.name.casefold() == AUDIT_LOG_CATEGORY_NAME.casefold(),
                guild.categories,
            )
        if not isinstance(audit_category, discord.CategoryChannel):
            logging.error("Could not resolve the audit log category for membership application reviews")
            return
        review_channel = discord.utils.find(
            lambda channel: channel.name == APPLICATION_REVIEW_CHANNEL_NAME,
            guild.text_channels,
        )
        if review_channel is None:
            review_channel = await guild.create_text_channel(
                APPLICATION_REVIEW_CHANNEL_NAME,
                category=audit_category,
                overwrites=review_overwrites,
                topic="Private membership application queue — server owner review only.",
                reason="Create private membership application review queue",
            )
        elif review_channel.category_id != audit_category.id or review_channel.overwrites != review_overwrites:
            await review_channel.edit(
                category=audit_category,
                overwrites=review_overwrites,
                sync_permissions=False,
                reason="Restore owner-only membership application reviews",
            )
        self.application_review_channel_id = review_channel.id

        cache_key = f"discord:membership-application-panel:{application_channel.id}"
        message_id = await self.cache.get(cache_key)
        panel = discord.Embed(
            title="Apply for Community Membership",
            description=(
                "Visitors can apply here to join the main Discord community as a member. "
                "Your answers and Discord identity will be sent privately to the server owner for review."
            ),
            color=discord.Color.from_rgb(54, 188, 232),
        )
        panel.add_field(name="1", value="Do you want to become a member of the community?", inline=False)
        panel.add_field(name="2", value="Will you follow the rules in place?", inline=False)
        panel.add_field(name="3", value="What is your RSI Handle?", inline=False)
        panel.add_field(name="4", value="What Star Citizen organizations are you a part of?", inline=False)
        panel.set_footer(text="Select Apply for Membership to begin. Your answers are private.")
        message = None
        if isinstance(message_id, int):
            with suppress(discord.NotFound, discord.Forbidden, discord.HTTPException):
                message = await application_channel.fetch_message(message_id)
        if message:
            await message.edit(embed=panel, view=MembershipApplicationPanelView())
        else:
            message = await application_channel.send(embed=panel, view=MembershipApplicationPanelView())
            await self.cache.set(cache_key, message.id, 315360000)

    async def submit_membership_application(
        self,
        interaction: discord.Interaction,
        rsi_handle: str,
        organizations: str,
    ) -> None:
        guild = interaction.guild
        applicant = interaction.user
        if guild is None or not isinstance(applicant, discord.Member):
            await interaction.response.send_message("This application is unavailable.", ephemeral=True)
            return
        pending_key = f"{APPLICATION_PENDING_CACHE_PREFIX}:{guild.id}:{applicant.id}"
        if await self.cache.get(pending_key):
            await interaction.response.send_message(
                embed=discord.Embed(
                    title="Application already pending",
                    description="Your application is already waiting for the server owner to review it.",
                    color=discord.Color.gold(),
                ),
                ephemeral=True,
            )
            return
        review_channel = guild.get_channel(self.application_review_channel_id or 0)
        if not isinstance(review_channel, discord.TextChannel):
            await interaction.response.send_message(
                embed=discord.Embed(title="Application unavailable", description="Please try again later.", color=discord.Color.red()),
                ephemeral=True,
            )
            return
        review_embed = discord.Embed(
            title="New Membership Application",
            description=f"{applicant.mention} has submitted a membership application.",
            color=discord.Color.gold(),
            timestamp=discord.utils.utcnow(),
        )
        review_embed.add_field(name="Applicant", value=f"{applicant} (`{applicant.id}`)", inline=False)
        review_embed.add_field(name="Applicant ID", value=str(applicant.id), inline=False)
        review_embed.add_field(name="1. Become a community member?", value="Yes", inline=False)
        review_embed.add_field(name="2. Follow the rules?", value="Yes", inline=False)
        safe_rsi_handle = discord.utils.escape_markdown(discord.utils.escape_mentions(rsi_handle))
        review_embed.add_field(name="3. RSI Handle", value=safe_rsi_handle, inline=False)
        safe_organizations = discord.utils.escape_markdown(discord.utils.escape_mentions(organizations))
        review_embed.add_field(name="4. Organizations", value=safe_organizations, inline=False)
        review_embed.set_thumbnail(url=applicant.display_avatar.url)
        review_message = await review_channel.send(embed=review_embed, view=MembershipReviewView())
        await self.cache.set(pending_key, review_message.id, 315360000)
        await interaction.response.send_message(
            embed=discord.Embed(
                title="Application submitted",
                description="Your application was sent privately to the server owner for review.",
                color=discord.Color.green(),
            ),
            ephemeral=True,
        )

    async def review_membership_application(self, interaction: discord.Interaction, approved: bool) -> None:
        guild = interaction.guild
        if guild is None or interaction.user.id != guild.owner_id:
            await interaction.response.send_message("Only the server owner can review applications.", ephemeral=True)
            return
        message = interaction.message
        embed = message.embeds[0] if message and message.embeds else None
        applicant_id_text = next(
            (field.value for field in embed.fields if field.name == "Applicant ID"), None
        ) if embed else None
        if not applicant_id_text or not str(applicant_id_text).isdigit():
            await interaction.response.send_message("The applicant could not be identified.", ephemeral=True)
            return
        applicant_id = int(applicant_id_text)
        applicant = guild.get_member(applicant_id)
        if applicant is None:
            await interaction.response.send_message("That applicant is no longer in the server.", ephemeral=True)
            return

        if approved:
            member_role = guild.get_role(MEMBER_ROLE_ID)
            visitor_role = discord.utils.find(
                lambda role: role.name.casefold() == VISITOR_ROLE_NAME.casefold(), guild.roles
            )
            if member_role is None:
                await interaction.response.send_message("The existing Members role is missing.", ephemeral=True)
                return
            updated_roles = [role for role in applicant.roles if role != visitor_role and not role.is_default()]
            if member_role not in updated_roles:
                updated_roles.append(member_role)
            await applicant.edit(roles=updated_roles, reason=f"Membership application approved by {interaction.user}")

        await self.cache.delete(f"{APPLICATION_PENDING_CACHE_PREFIX}:{guild.id}:{applicant_id}")
        result = "Approved" if approved else "Denied"
        embed.color = discord.Color.green() if approved else discord.Color.red()
        embed.title = f"Membership Application — {result}"
        embed.add_field(name="Reviewed by", value=interaction.user.mention, inline=False)
        await interaction.response.edit_message(embed=embed, view=None)
        with suppress(discord.Forbidden, discord.HTTPException):
            await applicant.send(
                f"Your membership application for **{guild.name}** was {result.lower()}."
            )

    async def ensure_bot_manager_role(self) -> None:
        guild = self.get_guild(self.settings.discord_guild_id or 0)
        if guild is None or guild.me is None:
            logging.error("Could not resolve the configured guild for Bot Manager provisioning")
            return
        me = guild.me
        if not me.guild_permissions.manage_roles or not me.guild_permissions.manage_channels:
            logging.error("Bot requires Manage Roles and Manage Channels to provision Bot Manager")
            return

        role = guild.get_role(self.hub_role_ids.get(BOT_MANAGER_ROLE_NAME, 0))
        if role is None:
            role = discord.utils.find(lambda item: item.name.casefold() == BOT_MANAGER_ROLE_NAME.casefold(), guild.roles)
        expected_role_permissions = _hub_role_permissions(BOT_MANAGER_ROLE_NAME)
        if role is None:
            role = await guild.create_role(
                name=BOT_MANAGER_ROLE_NAME,
                permissions=expected_role_permissions,
                reason="Create website and Discord bot management role",
            )
        elif (
            role.name != BOT_MANAGER_ROLE_NAME
            or role.permissions != expected_role_permissions
            or role.colour != discord.Colour.default()
            or role.hoist
            or role.mentionable
        ):
            await role.edit(
                name=BOT_MANAGER_ROLE_NAME,
                permissions=expected_role_permissions,
                colour=discord.Colour.default(),
                hoist=False,
                mentionable=False,
                reason="Restore Discord Bot Hub manager role",
            )
        self.hub_role_ids[BOT_MANAGER_ROLE_NAME] = role.id

        bot_channel_ids = {
            INVENTORY_CHANNEL_ID,
            *self.settings.command_channel_ids.values(),
            *self.visitor_channels.values(),
        }
        for channel_id in (
            self.settings.commands_channel_id,
            self.settings.exec_status_channel_id,
            self.settings.cz_timers_channel_id,
            self.settings.audit_log_channel_id,
            self.settings.feedback_forum_channel_id,
        ):
            if channel_id:
                bot_channel_ids.add(channel_id)

        visitor_category = discord.utils.find(
            lambda item: item.name.casefold() == VISITOR_CATEGORY_NAME.casefold(), guild.categories
        )
        if visitor_category:
            bot_channel_ids.add(visitor_category.id)
        for channel_id in bot_channel_ids:
            channel = guild.get_channel(channel_id)
            if channel is None:
                continue
            current_overwrite = channel.overwrites_for(role)
            overwrite = discord.PermissionOverwrite.from_pair(*current_overwrite.pair())
            overwrite.view_channel = True
            overwrite.send_messages = True
            overwrite.send_messages_in_threads = True
            overwrite.read_message_history = True
            overwrite.embed_links = True
            overwrite.attach_files = True
            overwrite.use_application_commands = True
            if overwrite != current_overwrite:
                await channel.set_permissions(role, overwrite=overwrite, reason="Grant Bot Manager access")
        logging.info("Bot Manager role %s is ready", role.id)

    async def ensure_loot_review_channel(self) -> None:
        guild = self.get_guild(self.settings.discord_guild_id or 0)
        if guild is None or guild.me is None:
            logging.error("Could not resolve the configured guild for loot review provisioning")
            return
        category = guild.get_channel(AUDIT_LOG_CATEGORY_ID)
        if not isinstance(category, discord.CategoryChannel):
            category = discord.utils.find(
                lambda item: item.name.casefold() == AUDIT_LOG_CATEGORY_NAME.casefold(), guild.categories
            )
        if category is None:
            logging.error("Could not resolve the audit log category for loot reviews")
            return
        channel = discord.utils.find(
            lambda item: item.name == LOOT_REVIEW_CHANNEL_NAME and item.category_id == category.id,
            guild.text_channels,
        )
        topic = "Private audit queue for community loot sightings. Only the configured service owner can approve or reject."
        if channel is None:
            channel = await guild.create_text_channel(
                LOOT_REVIEW_CHANNEL_NAME,
                category=category,
                overwrites=category.overwrites,
                topic=topic,
                reason="Create community loot sighting review queue",
            )
        elif channel.topic != topic or not channel.permissions_synced:
            await channel.edit(category=category, sync_permissions=True, topic=topic)
        self.loot_review_channel_id = channel.id

    async def publish_loot_review(self, report: dict) -> None:
        channel = self.get_channel(self.loot_review_channel_id or 0)
        if not isinstance(channel, discord.TextChannel):
            await self.ensure_loot_review_channel()
            channel = self.get_channel(self.loot_review_channel_id or 0)
        if not isinstance(channel, discord.TextChannel):
            raise RuntimeError("Loot review channel is unavailable.")
        view = LootSightingReviewView(int(report["id"]))
        self.add_view(view)
        message_id = report.get("review_message_id")
        message = None
        if isinstance(message_id, int):
            with suppress(discord.NotFound, discord.Forbidden, discord.HTTPException):
                message = await channel.fetch_message(message_id)
        embed = build_loot_sighting_review_embed(report)
        if message is None:
            message = await channel.send(embed=embed, view=view, silent=True)
            await self.cache.set_loot_sighting_review_message(int(report["id"]), message.id)
        else:
            await message.edit(embed=embed, view=view)

    async def restore_pending_loot_reviews(self) -> None:
        for report in await self.cache.pending_loot_sighting_reports():
            await self.publish_loot_review(report)

    async def review_loot_sighting(
        self, interaction: discord.Interaction, report_id: int, approved: bool
    ) -> None:
        if interaction.user.id not in self.settings.bot_admin_user_ids:
            await interaction.response.send_message(
                "Only the configured service owner can review item reports.",
                ephemeral=True,
            )
            return
        status = "approved" if approved else "rejected"
        changed = await self.cache.review_loot_sighting(
            report_id, status, interaction.user.id, str(interaction.user)
        )
        report = await self.cache.loot_sighting_report(report_id)
        if report is None:
            await interaction.response.send_message("That loot report no longer exists.", ephemeral=True)
            return
        if not changed:
            await interaction.response.send_message(
                f"This report was already {report['status']}.", ephemeral=True
            )
            return
        embed = build_loot_sighting_review_embed(report)
        await interaction.response.edit_message(embed=embed, view=None)
        await self.log_audit_event(
            "Loot Sighting Reviewed",
            {
                "Report": f"#{report_id}", "Item": report["item_name"],
                "Location": report["location"], "Status": status.title(),
                "Reviewer": _audit_user(interaction.user),
            },
            color=discord.Color.green() if approved else discord.Color.red(),
        )

    async def ensure_feedback_forum(self) -> None:
        channel_id = self.visitor_channels.get("feedback-and-issues") or self.settings.feedback_forum_channel_id
        channel = self.get_channel(channel_id or 0)
        try:
            if channel is None and channel_id:
                channel = await self.fetch_channel(channel_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            channel = None
        if channel is None:
            guild = self.get_guild(self.settings.discord_guild_id or 0)
            category = discord.utils.find(
                lambda item: item.name.casefold() == VISITOR_CATEGORY_NAME.casefold(),
                guild.categories if guild else [],
            )
            if guild is None or category is None or guild.me is None or not guild.me.guild_permissions.manage_channels:
                logging.error("Could not restore feedback forum %s", channel_id)
                return
            channel = await guild.create_forum(
                "feedback-and-issues",
                category=category,
                topic=FEEDBACK_FORUM_TOPIC,
                default_reaction_emoji=FEEDBACK_FORUM_DEFAULT_REACTION,
                reason="Restore protected Discord Bot Hub forum",
            )
            channel_id = channel.id
        if not isinstance(channel, discord.ForumChannel):
            logging.error("FEEDBACK_FORUM_CHANNEL_ID %s is not a Discord forum channel", channel_id)
            return
        self.visitor_channels["feedback-and-issues"] = channel.id
        if self.settings.discord_guild_id and channel.guild.id != self.settings.discord_guild_id:
            logging.error("FEEDBACK_FORUM_CHANNEL_ID %s is not in the configured guild", channel_id)
            return
        category = channel.guild.get_channel(self.visitor_category_id or 0)
        if isinstance(category, discord.CategoryChannel) and (
            channel.name != "feedback-and-issues"
            or channel.category_id != category.id
            or channel.topic != FEEDBACK_FORUM_TOPIC
        ):
            await channel.edit(
                name="feedback-and-issues",
                category=category,
                topic=FEEDBACK_FORUM_TOPIC,
                sync_permissions=True,
                reason="Restore Discord Bot Hub feedback forum settings",
            )

        member = channel.guild.me
        if member is None:
            logging.error("Could not resolve the bot member for feedback forum %s", channel_id)
            return
        required = {
            "view_channel": True,
            "send_messages": True,
            "send_messages_in_threads": True,
            "create_public_threads": True,
            "embed_links": True,
            "attach_files": True,
            "read_message_history": True,
            "manage_threads": True,
        }
        permissions = channel.permissions_for(member)
        missing = [name for name in required if not getattr(permissions, name, False)]
        if missing and permissions.manage_channels:
            overwrite = channel.overwrites_for(member)
            for name, value in required.items():
                setattr(overwrite, name, value)
            try:
                await channel.set_permissions(
                    member,
                    overwrite=overwrite,
                    reason="Star Citizen Companion feedback forum integration",
                )
                logging.info("Applied feedback forum permissions for the bot in channel %s", channel_id)
            except (discord.Forbidden, discord.HTTPException):
                logging.exception("Could not apply feedback forum permissions in channel %s", channel_id)
        elif missing:
            logging.error(
                "Feedback forum %s is missing bot permissions (%s); grant Manage Channels once or apply them manually",
                channel_id,
                ", ".join(missing),
            )

        await self.configure_feedback_forum(channel)
        await self.sync_feedback_template(channel)

    async def sync_feedback_template(self, channel: discord.ForumChannel) -> None:
        cache_key = f"{FEEDBACK_TEMPLATE_CACHE_PREFIX}:{channel.id}"
        thread_id = await self.cache.get(cache_key)
        thread: discord.Thread | None = None
        if isinstance(thread_id, int):
            try:
                candidate = self.get_channel(thread_id) or await self.fetch_channel(thread_id)
                if isinstance(candidate, discord.Thread):
                    thread = candidate
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                thread = None

        embed = build_feedback_template_embed()
        content = (
            "Use the **Feedback / Report Issue** button beside **Log out** on "
            "[sccompanion.org](https://sccompanion.org), or copy this example into a new forum post. "
            "Reply in your post whenever you need to add more details, screenshots, or videos."
        )
        try:
            if thread is None:
                applied_tags = list(channel.available_tags[:1]) if channel.flags.require_tag else []
                created = await channel.create_thread(
                    name="Example: How to submit a helpful website report",
                    content=content,
                    embed=embed,
                    allowed_mentions=discord.AllowedMentions.none(),
                    applied_tags=applied_tags,
                    reason="Create the Star Citizen Companion feedback template",
                )
                thread = created.thread
                await self.cache.set(cache_key, thread.id, 315360000)
            else:
                if thread.archived:
                    await thread.edit(archived=False, reason="Refresh the feedback template")
                starter = await thread.fetch_message(thread.id)
                await starter.edit(content=content, embed=embed, allowed_mentions=discord.AllowedMentions.none())
            try:
                await thread.edit(pinned=True, reason="Keep the feedback template visible")
            except (discord.Forbidden, discord.HTTPException):
                logging.warning("Could not pin feedback template thread %s", thread.id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException, ValueError):
            logging.exception("Could not create or update the feedback forum template in channel %s", channel.id)

    async def ensure_inventory_search_channel(self) -> None:
        try:
            channel = self.get_channel(INVENTORY_CHANNEL_ID) or await self.fetch_channel(INVENTORY_CHANNEL_ID)
            if not isinstance(channel, discord.TextChannel):
                logging.error("INVENTORY_CHANNEL_ID %s is not a Discord text channel", INVENTORY_CHANNEL_ID)
                return
            if self.settings.discord_guild_id and channel.guild.id != self.settings.discord_guild_id:
                logging.error("INVENTORY_CHANNEL_ID %s is not in the configured guild", INVENTORY_CHANNEL_ID)
                return
            logging.info("Inventory search restricted to Discord channel %s", INVENTORY_CHANNEL_ID)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            logging.exception("Could not access inventory search channel %s", INVENTORY_CHANNEL_ID)

    async def _exec_status_loop(self) -> None:
        while not self.is_closed():
            await asyncio.sleep(60)
            await self.sync_exec_status_message()

    async def _cz_timers_loop(self) -> None:
        while not self.is_closed():
            await asyncio.sleep(60)
            await self.sync_cz_timers_message()

    async def sync_commands_reference_message(self) -> None:
        channel_ids = []
        visitor_channel_id = self.visitor_channels.get("about-the-bot")
        if self.settings.commands_channel_id and not visitor_channel_id:
            channel_ids.append(self.settings.commands_channel_id)
        if visitor_channel_id and visitor_channel_id not in channel_ids:
            channel_ids.append(visitor_channel_id)
        for channel_id in channel_ids:
            await self._sync_commands_reference_channel(
                channel_id,
                directory_only=channel_id == visitor_channel_id,
            )

    async def _sync_commands_reference_channel(self, channel_id: int, *, directory_only: bool = False) -> None:
        try:
            channel = await self.fetch_channel(channel_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            logging.warning("Could not access commands reference channel %s", channel_id)
            return

        if not isinstance(channel, discord.abc.Messageable) or not hasattr(channel, "fetch_message"):
            logging.warning("Commands reference channel %s is not messageable", channel_id)
            return

        directory_only = directory_only or getattr(channel, "name", None) == "about-the-bot"
        embeds = (
            [build_visitor_channel_directory_embed(self.visitor_channels)]
            if directory_only
            else build_commands_reference_embeds(self.settings)
        )
        cache_key = f"discord:commands-reference-message:{channel_id}"
        cached_message_ids = await self.cache.get(cache_key)
        message_ids: list[int] = []
        if isinstance(cached_message_ids, int):
            message_ids = [cached_message_ids]
        elif isinstance(cached_message_ids, list):
            message_ids = [message_id for message_id in cached_message_ids if isinstance(message_id, int)]

        existing_messages = await self.find_recent_embed_messages(
            channel,
            {embed.title for embed in embeds if embed.title},
            limit=250,
        )
        updated_message_ids: list[int] = []
        for index, embed in enumerate(embeds):
            message = None
            if index < len(message_ids):
                try:
                    message = await channel.fetch_message(message_ids[index])
                    if not any(message_embed.title == embed.title for message_embed in message.embeds):
                        message = None
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    logging.info(
                        "Could not update commands reference message part %s; searching for an existing one",
                        index + 1,
                    )

            if message is None and embed.title:
                matching_messages = existing_messages.get(embed.title, [])
                if matching_messages:
                    message = matching_messages[0]

            if message is not None:
                if not _message_embed_matches(message, embed) or message.content:
                    await message.edit(content=None, embed=embed)
                    await asyncio.sleep(1)
                updated_message_ids.append(message.id)
                if embed.title:
                    await self.delete_recent_duplicate_embed_messages(channel, embed.title, message.id, limit=250)
                continue

            message = await channel.send(embed=embed, silent=True)
            await asyncio.sleep(1)
            updated_message_ids.append(message.id)
            if embed.title:
                await self.delete_recent_duplicate_embed_messages(channel, embed.title, message.id, limit=250)

        for stale_message_id in message_ids[len(embeds):]:
            try:
                message = await channel.fetch_message(stale_message_id)
                await message.delete()
                await asyncio.sleep(1)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                logging.info("Could not delete stale commands reference message %s", stale_message_id)

        if directory_only:
            async for message in channel.history(limit=250):
                if message.author.id != self.user.id or not message.embeds:
                    continue
                title = message.embeds[0].title or ""
                if title == "Discord Bot Commands - Channel Directory" or title.startswith(
                    "Welcome to the Star Citizen Companion Bot Hub"
                ):
                    continue
                if title.startswith("Discord Bot Commands - ") or title == "Example /lookup Response":
                    with suppress(discord.NotFound, discord.Forbidden, discord.HTTPException):
                        await message.delete()
                        await asyncio.sleep(1)

        await self.cache.set(cache_key, updated_message_ids, 315360000)
        logging.info("Synced %s commands reference message(s) in channel %s", len(updated_message_ids), channel_id)

    async def sync_exec_status_message(self) -> None:
        visitor_channel_id = self.visitor_channels.get("timers")
        channel_ids = {visitor_channel_id} if visitor_channel_id else {self.settings.exec_status_channel_id}
        channel_ids.discard(None)
        if channel_ids:
            try:
                status_context = await self.resolve_exec_status_context()
                embed = build_exec_status_embed(status_context)
                for channel_id in channel_ids:
                    await self._sync_exec_status_channel(channel_id, embed)
            except Exception:
                logging.warning("Could not fetch Executive Hangar timer for primary status message")
        for guild in self.guilds:
            if guild.id == self.settings.discord_guild_id:
                continue
            configured = await self.cache.guild_bot_settings(guild.id)
            if configured is None:
                continue
            modules = normalize_module_settings(configured.get("modules"))
            channel_id = timer_dashboard_channel_id(modules)
            if channel_id is None:
                continue
            try:
                guild_context = await self.resolve_exec_status_context(timer_scope(self, guild.id))
                await self._sync_exec_status_channel(channel_id, build_exec_status_embed(guild_context))
            except Exception:
                logging.warning("Could not refresh Executive Hangar timer in guild %s", guild.id)

    async def _sync_exec_status_channel(self, channel_id: int, embed: discord.Embed) -> None:
        try:
            channel = await self.fetch_channel(channel_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            logging.warning("Could not access Executive Hangar status channel %s", channel_id)
            return
        if not isinstance(channel, discord.abc.Messageable) or not hasattr(channel, "fetch_message"):
            logging.warning("Executive Hangar status channel %s is not messageable", channel_id)
            return

        cache_key = f"discord:exec-status-message:{channel_id}"
        message_id = await self.cache.get(cache_key)

        if isinstance(message_id, int):
            try:
                message = await channel.fetch_message(message_id)
                if not _message_embed_matches(message, embed) or message.content:
                    await message.edit(content=None, embed=embed)
                await self.delete_recent_duplicate_embed_messages(channel, "Executive Hangar Clock", message.id)
                logging.info("Updated Executive Hangar status message %s", message_id)
                return
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                logging.info("Could not update previous Executive Hangar status message; searching for an existing one")

        existing_message = await self.find_recent_embed_message(channel, "Executive Hangar Clock")
        if existing_message is not None:
            try:
                if not _message_embed_matches(existing_message, embed) or existing_message.content:
                    await existing_message.edit(content=None, embed=embed)
                await self.cache.set(cache_key, existing_message.id, 315360000)
                await self.delete_recent_duplicate_embed_messages(channel, "Executive Hangar Clock", existing_message.id)
                logging.info("Reused Executive Hangar status message %s", existing_message.id)
                return
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                logging.info("Could not reuse existing Executive Hangar status message; creating a new one")

        message = await channel.send(embed=embed)
        await self.cache.set(cache_key, message.id, 315360000)
        await self.delete_recent_duplicate_embed_messages(channel, "Executive Hangar Clock", message.id)
        logging.info("Created Executive Hangar status message %s", message.id)

    async def find_recent_embed_message(
        self,
        channel: discord.abc.Messageable,
        title: str,
    ) -> discord.Message | None:
        if not hasattr(channel, "history"):
            return None

        try:
            async for message in channel.history(limit=50):
                if self.user is not None and message.author.id != self.user.id:
                    continue
                if any(embed.title == title for embed in message.embeds):
                    return message
        except (discord.Forbidden, discord.HTTPException):
            return None

        return None

    async def find_recent_embed_messages(
        self,
        channel: discord.abc.Messageable,
        titles: set[str],
        limit: int = 50,
    ) -> dict[str, list[discord.Message]]:
        messages_by_title = {title: [] for title in titles}
        if not titles or not hasattr(channel, "history"):
            return messages_by_title

        try:
            async for message in channel.history(limit=limit):
                if self.user is not None and message.author.id != self.user.id:
                    continue
                for embed in message.embeds:
                    if embed.title in messages_by_title:
                        messages_by_title[embed.title].append(message)
                        break
        except (discord.Forbidden, discord.HTTPException):
            logging.info("Could not scan for existing embed messages")

        return messages_by_title

    async def delete_recent_duplicate_embed_messages(
        self,
        channel: discord.abc.Messageable,
        title: str,
        keep_message_id: int,
        limit: int = 50,
    ) -> None:
        if not hasattr(channel, "history"):
            return

        try:
            async for message in channel.history(limit=limit):
                if message.id == keep_message_id:
                    continue
                if self.user is not None and message.author.id != self.user.id:
                    continue
                if any(embed.title == title for embed in message.embeds):
                    try:
                        await message.delete()
                        logging.info("Deleted duplicate %s message %s", title, message.id)
                    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                        logging.info("Could not delete duplicate %s message %s", title, message.id)
        except (discord.Forbidden, discord.HTTPException):
            logging.info("Could not scan for duplicate %s messages", title)

    async def sync_cz_timers_message(self) -> None:
        visitor_channel_id = self.visitor_channels.get("timers")
        channel_ids = {visitor_channel_id} if visitor_channel_id else {self.settings.cz_timers_channel_id}
        channel_ids.discard(None)
        if not channel_ids:
            return

        timers = await get_cz_dashboard_timers(self.cache)
        embed = build_cz_dashboard_embed(timers)
        for channel_id in channel_ids:
            await self._sync_cz_timers_channel(channel_id, embed)
        for guild in self.guilds:
            if guild.id != self.settings.discord_guild_id:
                await self.sync_guild_timer_dashboard(guild)

    async def _sync_cz_timers_channel(self, channel_id: int, embed: discord.Embed) -> None:
        try:
            channel = await self.fetch_channel(channel_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            logging.warning("Could not access CZ timers channel %s", channel_id)
            return
        if not isinstance(channel, discord.abc.Messageable) or not hasattr(channel, "fetch_message"):
            logging.warning("CZ timers channel %s is not messageable", channel_id)
            return

        view = CZTimerDashboardView()
        cache_key = f"discord:cz-timers-message:{channel_id}"
        message_id = await self.cache.get(cache_key)

        if isinstance(message_id, int):
            try:
                message = await channel.fetch_message(message_id)
                if not _message_embed_matches(message, embed) or message.content:
                    await message.edit(content=None, embed=embed, view=view)
                else:
                    await message.edit(view=view)
                logging.info("Updated CZ timers dashboard message %s", message_id)
                await self.delete_recent_duplicate_embed_messages(channel, "Contested Zone Timers", message.id)
                return
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                logging.info("Could not update previous CZ timers dashboard; scanning for an existing one")

        existing_message = await self.find_recent_embed_message(channel, "Contested Zone Timers")
        if existing_message is not None:
            if not _message_embed_matches(existing_message, embed) or existing_message.content:
                await existing_message.edit(content=None, embed=embed, view=view)
            else:
                await existing_message.edit(view=view)
            await self.cache.set(cache_key, existing_message.id, 315360000)
            await self.delete_recent_duplicate_embed_messages(channel, "Contested Zone Timers", existing_message.id)
            logging.info("Adopted existing CZ timers dashboard message %s", existing_message.id)
            return

        message = await channel.send(embed=embed, view=view)
        await self.cache.set(cache_key, message.id, 315360000)
        await self.delete_recent_duplicate_embed_messages(channel, "Contested Zone Timers", message.id)
        logging.info("Created CZ timers dashboard message %s", message.id)

    async def sync_visitor_command_examples(self) -> None:
        for channel_name, embed in build_visitor_command_example_embeds().items():
            if channel_name in VISITOR_REMOVED_CHANNEL_NAMES:
                continue
            channel = self.get_channel(self.visitor_channels.get(channel_name, 0))
            if not isinstance(channel, discord.TextChannel):
                continue
            cache_key = f"discord:visitor-example:{channel.id}"
            message_id = await self.cache.get(cache_key)
            message = None
            if isinstance(message_id, int):
                with suppress(discord.NotFound, discord.Forbidden, discord.HTTPException):
                    message = await channel.fetch_message(message_id)
            if message is None:
                message = await self.find_recent_embed_message(channel, embed.title or "")
            if message:
                await message.edit(content=None, embed=embed)
            else:
                message = await channel.send(embed=embed, silent=True)
            await self.cache.set(cache_key, message.id, 315360000)
            await self._ensure_timer_dashboard_below_example(channel_name, channel)

    async def sync_loot_command_example(self) -> None:
        channel = self.get_channel(LOOT_CHANNEL_ID)
        if channel is None:
            with suppress(discord.NotFound, discord.Forbidden, discord.HTTPException):
                channel = await self.fetch_channel(LOOT_CHANNEL_ID)
        if not isinstance(channel, discord.TextChannel):
            logging.warning("Loot command channel %s could not be resolved", LOOT_CHANNEL_ID)
            return
        embed = build_loot_command_example_embed()
        cache_key = f"discord:loot-example:{channel.id}"
        message_id = await self.cache.get(cache_key)
        message = None
        if isinstance(message_id, int):
            with suppress(discord.NotFound, discord.Forbidden, discord.HTTPException):
                message = await channel.fetch_message(message_id)
        if message is None:
            message = await self.find_recent_embed_message(channel, embed.title or "")
        if message:
            await message.edit(content=None, embed=embed)
        else:
            message = await channel.send(embed=embed, silent=True)
        await self.cache.set(cache_key, message.id, 315360000)
        await self.delete_recent_duplicate_embed_messages(channel, embed.title or "", message.id)

    async def _ensure_timer_dashboard_below_example(
        self,
        channel_name: str,
        channel: discord.TextChannel,
    ) -> None:
        if channel_name != "timers":
            return
        timer_specs = (
            ("discord:exec-status-message", "Executive Hangar Clock"),
            ("discord:cz-timers-message", "Contested Zone Timers"),
        )
        for cache_prefix, embed_title in timer_specs:
            marker_key = f"discord:visitor-example-before-dashboard:v1:{cache_prefix}:{channel.id}"
            if await self.cache.get(marker_key):
                continue
            dashboard_cache_key = f"{cache_prefix}:{channel.id}"
            dashboard_message_id = await self.cache.get(dashboard_cache_key)
            dashboard_message = None
            if isinstance(dashboard_message_id, int):
                with suppress(discord.NotFound, discord.Forbidden, discord.HTTPException):
                    dashboard_message = await channel.fetch_message(dashboard_message_id)
            if dashboard_message is None:
                dashboard_message = await self.find_recent_embed_message(channel, embed_title)
            if dashboard_message is not None:
                with suppress(discord.NotFound, discord.Forbidden, discord.HTTPException):
                    await dashboard_message.delete()
            await self.cache.set(dashboard_cache_key, None, 315360000)
            await self.cache.set(marker_key, True, 315360000)

    async def resolve_exec_cycle_start(self, guild_id: int | None = None) -> tuple[int, str]:
        override = await self.cache.get(exec_override_cache_key(guild_id))
        if isinstance(override, dict) and isinstance(override.get("cycle_start_unix"), int):
            return override["cycle_start_unix"], "Manual override"

        cycle_start = await fetch_exec_cycle_start_unix(self.settings.http_timeout_seconds)
        return cycle_start, "contestedzonetimers.com community timer"

    async def resolve_exec_status_context(self, guild_id: int | None = None) -> dict:
        source_cycle_start = await fetch_exec_cycle_start_unix(self.settings.http_timeout_seconds)
        source_status = calculate_exec_hangar_status(source_cycle_start)
        override = await self.cache.get(exec_override_cache_key(guild_id))

        if isinstance(override, dict) and isinstance(override.get("cycle_start_unix"), int):
            corrected_status = calculate_exec_hangar_status(override["cycle_start_unix"])
            return {
                "source_status": source_status,
                "corrected_status": corrected_status,
                "override": override,
            }

        return {
            "source_status": source_status,
            "corrected_status": None,
            "override": None,
        }

    async def close(self) -> None:
        if self._exec_status_task:
            self._exec_status_task.cancel()
        if self._cz_timers_task:
            self._cz_timers_task.cancel()
        if self._hub_recovery_task:
            self._hub_recovery_task.cancel()
        if self._website_deployment_task:
            self._website_deployment_task.cancel()
        if self._trade_store_sync_task:
            self._trade_store_sync_task.cancel()
        if self._guild_sync_task:
            self._guild_sync_task.cancel()
        for task in self._shared_recovery_tasks.values():
            task.cancel()
        await self.sources.close()
        await self.cache.close()
        await super().close()

    async def log_audit_event(
        self,
        title: str,
        fields: dict[str, object],
        color: discord.Color = discord.Color.blurple(),
    ) -> None:
        await self.cache.add_audit_event(title, fields)

        if not self.settings.audit_log_channel_id:
            return

        try:
            channel = await self.fetch_channel(self.settings.audit_log_channel_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            logging.warning("Could not access AUDIT_LOG_CHANNEL_ID %s", self.settings.audit_log_channel_id)
            return

        if not isinstance(channel, discord.abc.Messageable):
            logging.warning("AUDIT_LOG_CHANNEL_ID does not point to a messageable channel")
            return

        embed = discord.Embed(title=title, color=color, timestamp=discord.utils.utcnow())
        for name, value in fields.items():
            embed.add_field(name=name, value=_truncate_audit_value(value), inline=False)

        try:
            await channel.send(embed=embed)
        except discord.HTTPException:
            logging.warning("Could not send audit log event: %s", title)



def _interaction_command_name(interaction: discord.Interaction) -> str:
    data = interaction.data if isinstance(interaction.data, dict) else {}
    names = [str(data.get("name") or "unknown")]
    options = data.get("options")

    while isinstance(options, list) and options:
        option = options[0]
        if not isinstance(option, dict) or option.get("type") not in (1, 2):
            break
        names.append(str(option.get("name") or "unknown"))
        options = option.get("options")

    return _normalize_command_name(" ".join(names))


def _automatic_module_channel_id(guild: discord.Guild | None, module_key: str) -> int | None:
    """Resolve an automatic channel even if its saved ID has not caught up yet."""
    if guild is None:
        return None
    expected_name = module_key.replace("_", "-")
    category = discord.utils.find(lambda item: item.name == "SC Companion", guild.categories)
    channel = discord.utils.find(
        lambda item: item.name == expected_name
        and (category is None or item.category_id == category.id),
        guild.text_channels,
    )
    return channel.id if channel is not None else None


def _normalize_command_name(value: str) -> str:
    return " ".join(value.lower().strip().removeprefix("/").replace("_", " ").split())


def _format_interaction_options(interaction: discord.Interaction) -> str:
    data = interaction.data if isinstance(interaction.data, dict) else {}
    options = _flatten_interaction_options(data.get("options"))
    if not options:
        return ""

    text = "\n".join(f"{name}: {value}" for name, value in options)
    return text if len(text) <= 900 else f"{text[:897].rstrip()}..."


def _flatten_interaction_options(options: object, prefix: str = "") -> list[tuple[str, object]]:
    if not isinstance(options, list):
        return []

    flattened: list[tuple[str, object]] = []
    for option in options:
        if not isinstance(option, dict):
            continue
        name = str(option.get("name") or "unknown")
        full_name = f"{prefix}.{name}" if prefix else name
        nested_options = option.get("options")
        if isinstance(nested_options, list):
            flattened.extend(_flatten_interaction_options(nested_options, full_name))
            continue
        if "value" in option:
            flattened.append((full_name, option["value"]))
    return flattened


def _audit_user(user: discord.abc.User) -> str:
    return f"{user} (`{user.id}`)"


def _audit_channel(channel_id: int | None) -> str:
    return f"<#{channel_id}>" if channel_id else "Unknown"


def _truncate_audit_value(value: object) -> str:
    text = str(value)
    return text if len(text) <= 1024 else f"{text[:1021].rstrip()}..."


SUPPORT_URL = "https://square.link/u/g43WPEyN?src=embed"
SUPPORT_QR_URL = "https://sccompanion.org/assets/media/support-square-qr.png"


def build_about_bot_embed(guild: discord.Guild | None = None) -> discord.Embed:
    embed = discord.Embed(
        title="About SC Companion",
        description=(
            "SC Companion is a community-built Star Citizen assistant for ship and item research, mining, "
            "blueprints, missions, Wikelo contracts, personal inventory, and trade planning. Server managers "
            "choose which modules and channels are enabled for their community."
        ),
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="Support operating costs",
        value=(
            f"SC Companion is free to use. [Optional contributions through Square]({SUPPORT_URL}) help cover "
            "hosting, domains, storage, security, and other operating costs. Contributions are not tax-deductible "
            "and do not provide ownership, guaranteed service, special access, or an advantage over other members."
        ),
        inline=False,
    )
    embed.add_field(name="Website", value="https://sccompanion.org", inline=False)
    if guild is not None:
        embed.add_field(
            name="This Discord",
            value=f"Installed for **{guild.name}**. Server managers configure modules and channels at sccompanion.org.",
            inline=False,
        )
    embed.set_image(url=SUPPORT_QR_URL)
    embed.set_footer(text="Unofficial fan project · Not affiliated with Cloud Imperium Games or Roberts Space Industries")
    return embed


MODULE_EXAMPLES = {
    "ship_search": "/ship name: Carrack",
    "mining_tools": "/mining material: Quantanium\n/industry split gross:1200000 crew:Alex,Bex,Cato expenses:150000",
    "blueprints": "/blueprint name: NDB-28 Repeater\n/myblueprints name: Quantum Drive",
    "missions_wikelo": "/mission name: Bounty Hunter\n/wikelo item: Golem",
    "item_locator": "/item search name: FS-9 LMG\n/loot search name: ADP-mk4 Arms Justified",
    "inventory_search": "/inventory search item: FS-9 station: Port Tressler",
    "trade_tools": "/commodity name: Gold\n/trade routing starting_point: Area18 investment:500000",
}


def build_guild_command_guide_embed(module_keys: list[str]) -> discord.Embed:
    embed = discord.Embed(
        title="SC Companion Command Guide",
        description="Enabled commands for this channel. Type `/`, choose the command, and complete its options.",
        color=discord.Color.blurple(),
    )
    for key in module_keys:
        module = BOT_MODULES[key]
        commands = ", ".join(f"`/{name}`" for name in module["commands"])
        examples = "\n".join(f"`{line}`" for line in MODULE_EXAMPLES[key].splitlines())
        embed.add_field(
            name=str(module["label"]),
            value=f"{module['description']}\n**Commands:** {commands}\n**Examples:**\n{examples}",
            inline=False,
        )
    embed.set_footer(text="Examples are updated automatically from this server's Bot Management settings")
    return embed


@app_commands.command(name="ship", description="Look up a Star Citizen ship or vehicle.")
@app_commands.describe(name="The ship or vehicle name to search for.")
async def ship_command(interaction: discord.Interaction, name: str) -> None:
    bot = interaction.client
    if not isinstance(bot, GameAssistBot):
        await interaction.response.send_message("Bot is not fully initialized.", ephemeral=True)
        return

    await interaction.response.defer(thinking=True, ephemeral=True)
    result = await bot.sources.lookup_ship(name)

    if result is None:
        await interaction.followup.send(f"No ship or vehicle found for `{name}`.", ephemeral=True)
        return

    await interaction.followup.send(embed=build_ship_embed(result), ephemeral=True)


@ship_command.autocomplete("name")
async def ship_name_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    bot = interaction.client
    if not isinstance(bot, GameAssistBot):
        return []

    names = await bot.sources.autocomplete_ships(current)
    return [
        app_commands.Choice(name=name[:100], value=name[:100])
        for name in names[:25]
    ]


@app_commands.command(name="commodity", description="Look up Star Citizen commodity prices and locations.")
@app_commands.describe(
    name="Commodity name or code.",
    system="Optional star system filter for both purchase and sell locations.",
    purchase_system="Optional system filter for purchase locations only.",
    sell_system="Optional system filter for sell locations only.",
    quantity_scu="Optional SCU amount for estimated buy cost and sell payout.",
)
async def commodity_command(
    interaction: discord.Interaction,
    name: str,
    system: str | None = None,
    purchase_system: str | None = None,
    sell_system: str | None = None,
    quantity_scu: float | None = None,
) -> None:
    bot = interaction.client
    if not isinstance(bot, GameAssistBot):
        await interaction.response.send_message("Bot is not fully initialized.", ephemeral=True)
        return

    if quantity_scu is not None and quantity_scu <= 0:
        await interaction.response.send_message("Quantity must be greater than 0 SCU.", ephemeral=True)
        return

    await interaction.response.defer(thinking=True, ephemeral=True)
    result = await bot.sources.lookup_commodity(name, system, purchase_system, sell_system)

    if result is None:
        await interaction.followup.send(f"No commodity found for `{name}`.", ephemeral=True)
        return

    await interaction.followup.send(
        embed=build_commodity_embed(result, quantity_scu, system, purchase_system, sell_system),
        ephemeral=True,
    )


@commodity_command.autocomplete("name")
async def commodity_name_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    bot = interaction.client
    if not isinstance(bot, GameAssistBot):
        return []

    names = await bot.sources.autocomplete_commodities(current)
    return [
        app_commands.Choice(name=name[:100], value=name[:100])
        for name in names[:25]
    ]


@commodity_command.autocomplete("system")
@commodity_command.autocomplete("purchase_system")
@commodity_command.autocomplete("sell_system")
async def commodity_system_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    del interaction
    systems = ["Stanton", "Pyro", "Nyx"]
    normalized = current.lower().strip()
    matches = [system for system in systems if system.lower().startswith(normalized)]
    if not matches and normalized:
        matches = [system for system in systems if normalized in system.lower()]
    return [app_commands.Choice(name=system, value=system) for system in matches[:25]]


@app_commands.command(name="mining", description="Find where to mine Star Citizen materials.")
@app_commands.describe(
    material="Required material name, code, or rock signature (for example, 6400).",
    system="Optional star system filter.",
    planet="Optional planet, moon, lagrange point, or location filter.",
)
async def mining_command(
    interaction: discord.Interaction,
    material: str,
    system: str | None = None,
    planet: str | None = None,
) -> None:
    bot = interaction.client
    if not isinstance(bot, GameAssistBot):
        await interaction.response.send_message("Bot is not fully initialized.", ephemeral=True)
        return

    await interaction.response.defer(thinking=True, ephemeral=True)
    result: MiningLocationResult | None = None
    terms = _mining_multi_search_terms(material)
    if len(terms) == 1 and not _has_mining_multi_separator(material):
        result = await bot.sources.lookup_mining_material(material, system, planet)
        if result is None:
            terms = _mining_space_search_terms(material)

    if len(terms) > 1:
        embed = await build_multi_mining_signature_embed(bot.sources, material, terms)
        await interaction.followup.send(embed=embed, ephemeral=True)
        return

    if result is None:
        result = await bot.sources.lookup_mining_material(material, system, planet)
    if result is None:
        await interaction.followup.send(f"No mining material found for `{material}`.", ephemeral=True)
        return
    result = await apply_community_mining_locations(bot.cache, result)

    kwargs = {
        "embed": build_mining_embed(result, system, planet),
        "ephemeral": True,
    }
    if _mining_location_page_count(result) > 1:
        kwargs["view"] = MiningLocationView(result, system, planet)
    await interaction.followup.send(**kwargs)


@mining_command.autocomplete("material")
async def mining_material_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    bot = interaction.client
    if not isinstance(bot, GameAssistBot):
        return []

    prefix, partial = _mining_autocomplete_prefix(current)
    names = await bot.sources.autocomplete_mining_materials(partial)
    return [
        app_commands.Choice(name=f"{prefix}{name}"[:100], value=f"{prefix}{name}"[:100])
        for name in names[:25]
    ]


@mining_command.autocomplete("system")
async def mining_system_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    return await commodity_system_autocomplete(interaction, current)


@mining_command.autocomplete("planet")
async def mining_planet_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    bot = interaction.client
    if not isinstance(bot, GameAssistBot):
        return []

    namespace = interaction.namespace
    system = namespace.system if isinstance(getattr(namespace, "system", None), str) else None
    names = await bot.sources.autocomplete_mining_locations(current, system)
    return [
        app_commands.Choice(name=name[:100], value=name[:100])
        for name in names[:25]
    ]


industry_group = app_commands.Group(name="industry", description="Mining and salvage operation planning tools.")


@industry_group.command(name="split", description="Divide an operation payout evenly after shared expenses.")
@app_commands.describe(
    gross="Total operation payout in aUEC.",
    crew="Comma-separated crew names.",
    expenses="Shared operation expenses in aUEC.",
)
async def industry_split_command(
    interaction: discord.Interaction,
    gross: int,
    crew: str,
    expenses: int = 0,
) -> None:
    names = [name.strip() for name in crew.split(",") if name.strip()]
    if gross < 0 or expenses < 0 or expenses > gross:
        await interaction.response.send_message(
            "Gross and expenses cannot be negative, and expenses cannot exceed gross.", ephemeral=True
        )
        return
    if not names:
        await interaction.response.send_message("Add at least one crew member.", ephemeral=True)
        return
    net = gross - expenses
    base_share, remainder = divmod(net, len(names))
    payouts = [f"**{name}** — {base_share + (1 if index < remainder else 0):,} aUEC" for index, name in enumerate(names)]
    embed = discord.Embed(title="Industry Crew Payout", color=discord.Color.orange())
    embed.description = "\n".join(payouts)
    embed.add_field(name="Gross", value=f"{gross:,} aUEC")
    embed.add_field(name="Expenses", value=f"{expenses:,} aUEC")
    embed.add_field(name="Net", value=f"{net:,} aUEC")
    embed.set_footer(text="Any indivisible remainder is assigned one aUEC at a time in listed order.")
    await interaction.response.send_message(embed=embed, ephemeral=True)


@industry_group.command(name="refinery", description="Calculate a refinery job's completion time.")
@app_commands.describe(job="Job label or refinery.", hours="Whole hours remaining.", minutes="Additional minutes remaining.")
async def industry_refinery_command(
    interaction: discord.Interaction,
    job: str,
    hours: app_commands.Range[int, 0, 999] = 0,
    minutes: app_commands.Range[int, 0, 59] = 0,
) -> None:
    if hours == 0 and minutes == 0:
        await interaction.response.send_message("The refinery duration must be longer than zero.", ephemeral=True)
        return
    completion_unix = int(discord.utils.utcnow().timestamp()) + ((hours * 60 + minutes) * 60)
    embed = discord.Embed(title="Refinery Completion", color=discord.Color.orange())
    embed.add_field(name="Job", value=job, inline=False)
    embed.add_field(name="Completes", value=f"<t:{completion_unix}:F> (<t:{completion_unix}:R>)", inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)


@industry_group.command(name="brief", description="Create a Discord-ready mining or salvage operation brief.")
@app_commands.describe(
    operation="Operation name.",
    location="Rally point or operating location.",
    objective="Mining or salvage objective.",
    crew="Assigned crew or open roles.",
    notes="Equipment, route, communications, or safety notes.",
)
async def industry_brief_command(
    interaction: discord.Interaction,
    operation: str,
    location: str,
    objective: str,
    crew: str | None = None,
    notes: str | None = None,
) -> None:
    embed = discord.Embed(title=operation, description=objective, color=discord.Color.orange())
    embed.add_field(name="Rally Point", value=location, inline=False)
    if crew:
        embed.add_field(name="Crew / Open Roles", value=crew, inline=False)
    if notes:
        embed.add_field(name="Notes", value=notes, inline=False)
    embed.set_footer(text="Prepared with SC Companion Industry Operations")
    await interaction.response.send_message(embed=embed, ephemeral=True)


@app_commands.command(name="miningadd", description="Add a community-reported mining location for a material.")
@app_commands.describe(
    material="Mineable material name or code.",
    system="Star system where the material was found.",
    location_type="Type of location to add.",
    location="Planet, moon, lagrange point, or point of interest name.",
)
@app_commands.choices(
    location_type=[
        app_commands.Choice(name="Lagrange Point", value="lagrange_points"),
        app_commands.Choice(name="Planet", value="planets"),
        app_commands.Choice(name="Moon", value="moons"),
        app_commands.Choice(name="Point of Interest", value="points_of_interest"),
    ]
)
async def miningadd_command(
    interaction: discord.Interaction,
    material: str,
    system: str,
    location_type: app_commands.Choice[str],
    location: str,
) -> None:
    bot = interaction.client
    if not isinstance(bot, GameAssistBot):
        await interaction.response.send_message("Bot is not fully initialized.", ephemeral=True)
        return

    if not _can_manage_change_commands(interaction, bot.settings):
        await interaction.response.send_message("You do not have permission to add mining locations.", ephemeral=True)
        return

    await interaction.response.defer(thinking=True, ephemeral=True)
    result = await bot.sources.lookup_mining_material(material)
    if result is None:
        await interaction.followup.send(f"No mining material found for `{material}`.", ephemeral=True)
        return

    entry = {
        "material": result.material_name,
        "system": system.strip(),
        "location_type": location_type.value,
        "location": location.strip(),
        "reported_by": str(interaction.user),
    }
    if interaction.user.id not in bot.settings.bot_admin_user_ids:
        review_id = await bot.cache.submit_review_request(
            "mining", entry, submitted_by=interaction.user.id, submitted_by_name=str(interaction.user),
            origin_guild_id=interaction.guild_id, origin_channel_id=interaction.channel_id,
        )
        await interaction.followup.send(f"Mining report #{review_id} was sent to the central review queue.", ephemeral=True)
        return
    await add_community_mining_location(bot.cache, entry)
    await bot.log_audit_event(
        "Mining Location Added",
        {
            "User": _audit_user(interaction.user),
            "Channel": _audit_channel(interaction.channel_id),
            "Material": result.material_name,
            "System": system.strip(),
            "Location Type": location_type.name,
            "Location": location.strip(),
        },
        color=discord.Color.green(),
    )
    await interaction.followup.send(
        f"Added `{location.strip()}` to `{result.material_name}` mining locations in `{system.strip()}`.",
        ephemeral=True,
    )


@miningadd_command.autocomplete("material")
async def miningadd_material_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    return await mining_material_autocomplete(interaction, current)


@miningadd_command.autocomplete("system")
async def miningadd_system_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    return await commodity_system_autocomplete(interaction, current)


class MiningLocationView(discord.ui.View):
    def __init__(
        self,
        result: MiningLocationResult,
        system: str | None = None,
        planet: str | None = None,
        page: int = 1,
    ) -> None:
        super().__init__(timeout=300)
        self.result = result
        self.system = system
        self.planet = planet
        self.page = page
        if not system and _mining_location_page_count(result) > 1:
            self.add_item(MiningSystemSelect(result, planet))
        self._sync_buttons()

    def _sync_buttons(self) -> None:
        page_count = _mining_location_page_count(self.result)
        self.previous_page.disabled = self.page <= 1
        self.next_page.disabled = self.page >= page_count

    @discord.ui.button(label="Previous Page", style=discord.ButtonStyle.secondary, row=0)
    async def previous_page(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        self.page = max(1, self.page - 1)
        self._sync_buttons()
        await interaction.response.edit_message(
            embed=build_mining_embed(self.result, self.system, self.planet, page=self.page),
            view=self,
        )

    @discord.ui.button(label="Next Page", style=discord.ButtonStyle.secondary, row=0)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        self.page = min(_mining_location_page_count(self.result), self.page + 1)
        self._sync_buttons()
        await interaction.response.edit_message(
            embed=build_mining_embed(self.result, self.system, self.planet, page=self.page),
            view=self,
        )


class MiningSystemSelect(discord.ui.Select):
    def __init__(self, result: MiningLocationResult, planet: str | None = None) -> None:
        options = [
            discord.SelectOption(label=group.system, value=group.system)
            for group in result.location_groups or []
            if _mining_system_group_has_locations(group)
        ][:25]
        super().__init__(
            placeholder="Filter by system",
            min_values=1,
            max_values=1,
            options=options,
            row=1,
        )
        self.result = result
        self.planet = planet

    async def callback(self, interaction: discord.Interaction) -> None:
        system = self.values[0]
        result = _mining_result_for_system(self.result, system)
        view = MiningLocationView(result, system, self.planet) if _mining_location_page_count(result) > 1 else None
        await interaction.response.edit_message(
            embed=build_mining_embed(result, system, self.planet),
            view=view,
        )


@app_commands.command(name="blueprint", description="Search Star Citizen crafting blueprints.")
@app_commands.describe(
    name="Blueprint or item name to search.",
    category="Optional blueprint category.",
    material="Optional required material or resource.",
    mission_type="Optional mission type that can award the blueprint.",
    contractor="Optional mission contractor.",
    qualities="Material qualities, e.g. Titanium=750, Gold=800. One number applies to all.",
)
async def blueprint_command(
    interaction: discord.Interaction,
    name: str | None = None,
    category: str | None = None,
    material: str | None = None,
    mission_type: str | None = None,
    contractor: str | None = None,
    qualities: str | None = None,
) -> None:
    bot = interaction.client
    if not isinstance(bot, GameAssistBot):
        await interaction.response.send_message("Bot is not fully initialized.", ephemeral=True)
        return

    if not any([name, category, material, mission_type, contractor]):
        await interaction.response.send_message("Add a blueprint name or at least one filter.", ephemeral=True)
        return

    try:
        quality_values = _parse_blueprint_qualities(qualities)
    except ValueError as error:
        await interaction.response.send_message(str(error), ephemeral=True)
        return

    await interaction.response.defer(thinking=True, ephemeral=True)
    try:
        lookup_name = name
        lookup_material = material
        if name and not any([category, material, mission_type, contractor]):
            exact_blueprint_name = await _exact_blueprint_name_match(bot, name)
            exact_material = await _exact_blueprint_filter_match(bot, "resource", name)
            if exact_material and not exact_blueprint_name:
                lookup_name = None
                lookup_material = exact_material

        result_limit = 3 if lookup_name else 25
        results = await bot.sources.lookup_blueprints(
            query=lookup_name,
            category=category,
            material=lookup_material,
            mission_type=mission_type,
            contractor=contractor,
            limit=result_limit,
        )

        if not results:
            await interaction.followup.send("No blueprints found for those filters.", ephemeral=True)
            return

        if not lookup_name:
            has_next = bool(
                await bot.sources.lookup_blueprints(
                    query=None,
                    category=category,
                    material=lookup_material,
                    mission_type=mission_type,
                    contractor=contractor,
                    limit=BLUEPRINT_PAGE_SIZE,
                    page=2,
                )
            )
            await interaction.followup.send(
                embed=build_blueprint_selection_embed(
                    results,
                    category=category,
                    material=lookup_material,
                    mission_type=mission_type,
                    contractor=contractor,
                    page=1,
                    has_next=has_next,
                ),
                view=BlueprintSelectView(
                    results,
                    category=category,
                    material=lookup_material,
                    mission_type=mission_type,
                    contractor=contractor,
                    page=1,
                    has_next=has_next,
                    quality_values=quality_values,
                ),
                ephemeral=True,
            )
            return

        if len(results) == 1:
            result = results[0]
            has_next = _blueprint_mission_page_count(result.missions) > 1
            kwargs = {
                "embed": build_blueprint_embed(
                    result,
                    lookup_name,
                    category,
                    lookup_material,
                    mission_type,
                    contractor,
                    mission_page=1,
                    quality_values=quality_values,
                ),
                "ephemeral": True,
            }
            if has_next:
                kwargs["view"] = BlueprintDetailView(
                    result,
                    lookup_name,
                    category,
                    lookup_material,
                    mission_type,
                    contractor,
                    page=1,
                    quality_values=quality_values,
                )
            await interaction.followup.send(**kwargs)
            return

        await interaction.followup.send(
            embeds=[
                build_blueprint_embed(result, lookup_name, category, lookup_material, mission_type, contractor, mission_page=1, quality_values=quality_values)
                for result in results
            ],
            ephemeral=True,
        )
    except Exception:
        logging.exception("Blueprint command failed")
        await interaction.followup.send(
            "Blueprint lookup hit an internal error. I logged the details so it can be fixed.",
            ephemeral=True,
        )


@blueprint_command.autocomplete("name")
async def blueprint_name_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    bot = interaction.client
    if not isinstance(bot, GameAssistBot):
        return []

    names = await bot.sources.autocomplete_blueprints(current)
    return [app_commands.Choice(name=name[:100], value=name[:100]) for name in names[:25]]


@app_commands.command(name="myblueprints", description="Look up blueprints saved by your website scanner.")
@app_commands.describe(name="Optional blueprint name or category to filter your collection.")
async def my_blueprints_command(
    interaction: discord.Interaction,
    name: str | None = None,
) -> None:
    bot = interaction.client
    if not isinstance(bot, GameAssistBot):
        await interaction.response.send_message("Bot is not fully initialized.", ephemeral=True)
        return

    blueprints = await bot.cache.user_blueprints(interaction.user.id)
    query = (name or "").strip().casefold()
    if query:
        blueprints = [
            item for item in blueprints
            if query in str(item.get("name") or "").casefold()
            or query in str(item.get("category") or "").casefold()
        ]

    if not blueprints:
        message = "No saved blueprints matched that search." if query else (
            "You have no saved blueprints yet. Sign in to the website with Discord and use the Blueprint Scanner to add some."
        )
        await interaction.response.send_message(message, ephemeral=True)
        return

    shown = blueprints[:40]
    lines = [
        f"**{discord.utils.escape_markdown(str(item['name']))}**"
        + (f" — {discord.utils.escape_markdown(str(item['category']))}" if item.get("category") else "")
        for item in shown
    ]
    description = "\n".join(lines)
    if len(blueprints) > len(shown):
        description += f"\n\n…and {len(blueprints) - len(shown)} more. Use `name` to narrow the list."
    embed = discord.Embed(
        title=f"My Blueprints ({len(blueprints)})",
        description=description[:4096],
        color=discord.Color.blurple(),
    )
    embed.set_footer(text="Synced with blueprints saved through the website scanner")
    await interaction.response.send_message(embed=embed, ephemeral=True)


@app_commands.command(name="wikelo", description="Search or browse Wikelo contracts and requirements.")
@app_commands.describe(
    item="Optional reward, contract, or turn-in item.",
    show_all="Browse every Wikelo contract in pages.",
)
async def wikelo_command(
    interaction: discord.Interaction, item: str | None = None, show_all: bool = False,
) -> None:
    bot = interaction.client
    if not isinstance(bot, GameAssistBot):
        await interaction.response.send_message("Bot is not fully initialized.", ephemeral=True)
        return
    await interaction.response.defer(thinking=True, ephemeral=True)
    try:
        browse_all = show_all or not item or not item.strip()
        results = await bot.sources.lookup_wikelo(item, limit=25 if browse_all else 10, page=1)
        if not results:
            await interaction.followup.send("No Wikelo missions found for that item.", ephemeral=True)
            return
        if browse_all:
            next_results = await bot.sources.lookup_wikelo(None, limit=25, page=2)
            await interaction.followup.send(
                embed=build_wikelo_browse_embed(results, page=1, has_next=bool(next_results)),
                view=WikeloBrowseView(bot.sources, results, page=1, has_next=bool(next_results)),
                ephemeral=True,
            )
            return
        await interaction.followup.send(
            embeds=[build_wikelo_embed(result) for result in results], ephemeral=True,
        )
    except Exception:
        logging.exception("Wikelo command failed")
        await interaction.followup.send("Wikelo lookup hit an internal error.", ephemeral=True)


@wikelo_command.autocomplete("item")
async def wikelo_autocomplete(
    interaction: discord.Interaction, current: str,
) -> list[app_commands.Choice[str]]:
    bot = interaction.client
    if not isinstance(bot, GameAssistBot):
        return []
    values = await bot.sources.autocomplete_wikelo(current)
    return [app_commands.Choice(name=value[:100], value=value[:100]) for value in values[:25]]


class WikeloContractSelect(discord.ui.Select):
    def __init__(self, results: list[WikeloMissionResult]) -> None:
        self.results = results[:25]
        options = [
            discord.SelectOption(
                label=result.name[:100],
                description=_wikelo_reward_summary(result)[:100],
                value=str(index),
            )
            for index, result in enumerate(self.results)
        ]
        super().__init__(placeholder="Select a contract to view its requirements", options=options)

    async def callback(self, interaction: discord.Interaction) -> None:
        result = self.results[int(self.values[0])]
        await interaction.response.edit_message(embed=build_wikelo_embed(result), view=None)


class WikeloBrowseView(discord.ui.View):
    def __init__(
        self,
        sources,
        results: list[WikeloMissionResult],
        page: int,
        has_next: bool,
    ) -> None:
        super().__init__(timeout=300)
        self.sources = sources
        self.page = page
        self.has_next = has_next
        self.add_item(WikeloContractSelect(results))
        self.previous_page.disabled = page <= 1
        self.next_page.disabled = not has_next

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.secondary, row=1)
    async def previous_page(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        await self._show_page(interaction, self.page - 1)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary, row=1)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        await self._show_page(interaction, self.page + 1)

    async def _show_page(self, interaction: discord.Interaction, page: int) -> None:
        await interaction.response.defer()
        results = await self.sources.lookup_wikelo(None, limit=25, page=page)
        has_next = bool(await self.sources.lookup_wikelo(None, limit=25, page=page + 1))
        await interaction.edit_original_response(
            embed=build_wikelo_browse_embed(results, page=page, has_next=has_next),
            view=WikeloBrowseView(self.sources, results, page=page, has_next=has_next),
        )


@app_commands.command(name="mission", description="Search Star Citizen missions and blueprint rewards.")
@app_commands.describe(
    name="Mission name.",
    region="Region or star system.",
    rep_giver="Contractor or reputation giver.",
    rep_level="Required reputation level.",
    mission_type="Mission category or type.",
)
async def mission_command(
    interaction: discord.Interaction,
    name: str | None = None,
    region: str | None = None,
    rep_giver: str | None = None,
    rep_level: str | None = None,
    mission_type: str | None = None,
) -> None:
    bot = interaction.client
    if not isinstance(bot, GameAssistBot):
        await interaction.response.send_message("Bot is not fully initialized.", ephemeral=True)
        return
    if not any([name, region, rep_giver, rep_level, mission_type]):
        await interaction.response.send_message("Add a mission name or at least one filter.", ephemeral=True)
        return
    await interaction.response.defer(thinking=True, ephemeral=True)
    try:
        results = await bot.sources.lookup_missions(
            query=name, region=region, contractor=rep_giver,
            reputation_level=rep_level, mission_type=mission_type, limit=10,
        )
        if not results:
            await interaction.followup.send("No missions found for those filters.", ephemeral=True)
            return
        filters = {
            "query": name,
            "region": region,
            "contractor": rep_giver,
            "reputation_level": rep_level,
            "mission_type": mission_type,
        }
        next_results = await bot.sources.lookup_missions(**filters, limit=10, page=2)
        await interaction.followup.send(
            embeds=[build_mission_embed(result) for result in results],
            view=MissionResultsView(bot.sources, filters, page=1, has_next=bool(next_results)),
            ephemeral=True,
        )
    except Exception:
        logging.exception("Mission command failed")
        await interaction.followup.send(
            "Mission lookup hit an internal error. I logged the details so it can be fixed.",
            ephemeral=True,
        )


class MissionResultsView(discord.ui.View):
    def __init__(self, sources, filters: dict[str, str | None], page: int, has_next: bool) -> None:
        super().__init__(timeout=300)
        self.sources = sources
        self.filters = filters
        self.page = page
        self.has_next = has_next
        self.previous_page.disabled = page <= 1
        self.next_page.disabled = not has_next

    @discord.ui.button(label="Previous Page", style=discord.ButtonStyle.secondary)
    async def previous_page(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        await self._show_page(interaction, self.page - 1)

    @discord.ui.button(label="Next Page", style=discord.ButtonStyle.secondary)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        await self._show_page(interaction, self.page + 1)

    async def _show_page(self, interaction: discord.Interaction, page: int) -> None:
        page = max(1, page)
        results = await self.sources.lookup_missions(
            **self.filters,
            limit=10,
            page=page,
        )
        if not results:
            await interaction.response.send_message("No more missions found.", ephemeral=True)
            return
        next_results = await self.sources.lookup_missions(**self.filters, limit=10, page=page + 1)
        await interaction.response.edit_message(
            embeds=[build_mission_embed(result) for result in results],
            view=MissionResultsView(self.sources, self.filters, page, bool(next_results)),
        )


@mission_command.autocomplete("name")
@mission_command.autocomplete("region")
@mission_command.autocomplete("rep_giver")
@mission_command.autocomplete("rep_level")
@mission_command.autocomplete("mission_type")
async def mission_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    bot = interaction.client
    if not isinstance(bot, GameAssistBot):
        return []
    field_map = {
        "name": "name",
        "region": "region",
        "rep_giver": "contractor",
        "rep_level": "reputation_level",
        "mission_type": "mission_type",
    }
    focused = interaction.namespace
    parameter = next(
        (option.get("name") for option in interaction.data.get("options", []) if option.get("focused")),
        "name",
    )
    del focused
    values = await bot.sources.autocomplete_missions(field_map.get(parameter, "name"), current)
    return [app_commands.Choice(name=value[:100], value=value[:100]) for value in values[:25]]


@blueprint_command.autocomplete("category")
@blueprint_command.autocomplete("material")
@blueprint_command.autocomplete("mission_type")
@blueprint_command.autocomplete("contractor")
async def blueprint_filter_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    bot = interaction.client
    if not isinstance(bot, GameAssistBot):
        return []

    field_map = {
        "category": "category",
        "material": "resource",
        "mission_type": "mission_type",
        "contractor": "contractor",
    }
    names = await bot.sources.autocomplete_blueprint_filter(
        field_map.get(_focused_option_name(interaction), "category"),
        current,
    )
    return [app_commands.Choice(name=name[:100], value=name[:100]) for name in names[:25]]


async def _exact_blueprint_name_match(bot: GameAssistBot, query: str) -> str | None:
    names = await bot.sources.autocomplete_blueprints(query)
    return _exact_choice_match(query, names)


async def _exact_blueprint_filter_match(bot: GameAssistBot, filter_name: str, query: str) -> str | None:
    names = await bot.sources.autocomplete_blueprint_filter(filter_name, query)
    return _exact_choice_match(query, names)


def _exact_choice_match(query: str, choices: list[str]) -> str | None:
    normalized_query = _normalize_choice(query)
    for choice in choices:
        if _normalize_choice(choice) == normalized_query:
            return choice
    return None


def _normalize_choice(value: str) -> str:
    return " ".join(value.lower().replace("-", " ").split())


class BlueprintSelect(discord.ui.Select):
    def __init__(self, results: list[BlueprintResult], quality_values: dict[str, float] | None = None) -> None:
        self.results = results[:25]
        self.quality_values = quality_values or {}
        options = [
            discord.SelectOption(
                label=result.name[:100],
                description=_blueprint_result_label(result)[:100],
                value=str(index),
            )
            for index, result in enumerate(self.results)
        ]
        super().__init__(
            placeholder="Select a blueprint for full details",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        result = self.results[int(self.values[0])]
        has_next = _blueprint_mission_page_count(result.missions) > 1
        kwargs = {"embed": build_blueprint_embed(result, mission_page=1, quality_values=self.quality_values)}
        if has_next:
            kwargs["view"] = BlueprintDetailView(result, page=1, quality_values=self.quality_values)
        else:
            kwargs["view"] = None
        await interaction.response.edit_message(**kwargs)


class BlueprintDetailView(discord.ui.View):
    def __init__(
        self,
        result: BlueprintResult,
        name: str | None = None,
        category: str | None = None,
        material: str | None = None,
        mission_type: str | None = None,
        contractor: str | None = None,
        page: int = 1,
        quality_values: dict[str, float] | None = None,
    ) -> None:
        super().__init__(timeout=300)
        self.result = result
        self.name = name
        self.category = category
        self.material = material
        self.mission_type = mission_type
        self.contractor = contractor
        self.page = page
        self.quality_values = quality_values or {}
        self.page_count = _blueprint_mission_page_count(result.missions)
        self.previous_page.disabled = self.page_count <= 1
        self.next_page.disabled = self.page_count <= 1

    @discord.ui.button(label="Previous Page", style=discord.ButtonStyle.secondary, row=0)
    async def previous_page(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        await self._show_page(interaction, self.page - 1)

    @discord.ui.button(label="Next Page", style=discord.ButtonStyle.secondary, row=0)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        await self._show_page(interaction, self.page + 1)

    async def _show_page(self, interaction: discord.Interaction, page: int) -> None:
        if page < 1:
            page = self.page_count
        elif page > self.page_count:
            page = 1
        await interaction.response.edit_message(
            embed=build_blueprint_embed(
                self.result,
                self.name,
                self.category,
                self.material,
                self.mission_type,
                self.contractor,
                mission_page=page,
                quality_values=self.quality_values,
            ),
            view=BlueprintDetailView(
                self.result,
                self.name,
                self.category,
                self.material,
                self.mission_type,
                self.contractor,
                page=page,
                quality_values=self.quality_values,
            ),
        )


class BlueprintSelectView(discord.ui.View):
    def __init__(
        self,
        results: list[BlueprintResult],
        category: str | None = None,
        material: str | None = None,
        mission_type: str | None = None,
        contractor: str | None = None,
        page: int = 1,
        has_next: bool = False,
        quality_values: dict[str, float] | None = None,
    ) -> None:
        super().__init__(timeout=300)
        self.category = category
        self.material = material
        self.mission_type = mission_type
        self.contractor = contractor
        self.page = page
        self.has_next = has_next
        self.quality_values = quality_values or {}
        self.add_item(BlueprintSelect(results, self.quality_values))
        self.previous_page.disabled = page <= 1
        self.next_page.disabled = not has_next

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.secondary, row=1)
    async def previous_page(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        await self._show_page(interaction, self.page - 1)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary, row=1)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        await self._show_page(interaction, self.page + 1)

    async def _show_page(self, interaction: discord.Interaction, page: int) -> None:
        bot = interaction.client
        if not isinstance(bot, GameAssistBot):
            await interaction.response.send_message("Bot is not fully initialized.", ephemeral=True)
            return

        await interaction.response.defer()
        results = await bot.sources.lookup_blueprints(
            query=None,
            category=self.category,
            material=self.material,
            mission_type=self.mission_type,
            contractor=self.contractor,
            limit=BLUEPRINT_PAGE_SIZE,
            page=page,
        )
        has_next = bool(
            await bot.sources.lookup_blueprints(
                query=None,
                category=self.category,
                material=self.material,
                mission_type=self.mission_type,
                contractor=self.contractor,
                limit=BLUEPRINT_PAGE_SIZE,
                page=page + 1,
            )
        )
        await interaction.edit_original_response(
            embed=build_blueprint_selection_embed(
                results,
                category=self.category,
                material=self.material,
                mission_type=self.mission_type,
                contractor=self.contractor,
                page=page,
                has_next=has_next,
            ),
            view=BlueprintSelectView(
                results,
                category=self.category,
                material=self.material,
                mission_type=self.mission_type,
                contractor=self.contractor,
                page=page,
                has_next=has_next,
                quality_values=self.quality_values,
            ),
        )


item_group = app_commands.Group(name="item", description="Item lookup tools.")

loot_group = app_commands.Group(name="loot", description="Lootable item lookup tools.")


class LootSightingReviewView(discord.ui.View):
    def __init__(self, report_id: int) -> None:
        super().__init__(timeout=None)
        self.report_id = report_id
        approve = discord.ui.Button(
            label="Approve", style=discord.ButtonStyle.success,
            custom_id=f"loot-report:approve:{report_id}",
        )
        reject = discord.ui.Button(
            label="Reject", style=discord.ButtonStyle.danger,
            custom_id=f"loot-report:reject:{report_id}",
        )
        approve.callback = self._approve
        reject.callback = self._reject
        self.add_item(approve)
        self.add_item(reject)

    async def _approve(self, interaction: discord.Interaction) -> None:
        bot = interaction.client
        if isinstance(bot, GameAssistBot):
            await bot.review_loot_sighting(interaction, self.report_id, True)

    async def _reject(self, interaction: discord.Interaction) -> None:
        bot = interaction.client
        if isinstance(bot, GameAssistBot):
            await bot.review_loot_sighting(interaction, self.report_id, False)


@loot_group.command(name="search", description="Find a lootable Star Citizen item and its UEX value.")
@app_commands.describe(name="Lootable item name, such as ADP-mk4 Arms Justified.")
async def loot_search_command(interaction: discord.Interaction, name: str) -> None:
    bot = interaction.client
    if not isinstance(bot, GameAssistBot):
        await interaction.response.send_message("Bot is not fully initialized.", ephemeral=True)
        return
    await interaction.response.defer(thinking=True, ephemeral=True)
    result = await bot.sources.lookup_loot_item(name)
    if result is None:
        await interaction.followup.send(
            f"No lootable item matching `{name}` was found in the current Star Citizen Wiki catalog.",
            ephemeral=True,
        )
        return
    sightings = await bot.cache.loot_location_evidence(result.name)
    await interaction.followup.send(embed=build_loot_item_embed(result, sightings), ephemeral=True)


@loot_search_command.autocomplete("name")
async def loot_search_autocomplete(
    interaction: discord.Interaction, current: str,
) -> list[app_commands.Choice[str]]:
    bot = interaction.client
    if not isinstance(bot, GameAssistBot):
        return []
    names = await bot.sources.autocomplete_loot_items(current)
    return [app_commands.Choice(name=name[:100], value=name[:100]) for name in names[:25]]


@loot_group.command(name="found", description="Quickly report where you found a lootable item.")
@app_commands.describe(
    name="Lootable item name.",
    location="Named point of interest or facility.",
    celestial_body="Planet or moon, such as Daymar.",
    screenshot="Optional screenshot showing the item or container.",
)
async def loot_found_command(
    interaction: discord.Interaction,
    name: str,
    location: str,
    celestial_body: str | None = None,
    screenshot: discord.Attachment | None = None,
) -> None:
    bot = interaction.client
    if not isinstance(bot, GameAssistBot):
        await interaction.response.send_message("Bot is not fully initialized.", ephemeral=True)
        return
    if screenshot and not _is_image_attachment(screenshot):
        await interaction.response.send_message("Evidence must be a PNG, JPG, WEBP, or GIF image.", ephemeral=True)
        return
    await interaction.response.defer(thinking=True, ephemeral=True)
    item = await bot.sources.lookup_loot_item(name)
    if item is None:
        await interaction.followup.send(f"No lootable item matching `{name}` was found.", ephemeral=True)
        return
    report_id = await bot.cache.add_loot_sighting_report(
        item_uuid=item.uuid,
        item_name=item.name,
        location=location.strip()[:300],
        celestial_body=(celestial_body or "").strip()[:100] or None,
        location_type=None,
        game_version=item.game_version,
        notes=None,
        screenshot_url=screenshot.url if screenshot else None,
        reporter_id=interaction.user.id,
        reporter_name=str(interaction.user),
        guild_id=interaction.guild_id,
        channel_id=interaction.channel_id,
    )
    report = await bot.cache.loot_sighting_report(report_id)
    await bot.publish_loot_review(report or {})
    await interaction.followup.send(
        f"Thanks — sighting **#{report_id}** is waiting for Bot Manager review.", ephemeral=True
    )


@loot_found_command.autocomplete("name")
async def loot_found_autocomplete(
    interaction: discord.Interaction, current: str,
) -> list[app_commands.Choice[str]]:
    return await loot_search_autocomplete(interaction, current)


@loot_group.command(name="report", description="Report where you found a lootable item.")
@app_commands.describe(
    name="Lootable item name.",
    location="Where you found it, including the facility, mission, or point of interest.",
    game_version="Optional patch observed, such as 4.9.0; defaults to the item's current catalog version.",
    location_type="Optional category, such as bunker, settlement, mission, or asteroid base.",
    screenshot="Optional screenshot showing the item or loot container.",
    notes="Optional directions, container details, mission name, or other verification context.",
)
async def loot_report_command(
    interaction: discord.Interaction,
    name: str,
    location: str,
    game_version: str | None = None,
    location_type: str | None = None,
    screenshot: discord.Attachment | None = None,
    notes: str | None = None,
) -> None:
    bot = interaction.client
    if not isinstance(bot, GameAssistBot):
        await interaction.response.send_message("Bot is not fully initialized.", ephemeral=True)
        return
    if screenshot and not _is_image_attachment(screenshot):
        await interaction.response.send_message(
            "The optional evidence attachment must be a PNG, JPG, WEBP, or GIF image.", ephemeral=True
        )
        return
    await interaction.response.defer(thinking=True, ephemeral=True)
    item = await bot.sources.lookup_loot_item(name)
    if item is None:
        await interaction.followup.send(
            f"No lootable item matching `{name}` was found. Choose an item from autocomplete and try again.",
            ephemeral=True,
        )
        return
    report_id = await bot.cache.add_loot_sighting_report(
        item_uuid=item.uuid,
        item_name=item.name,
        location=location.strip()[:300],
        location_type=(location_type or "").strip()[:100] or None,
        game_version=(game_version or item.game_version or "").strip()[:50] or None,
        notes=(notes or "").strip()[:1000] or None,
        screenshot_url=screenshot.url if screenshot else None,
        reporter_id=interaction.user.id,
        reporter_name=str(interaction.user),
        guild_id=interaction.guild_id,
        channel_id=interaction.channel_id,
    )
    report = await bot.cache.loot_sighting_report(report_id)
    try:
        await bot.publish_loot_review(report or {})
    except (RuntimeError, discord.HTTPException):
        logging.exception("Could not publish loot report %s to the review queue", report_id)
        await interaction.followup.send(
            f"Report **#{report_id}** was saved, but the private review post could not be published yet. "
            "It will be restored when the bot reconnects.",
            ephemeral=True,
        )
        return
    await interaction.followup.send(
        f"Loot sighting **#{report_id}** was submitted privately for Bot Manager review.", ephemeral=True
    )


@loot_report_command.autocomplete("name")
async def loot_report_autocomplete(
    interaction: discord.Interaction, current: str,
) -> list[app_commands.Choice[str]]:
    return await loot_search_autocomplete(interaction, current)


@item_group.command(name="search", description="Search for in-game buyable Star Citizen items.")
@app_commands.describe(
    name="Item name to search.",
    category="Optional category. Start typing to search all choices, including those beyond Discord's first 25.",
    section="Optional item section, such as Systems, Vehicle Weapons, Armor, or Utility.",
    size="Optional item size.",
    location="Optional station, city, outpost, or point of interest.",
)
async def item_search_command(
    interaction: discord.Interaction,
    name: str | None = None,
    category: str | None = None,
    section: str | None = None,
    size: str | None = None,
    location: str | None = None,
) -> None:
    bot = interaction.client
    if not isinstance(bot, GameAssistBot):
        await interaction.response.send_message("Bot is not fully initialized.", ephemeral=True)
        return

    if not any([name, category, section, size, location]):
        await interaction.response.send_message("Add an item name or at least one filter.", ephemeral=True)
        return

    await interaction.response.defer(thinking=True, ephemeral=True)
    results = await bot.sources.lookup_items(
        query=name,
        category=category,
        section=section,
        size=size,
        location=location,
        limit=BLUEPRINT_PAGE_SIZE,
        page=1,
    )
    if not results:
        await interaction.followup.send("No in-game buyable items found for those filters.", ephemeral=True)
        return

    if name and len(results) == 1:
        detail = await bot.sources.lookup_item_by_id(results[0].id, location)
        await interaction.followup.send(
            embed=build_item_locator_embed(detail or results[0], name, category, section, size, location),
            ephemeral=True,
        )
        return

    has_next = bool(
        await bot.sources.lookup_items(
            query=name,
            category=category,
            section=section,
            size=size,
            location=location,
            limit=BLUEPRINT_PAGE_SIZE,
            page=2,
        )
    )
    await interaction.followup.send(
        embed=build_item_locator_selection_embed(results, name, category, section, size, location, page=1, has_next=has_next),
        view=ItemLocatorSelectView(results, name, category, section, size, location, page=1, has_next=has_next),
        ephemeral=True,
    )


@item_search_command.autocomplete("name")
async def item_locator_name_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    bot = interaction.client
    if not isinstance(bot, GameAssistBot):
        return []
    names = await bot.sources.autocomplete_items(current)
    return [app_commands.Choice(name=name[:100], value=name[:100]) for name in names[:25]]


@item_search_command.autocomplete("category")
@item_search_command.autocomplete("section")
@item_search_command.autocomplete("size")
@item_search_command.autocomplete("location")
async def item_locator_filter_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    bot = interaction.client
    if not isinstance(bot, GameAssistBot):
        return []
    names = await bot.sources.autocomplete_item_filter(_focused_option_name(interaction), current)
    return [app_commands.Choice(name=name[:100], value=name[:100]) for name in names[:25]]


class ItemLocatorSelect(discord.ui.Select):
    def __init__(self, results: list[ItemLocatorResult], location: str | None = None) -> None:
        self.location = location
        self.results = results[:25]
        options = [
            discord.SelectOption(
                label=result.name[:100],
                description=_item_locator_result_label(result)[:100],
                value=str(result.id),
            )
            for result in self.results
        ]
        super().__init__(
            placeholder="Select an item for buy locations",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        bot = interaction.client
        if not isinstance(bot, GameAssistBot):
            await interaction.response.send_message("Bot is not fully initialized.", ephemeral=True)
            return

        await interaction.response.defer()
        result = await bot.sources.lookup_item_by_id(int(self.values[0]), self.location)
        if result is None:
            await interaction.edit_original_response(content="That item is no longer available in UEX.", embed=None, view=None)
            return
        await interaction.edit_original_response(embed=build_item_locator_embed(result), view=None)


class ItemLocatorSelectView(discord.ui.View):
    def __init__(
        self,
        results: list[ItemLocatorResult],
        name: str | None = None,
        category: str | None = None,
        section: str | None = None,
        size: str | None = None,
        location: str | None = None,
        page: int = 1,
        has_next: bool = False,
    ) -> None:
        super().__init__(timeout=300)
        self.name = name
        self.category = category
        self.section = section
        self.size = size
        self.location = location
        self.page = page
        self.has_next = has_next
        self.add_item(ItemLocatorSelect(results, location))
        self.previous_page.disabled = page <= 1
        self.next_page.disabled = not has_next

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.secondary, row=1)
    async def previous_page(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        await self._show_page(interaction, self.page - 1)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary, row=1)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        await self._show_page(interaction, self.page + 1)

    async def _show_page(self, interaction: discord.Interaction, page: int) -> None:
        bot = interaction.client
        if not isinstance(bot, GameAssistBot):
            await interaction.response.send_message("Bot is not fully initialized.", ephemeral=True)
            return

        await interaction.response.defer()
        results = await bot.sources.lookup_items(
            query=self.name,
            category=self.category,
            section=self.section,
            size=self.size,
            location=self.location,
            limit=BLUEPRINT_PAGE_SIZE,
            page=page,
        )
        has_next = bool(
            await bot.sources.lookup_items(
                query=self.name,
                category=self.category,
                section=self.section,
                size=self.size,
                location=self.location,
                limit=BLUEPRINT_PAGE_SIZE,
                page=page + 1,
            )
        )
        await interaction.edit_original_response(
            embed=build_item_locator_selection_embed(
                results,
                self.name,
                self.category,
                self.section,
                self.size,
                self.location,
                page=page,
                has_next=has_next,
            ),
            view=ItemLocatorSelectView(
                results,
                self.name,
                self.category,
                self.section,
                self.size,
                self.location,
                page=page,
                has_next=has_next,
            ),
        )


inventory_group = app_commands.Group(name="inventory", description="Search your website inventory.")


@inventory_group.command(name="search", description="Search the inventory saved through the website.")
@app_commands.describe(
    item="Item name or notes to search.",
    station="Station or inventory location.",
    category="Inventory category.",
    item_type="Item type.",
    size="Item size.",
    sort_by="How to order the results.",
)
@app_commands.choices(
    sort_by=[
        app_commands.Choice(name="Item name", value="name"),
        app_commands.Choice(name="Station", value="location"),
        app_commands.Choice(name="Category", value="category"),
        app_commands.Choice(name="Quantity", value="quantity"),
        app_commands.Choice(name="Recently updated", value="updated"),
    ]
)
async def inventory_search_command(
    interaction: discord.Interaction,
    item: str | None = None,
    station: str | None = None,
    category: str | None = None,
    item_type: str | None = None,
    size: str | None = None,
    sort_by: app_commands.Choice[str] | None = None,
) -> None:
    bot = interaction.client
    if not isinstance(bot, GameAssistBot):
        await interaction.response.send_message("Bot is not fully initialized.", ephemeral=True)
        return

    await interaction.response.defer(thinking=True, ephemeral=True)
    results = await bot.cache.user_inventory_items(
        interaction.user.id,
        location=station,
        category=category,
        query=item,
        sort_by=sort_by.value if sort_by else "name",
        item_type=item_type,
        item_size=size,
    )
    if not results:
        await interaction.followup.send(
            "No items in your website inventory matched those filters. Sign into the website with this Discord account to add inventory.",
            ephemeral=True,
        )
        return

    await interaction.followup.send(
        embed=build_inventory_search_embed(results, item, station, category, item_type, size),
        ephemeral=True,
    )


@inventory_search_command.autocomplete("item")
@inventory_search_command.autocomplete("station")
@inventory_search_command.autocomplete("category")
@inventory_search_command.autocomplete("item_type")
@inventory_search_command.autocomplete("size")
async def inventory_search_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    bot = interaction.client
    if not isinstance(bot, GameAssistBot):
        return []

    focused = _focused_option_name(interaction)
    if focused == "item":
        inventory = await bot.cache.user_inventory_items(interaction.user.id)
        values = sorted({str(row["name"]) for row in inventory if row.get("name")}, key=str.casefold)
    else:
        facets = await bot.cache.user_inventory_facets(interaction.user.id)
        values = facets.get(
            {
                "station": "locations",
                "category": "categories",
                "item_type": "item_types",
                "size": "item_sizes",
            }.get(focused, ""),
            [],
        )

    normalized = current.strip().casefold()
    matches = [value for value in values if not normalized or normalized in value.casefold()]
    return [app_commands.Choice(name=value[:100], value=value[:100]) for value in matches[:25]]


trade_group = app_commands.Group(name="trade", description="Trade planning tools.")


def _trade_listing_content(
    user: discord.abc.User,
    listing_type: str,
    price: int,
    quantity: int,
    notes: str | None,
) -> str:
    lines = [
        f"**{listing_type} listing by {user.mention}**",
        "",
        f"**Price:** {price:,} aUEC",
        f"**Quantity:** {quantity:,}",
    ]
    if notes and notes.strip():
        lines.extend(("", f"**Details:** {notes.strip()[:1000]}"))
    lines.extend(("", "Reply in this post if interested."))
    return "\n".join(lines)


@trade_group.command(name="listing", description="Create an item listing with assisted catalog-name selection.")
@app_commands.describe(
    listing_type="Choose whether you want to sell, buy, or trade the item.",
    item="Start typing, then select the exact item name from the catalog.",
    price="Price per item in aUEC.",
    quantity="Number of items included in the listing.",
    notes="Optional condition, availability, or trade details.",
)
@app_commands.choices(
    listing_type=[
        app_commands.Choice(name="WTS — Want to sell", value="WTS"),
        app_commands.Choice(name="WTB — Want to buy", value="WTB"),
        app_commands.Choice(name="WTT — Want to trade", value="WTT"),
    ]
)
async def trade_listing_command(
    interaction: discord.Interaction,
    listing_type: app_commands.Choice[str],
    item: str,
    price: app_commands.Range[int, 1, 2_147_483_647],
    quantity: app_commands.Range[int, 1, 10_000] = 1,
    notes: str | None = None,
) -> None:
    bot = interaction.client
    if not isinstance(bot, GameAssistBot):
        await interaction.response.send_message("Bot is not fully initialized.", ephemeral=True)
        return

    await interaction.response.defer(thinking=True, ephemeral=True)
    catalog_item = await bot.sources.lookup_trade_item(item)
    if catalog_item is None or catalog_item.name.casefold() != item.strip().casefold():
        await interaction.followup.send(
            "That item name was not an exact catalog match. Start typing the name and select one of the suggestions.",
            ephemeral=True,
        )
        return

    forum_id = await bot.marketplace_forum_id(interaction.guild_id)
    forum = bot.get_channel(forum_id or 0)
    if forum is None and forum_id:
        try:
            forum = await bot.fetch_channel(forum_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            forum = None
    if not isinstance(forum, discord.ForumChannel):
        await interaction.followup.send("The trading forum is currently unavailable.", ephemeral=True)
        return

    tag = discord.utils.find(
        lambda candidate: candidate.name.casefold() == listing_type.value.casefold(),
        forum.available_tags,
    )
    if tag is None:
        await interaction.followup.send(
            f"The marketplace forum needs a **{listing_type.value}** tag. Ask a server manager to add it.", ephemeral=True
        )
        return

    listing_content = _trade_listing_content(interaction.user, listing_type.value, price, quantity, notes)
    created = await forum.create_thread(
        name=catalog_item.name[:100],
        content=listing_content,
        embed=build_trading_item_embed(
            catalog_item,
            listing_type.value,
            _trade_seller_terms(listing_content),
        ),
        applied_tags=[tag],
        auto_archive_duration=10080,
        allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
        reason=f"Trading listing created by {interaction.user} ({interaction.user.id})",
    )
    await interaction.followup.send(
        f"Created {created.thread.mention} with the exact catalog name **{catalog_item.name}**.",
        ephemeral=True,
    )


@trade_listing_command.autocomplete("item")
async def trade_listing_item_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    bot = interaction.client
    if not isinstance(bot, GameAssistBot):
        return []
    results = await bot.sources.lookup_inventory_items(current, limit=25)
    return [app_commands.Choice(name=result.name[:100], value=result.name[:100]) for result in results[:25]]


@trade_group.command(name="store", description="Create a store from Google Sheets or an Inventory Scanner workbook.")
@app_commands.describe(
    name="Store name shown in the trading forum.",
    description="Brief description of what the store sells.",
    sheet_url="View-only Google Sheets link (choose this or inventory_file).",
    inventory_file="Inventory Scanner .xlsx export (choose this or sheet_url).",
    location="Optional default in-game meetup location.",
    availability="Optional schedule or fulfillment information.",
)
async def trade_store_command(
    interaction: discord.Interaction,
    name: str,
    description: str,
    sheet_url: str | None = None,
    inventory_file: discord.Attachment | None = None,
    location: str | None = None,
    availability: str | None = None,
) -> None:
    bot = interaction.client
    if not isinstance(bot, GameAssistBot):
        await interaction.response.send_message("Bot is not fully initialized.", ephemeral=True)
        return
    await interaction.response.defer(thinking=True, ephemeral=True)
    if not name.strip() or len(name.strip()) > 100:
        await interaction.followup.send("Store names must be between 1 and 100 characters.", ephemeral=True)
        return
    if not description.strip() or len(description.strip()) > 1500:
        await interaction.followup.send("Store descriptions must be between 1 and 1,500 characters.", ephemeral=True)
        return
    if bool(sheet_url) == bool(inventory_file):
        await interaction.followup.send("Provide exactly one inventory source: a Google Sheets link or an Inventory Scanner .xlsx file.", ephemeral=True)
        return
    upload_data: bytes | None = None
    source_type = "google_sheet"
    try:
        if inventory_file:
            if not inventory_file.filename.casefold().endswith(".xlsx"):
                raise ValueError("Inventory Scanner uploads must be .xlsx files.")
            upload_data = await inventory_file.read()
            inventory = parse_store_inventory_xlsx(upload_data)
            source_type = "inventory_workbook"
            source_url = inventory_file.url
        else:
            source_url = str(sheet_url).strip()
            google_sheet_csv_url(source_url)
            inventory = await bot.fetch_trade_store_inventory(source_url)
    except (ValueError, aiohttp.ClientError, asyncio.TimeoutError) as exc:
        await interaction.followup.send(f"I couldn't import that inventory: {exc}", ephemeral=True)
        return

    forum_id = await bot.marketplace_forum_id(interaction.guild_id)
    forum = bot.get_channel(forum_id or 0)
    if not isinstance(forum, discord.ForumChannel):
        try:
            forum = await bot.fetch_channel(forum_id or 0)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            forum = None
    if not isinstance(forum, discord.ForumChannel):
        await interaction.followup.send("The trading forum is currently unavailable.", ephemeral=True)
        return
    tag = discord.utils.find(lambda item: item.name.casefold() == TRADING_STORE_TAG.casefold(), forum.available_tags)
    if tag is None:
        await interaction.followup.send(
            "The marketplace forum needs a **STORE** tag. Ask a server manager to add it.", ephemeral=True
        )
        return

    now = int(discord.utils.utcnow().timestamp())
    store = {
        "owner_id": interaction.user.id,
        "store_name": name.strip(),
        "description": description.strip(),
        "sheet_url": source_url,
        "source_type": source_type,
        "location": (location or "").strip() or None,
        "availability": (availability or "").strip() or None,
    }
    create_options = dict(
        name=f"STORE · {store['store_name']}"[:100],
        content=build_trade_store_content(store),
        embed=build_trade_store_embed(store, inventory.items, now),
        applied_tags=[tag],
        auto_archive_duration=10080,
        allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
        reason=f"Trading store created by {interaction.user} ({interaction.user.id})",
    )
    if upload_data is not None and inventory_file is not None:
        create_options["file"] = discord.File(io.BytesIO(upload_data), filename=inventory_file.filename[:100])
    created = await forum.create_thread(**create_options)
    if upload_data is not None and created.message.attachments:
        store["sheet_url"] = created.message.attachments[0].url
        await created.message.edit(content=build_trade_store_content(store), embed=build_trade_store_embed(store, inventory.items, now))
    await bot.cache.save_trade_store(
        {
            **store,
            "guild_id": interaction.guild_id,
            "thread_id": created.thread.id,
            "message_id": created.message.id,
            "content_hash": inventory.content_hash,
            "last_synced_at": now,
            "last_error": None,
        }
    )
    await interaction.followup.send(
        f"Created {created.thread.mention} with {len(inventory.items):,} imported item(s). "
        + ("This uploaded workbook is a snapshot; upload a new workbook when the inventory changes." if source_type == "inventory_workbook" else "The Google Sheet will be checked automatically once per day."),
        ephemeral=True,
    )


@trade_group.command(name="store-refresh", description="Refresh one of your Google Sheets store listings now.")
@app_commands.describe(store="Choose one of your active stores.")
async def trade_store_refresh_command(interaction: discord.Interaction, store: str) -> None:
    bot = interaction.client
    if not isinstance(bot, GameAssistBot):
        await interaction.response.send_message("Bot is not fully initialized.", ephemeral=True)
        return
    await interaction.response.defer(thinking=True, ephemeral=True)
    try:
        thread_id = int(store)
    except ValueError:
        await interaction.followup.send("Choose a store from autocomplete.", ephemeral=True)
        return
    record = await bot.cache.trade_store(thread_id)
    if record is None:
        await interaction.followup.send("That active store could not be found.", ephemeral=True)
        return
    if record.get("guild_id") is not None and int(record["guild_id"]) != interaction.guild_id:
        await interaction.followup.send("That store belongs to another Discord server.", ephemeral=True)
        return
    if record.get("guild_id") is None and interaction.guild_id != bot.settings.discord_guild_id:
        await interaction.followup.send("That legacy store belongs to the primary Discord server.", ephemeral=True)
        return
    if int(record["owner_id"]) != interaction.user.id and not _can_manage_admin_commands(interaction, bot.settings):
        await interaction.followup.send("Only the store owner or a Bot Manager can refresh this store.", ephemeral=True)
        return
    success, message = await bot.sync_trade_store(record, force=True)
    await interaction.followup.send(("✅ " if success else "⚠️ ") + message, ephemeral=True)


@trade_store_refresh_command.autocomplete("store")
async def trade_store_refresh_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    bot = interaction.client
    if not isinstance(bot, GameAssistBot):
        return []
    stores = await bot.cache.trade_stores(
        owner_id=interaction.user.id,
        guild_id=interaction.guild_id,
        include_legacy=interaction.guild_id == bot.settings.discord_guild_id,
    )
    normalized = current.strip().casefold()
    matches = [store for store in stores if not normalized or normalized in str(store["store_name"]).casefold()]
    return [
        app_commands.Choice(name=str(store["store_name"])[:100], value=str(store["thread_id"]))
        for store in matches[:25]
    ]


@trade_group.command(name="routing", description="Find Star Citizen trade route candidates from UEX.")
@app_commands.describe(
    starting_point="Required starting trade terminal for the circular route.",
    ship="Ship for route planning.",
    investment="aUEC investment for route planning.",
    max_stops="Maximum route stops, from 2 to 5.",
    stay_system="Optional star system to keep the full loop inside.",
)
async def trade_routing_command(
    interaction: discord.Interaction,
    starting_point: str,
    ship: str = "Ironclad Assault",
    investment: int = 1_000_000,
    max_stops: int = 5,
    stay_system: str | None = None,
) -> None:
    bot = interaction.client
    if not isinstance(bot, GameAssistBot):
        await interaction.response.send_message("Bot is not fully initialized.", ephemeral=True)
        return

    if investment <= 0:
        await interaction.response.send_message("Investment must be greater than 0 aUEC.", ephemeral=True)
        return
    if max_stops < 2 or max_stops > 5:
        await interaction.response.send_message("Circular routes need max stops between 2 and 5.", ephemeral=True)
        return

    await interaction.response.defer(thinking=True, ephemeral=True)
    ship_result = await bot.sources.lookup_ship(ship)
    if ship_result is None:
        await interaction.followup.send(f"No ship or vehicle found for `{ship}`.", ephemeral=True)
        return
    if ship_result.cargo_capacity is None or ship_result.cargo_capacity <= 0:
        await interaction.followup.send(f"`{ship_result.name}` does not have a usable cargo capacity for trade routing.", ephemeral=True)
        return

    result = await bot.sources.lookup_trade_routes(
        ship_result.name,
        ship_result.cargo_capacity,
        starting_point,
        investment,
        max_stops,
        stay_system,
        True,
    )
    if result is None or not result.legs:
        await interaction.followup.send(
            "No profitable UEX circular route found from that starting point right now.",
            ephemeral=True,
        )
        return

    embed = build_trade_route_embed(result, starting_point, max_stops, stay_system)
    await interaction.followup.send(embed=embed, ephemeral=True)


@trade_routing_command.autocomplete("starting_point")
async def trade_starting_point_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    bot = interaction.client
    if not isinstance(bot, GameAssistBot):
        return []

    locations = await bot.sources.autocomplete_trade_locations(current)
    return [
        app_commands.Choice(name=location[:100], value=location[:100])
        for location in locations[:25]
    ]


@trade_routing_command.autocomplete("ship")
async def trade_ship_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    bot = interaction.client
    if not isinstance(bot, GameAssistBot):
        return []

    names = await bot.sources.autocomplete_ships(current)
    return [
        app_commands.Choice(name=name[:100], value=name[:100])
        for name in names[:25]
    ]


@trade_routing_command.autocomplete("stay_system")
async def trade_system_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    return await commodity_system_autocomplete(interaction, current)


admin_group = app_commands.Group(name="admin", description="Bot management commands.")


def build_native_admin_embed(guild: discord.Guild, modules: dict[str, dict[str, object]], pending: int = 0) -> discord.Embed:
    embed = discord.Embed(
        title=f"SC Companion Admin — {guild.name}",
        description="Enable features below. Channel assignments can be changed with `/admin channel` or on sccompanion.org.",
        color=discord.Color.blurple(),
    )
    for key, definition in BOT_MODULES.items():
        item = modules[key]
        destination = f" <#{item['channel_id']}>" if item.get("channel_id") else " any channel"
        commands = " ".join(f"`/{command}`" for command in definition["commands"])
        embed.add_field(name=str(definition["label"]), value=("Enabled —" if item["enabled"] else "Disabled —") + destination + f"\n{commands}", inline=True)
    embed.add_field(name="Central reviews", value=f"{pending} pending", inline=False)
    embed.set_footer(text="Changes are isolated to this Discord server. Global approved game knowledge remains shared.")
    return embed


def build_bot_setup_guide_embed() -> discord.Embed:
    embed = discord.Embed(
        title="Set Up SC Companion",
        description="You need the **Manage Server** permission. Follow these steps in the server where you want to use the bot.",
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="1. Open the panel",
        value="Type `/admin panel`. Only server managers can see and change these settings.",
        inline=False,
    )
    embed.add_field(
        name="2. Choose channel setup",
        value="Choose **Let the bot set up channels** or **I will set up channels**. The bot asks this before you pick features.",
        inline=False,
    )
    embed.add_field(
        name="3. Pick the features",
        value="Open **Choose enabled features**. Check each feature you want. Remove the check from a feature you do not want. Then save your choice.",
        inline=False,
    )
    embed.add_field(
        name="4. Finish and test",
        value="For manual setup, select **Next: Assign Channels** and choose a channel for each feature. Then type `/admin health` and try one enabled command.",
        inline=False,
    )
    embed.add_field(
        name="What the bot makes",
        value="The bot makes an **about-the-bot** page and a **feedback-and-issues** forum. It also adds command examples to the channels you choose.",
        inline=False,
    )
    embed.add_field(
        name="Need help?",
        value="Open the full panel at [sccompanion.org](https://sccompanion.org), or send a ticket in **feedback-and-issues**.",
        inline=False,
    )
    embed.set_footer(text="Settings only change this Discord server. They do not change other servers.")
    return embed


def build_first_run_setup_embed(*, can_manage_channels: bool = True) -> discord.Embed:
    embed = discord.Embed(
        title="Finish setting up SC Companion",
        description=("A server owner or manager can select **Open Admin Panel** below. "
                     "You can also type `/admin panel` at any time."),
        color=discord.Color.blurple(),
    )
    embed.add_field(name="What happens next?", value="Choose how channels are made, pick the features you want, and save.", inline=False)
    if not can_manage_channels:
        embed.add_field(
            name="One permission is still needed",
            value=("Give the bot **Manage Channels** before choosing automatic setup. "
                   "A server owner can update the bot role in **Server Settings → Roles**."),
            inline=False,
        )
    embed.set_footer(text="This setup notice is removed after the server settings are saved.")
    return embed


class NativeAdminModuleSelect(discord.ui.Select):
    def __init__(self, modules: dict[str, dict[str, object]]) -> None:
        options = [discord.SelectOption(label=str(value["label"]), value=key,
                                        description=", ".join(f"/{command}" for command in value["commands"])[:100],
                                        default=bool(modules[key]["enabled"]))
                   for key, value in BOT_MODULES.items()]
        super().__init__(placeholder="Choose enabled features", min_values=0, max_values=len(options), options=options)

    async def callback(self, interaction: discord.Interaction) -> None:
        bot = interaction.client
        if not isinstance(bot, GameAssistBot) or interaction.guild is None or not _can_manage_admin_commands(interaction, bot.settings):
            await interaction.response.send_message("You need Manage Server permission to change these settings.", ephemeral=True)
            return
        await interaction.response.defer()
        stored = await bot.cache.guild_bot_settings(interaction.guild.id)
        modules = normalize_module_settings(stored.get("modules") if stored else None)
        enabled = set(self.values)
        for key in modules:
            modules[key]["enabled"] = key in enabled
        await bot.cache.save_guild_bot_settings(interaction.guild.id, interaction.guild.name, modules, interaction.user.id)
        await bot.ensure_about_panel(interaction.guild)
        pending = len(await bot.cache.pending_review_requests()) if interaction.guild.id == bot.settings.discord_guild_id else 0
        refreshed = await bot.cache.guild_bot_settings(interaction.guild.id)
        mode = str((refreshed or {}).get("channel_setup_mode") or "manual")
        await interaction.edit_original_response(embed=build_native_admin_embed(interaction.guild, modules, pending), view=NativeAdminView(modules, mode))


class ChannelSetupChoiceView(discord.ui.View):
    def __init__(self, modules: dict[str, dict[str, object]]) -> None:
        super().__init__(timeout=900)
        self.modules = modules

    async def save_choice(self, interaction: discord.Interaction, mode: str) -> None:
        bot = interaction.client
        if not isinstance(bot, GameAssistBot) or interaction.guild is None or not _can_manage_admin_commands(interaction, bot.settings):
            await interaction.response.send_message("You need Manage Server permission to choose channel setup.", ephemeral=True)
            return
        await bot.cache.save_guild_bot_settings(
            interaction.guild.id, interaction.guild.name, self.modules, interaction.user.id, mode
        )
        if mode == "automatic":
            await bot.ensure_automatic_module_channels(interaction.guild)
        await interaction.response.edit_message(
            embed=build_native_admin_embed(interaction.guild, self.modules),
            view=NativeAdminView(self.modules, mode),
        )

    @discord.ui.button(label="Let the bot set up channels", style=discord.ButtonStyle.primary)
    async def automatic(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        await self.save_choice(interaction, "automatic")

    @discord.ui.button(label="I will set up channels", style=discord.ButtonStyle.secondary)
    async def manual(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        await self.save_choice(interaction, "manual")


def manual_channel_steps(modules: dict[str, dict[str, object]]) -> list[tuple[str, str]]:
    steps: list[tuple[str, str]] = []
    for key, item in modules.items():
        if not item["enabled"]:
            continue
        steps.append((key, "channel_id"))
        if key == "trade_tools":
            steps.append((key, "resource_channel_id"))
    return steps


def timer_dashboard_channel_id(modules: dict[str, dict[str, object]]) -> int | None:
    timers = modules.get("timers")
    if not timers or not timers.get("enabled") or not timers.get("channel_id"):
        return None
    return int(timers["channel_id"])


def automatic_cleanup_channel_ids(modules: dict[str, dict[str, object]]) -> set[int]:
    return {
        int(channel_id)
        for item in modules.values()
        for channel_id in (item.get("channel_id"), item.get("resource_channel_id"))
        if channel_id
    }


class ManualChannelSelect(discord.ui.ChannelSelect):
    def __init__(self, module_key: str, field_name: str) -> None:
        channel_types = ([discord.ChannelType.forum] if field_name == "resource_channel_id"
                         else [discord.ChannelType.text, discord.ChannelType.news])
        feature_label = str(BOT_MODULES[module_key]["label"])
        label = (f"Choose a marketplace forum for {feature_label}"
                 if field_name == "resource_channel_id" else f"Choose a channel for {feature_label}")
        super().__init__(placeholder=label, min_values=1, max_values=1, channel_types=channel_types)
        self.module_key = module_key
        self.field_name = field_name

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if isinstance(view, ManualChannelWizardView):
            await view.save_selection(interaction, int(self.values[0].id))


class ManualChannelWizardView(discord.ui.View):
    def __init__(self, modules: dict[str, dict[str, object]], step_index: int = 0) -> None:
        super().__init__(timeout=900)
        self.modules = modules
        self.steps = manual_channel_steps(modules)
        self.step_index = step_index
        if self.steps and step_index < len(self.steps):
            module_key, field_name = self.steps[step_index]
            self.add_item(ManualChannelSelect(module_key, field_name))
        is_marketplace = bool(self.steps and self.steps[step_index][1] == "resource_channel_id")
        any_channel = discord.ui.Button(
            label="Disable Marketplace" if is_marketplace else "Use Any Channel",
            style=discord.ButtonStyle.secondary,
        )
        any_channel.callback = self.use_any_channel
        any_channel.disabled = not bool(self.steps)
        self.add_item(any_channel)
        back = discord.ui.Button(label="Back to Panel", style=discord.ButtonStyle.secondary)
        back.callback = self.back_to_panel
        self.add_item(back)

    def embed(self) -> discord.Embed:
        if not self.steps:
            return discord.Embed(title="No features selected",
                                 description="Go back and select at least one feature first.",
                                 color=discord.Color.orange())
        module_key, field_name = self.steps[self.step_index]
        label = str(BOT_MODULES[module_key]["label"])
        instruction = (f"Choose the **marketplace forum for {label}**."
                       if field_name == "resource_channel_id"
                       else f"Choose a **channel for {label}**.")
        return discord.Embed(
            title=f"Assign Channels — Step {self.step_index + 1} of {len(self.steps)}",
            description=instruction,
            color=discord.Color.blurple(),
        )

    async def save_selection(self, interaction: discord.Interaction, channel_id: int | None) -> None:
        bot = interaction.client
        if not isinstance(bot, GameAssistBot) or interaction.guild is None or not _can_manage_admin_commands(interaction, bot.settings):
            await interaction.response.send_message("You need Manage Server permission to assign channels.", ephemeral=True)
            return
        await interaction.response.defer()
        module_key, field_name = self.steps[self.step_index]
        self.modules[module_key][field_name] = channel_id
        await bot.cache.save_guild_bot_settings(
            interaction.guild.id, interaction.guild.name, self.modules, interaction.user.id, "manual"
        )
        if field_name == "resource_channel_id" and channel_id is not None:
            forum = interaction.guild.get_channel(channel_id)
            if isinstance(forum, discord.ForumChannel):
                command_channel_id = self.modules[module_key].get("channel_id")
                await bot.configure_marketplace_forum(
                    forum, int(command_channel_id) if command_channel_id else None
                )
        next_index = self.step_index + 1
        if next_index < len(self.steps):
            next_view = ManualChannelWizardView(self.modules, next_index)
            await interaction.edit_original_response(embed=next_view.embed(), view=next_view)
            return
        await bot.ensure_about_panel(interaction.guild)
        embed = build_native_admin_embed(interaction.guild, self.modules)
        embed.description = "Manual channel setup is complete. You can change these choices at any time."
        await interaction.edit_original_response(embed=embed, view=NativeAdminView(self.modules, "manual"))

    async def use_any_channel(self, interaction: discord.Interaction) -> None:
        await self.save_selection(interaction, None)

    async def back_to_panel(self, interaction: discord.Interaction) -> None:
        await interaction.response.edit_message(
            embed=build_native_admin_embed(interaction.guild, self.modules),
            view=NativeAdminView(self.modules, "manual"),
        )


class ConfirmBotUninstallView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=300)

    @discord.ui.button(label="Delete Bot Setup and Uninstall", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        bot = interaction.client
        if not isinstance(bot, GameAssistBot) or interaction.guild is None or not _can_manage_admin_commands(interaction, bot.settings):
            await interaction.response.send_message("You need Manage Server permission to uninstall the bot.", ephemeral=True)
            return
        guild = interaction.guild
        stored = await bot.cache.guild_bot_settings(guild.id)
        modules = normalize_module_settings(stored.get("modules") if stored else None)
        automatic = bool(stored and stored.get("channel_setup_mode") == "automatic")
        setup_channel_ids: set[int] = set()
        for cache_key in (
            f"guild:{guild.id}:about-channel",
            f"guild:{guild.id}:feedback-forum",
        ):
            channel_id = await bot.cache.get(cache_key)
            if isinstance(channel_id, int):
                setup_channel_ids.add(channel_id)

        # Older installations did not save the About channel ID. Confirm that the
        # cached About panel message belongs to this bot before treating the named
        # channel as bot-owned.
        if not any(
            getattr(guild.get_channel(channel_id), "name", None) == "about-the-bot"
            for channel_id in setup_channel_ids
        ):
            about = discord.utils.find(lambda item: item.name == "about-the-bot", guild.text_channels)
            panel_message_id = await bot.cache.get(f"guild:{guild.id}:about-panel-message")
            if isinstance(about, discord.TextChannel) and panel_message_id and bot.user is not None:
                with suppress(discord.NotFound, discord.Forbidden, discord.HTTPException):
                    panel_message = await about.fetch_message(int(panel_message_id))
                    if panel_message.author.id == bot.user.id:
                        setup_channel_ids.add(about.id)
        await interaction.response.edit_message(
            content="Uninstalling SC Companion and cleaning up its channels…", embed=None, view=None
        )
        if automatic:
            category = discord.utils.find(lambda item: item.name == "SC Companion", guild.categories)
            cleanup_ids = automatic_cleanup_channel_ids(modules)
            if category is not None:
                original_children = list(category.channels)
                for channel in original_children:
                    if channel.id in cleanup_ids:
                        with suppress(discord.NotFound, discord.Forbidden, discord.HTTPException):
                            await channel.delete(reason="SC Companion confirmed uninstall")
                if not any(channel.id not in cleanup_ids for channel in original_children):
                    with suppress(discord.NotFound, discord.Forbidden, discord.HTTPException):
                        await category.delete(reason="SC Companion confirmed uninstall")
        for channel_id in setup_channel_ids:
            channel = guild.get_channel(channel_id)
            if channel is not None:
                with suppress(discord.NotFound, discord.Forbidden, discord.HTTPException):
                    await channel.delete(reason="SC Companion confirmed uninstall")
        await bot.cache.record_guild_installation(guild.id, guild.name, guild.member_count, active=False)
        await bot.cache.purge_guild_data(guild.id)
        await guild.leave()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        await interaction.response.edit_message(content="Uninstall canceled. Nothing was changed.", embed=None, view=None)


def build_uninstall_warning_embed() -> discord.Embed:
    return discord.Embed(
        title="Uninstall SC Companion?",
        description=("This removes the bot's saved settings. If the bot used automatic setup, it also deletes the "
                     "feature channels and marketplace it created inside the **SC Companion** category, then removes "
                     "the bot from this Discord. The bot-created **about-the-bot** page and **feedback-and-issues** "
                     "forum are also deleted. Manually selected channels are not deleted."),
        color=discord.Color.red(),
    )


class NativeAdminView(discord.ui.View):
    def __init__(self, modules: dict[str, dict[str, object]], setup_mode: str = "manual") -> None:
        super().__init__(timeout=900)
        self.modules = modules
        self.add_item(NativeAdminModuleSelect(modules))
        setup = discord.ui.Button(label=f"Channels: {'Bot setup' if setup_mode == 'automatic' else 'Manual'}",
                                  style=discord.ButtonStyle.secondary, emoji="⚙️")
        setup.callback = self.show_channel_choice
        self.add_item(setup)
        if setup_mode == "manual":
            assign = discord.ui.Button(label="Next: Assign Channels", style=discord.ButtonStyle.primary, emoji="➡️")
            assign.callback = self.assign_channels
            self.add_item(assign)
        guide = discord.ui.Button(label="Setup Guide", style=discord.ButtonStyle.secondary, emoji="📘")
        guide.callback = self.show_guide
        self.add_item(guide)
        self.add_item(discord.ui.Button(label="Open full website panel", style=discord.ButtonStyle.link, url="https://sccompanion.org"))
        uninstall = discord.ui.Button(label="Uninstall Bot", style=discord.ButtonStyle.danger, emoji="🗑️")
        uninstall.callback = self.show_uninstall
        self.add_item(uninstall)

    async def show_guide(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(embed=build_bot_setup_guide_embed(), ephemeral=True)

    async def show_uninstall(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(
            embed=build_uninstall_warning_embed(), view=ConfirmBotUninstallView(), ephemeral=True
        )

    async def assign_channels(self, interaction: discord.Interaction) -> None:
        view = ManualChannelWizardView(self.modules)
        await interaction.response.edit_message(embed=view.embed(), view=view)

    async def show_channel_choice(self, interaction: discord.Interaction) -> None:
        embed = discord.Embed(
            title="How should channels be set up?",
            description=("Choose **Let the bot set up channels** to make one channel for every feature you turn on. "
                         "Choose **I will set up channels** if you want to pick the channels yourself."),
            color=discord.Color.blurple(),
        )
        await interaction.response.edit_message(embed=embed, view=ChannelSetupChoiceView(self.modules))


class FirstRunSetupView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(label="Open Admin Panel", style=discord.ButtonStyle.primary,
                       emoji="⚙️", custom_id="sc-companion:first-run-admin-panel")
    async def open_panel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        await respond_with_admin_panel(interaction)


async def respond_with_admin_panel(interaction: discord.Interaction) -> None:
    bot = interaction.client
    if not isinstance(bot, GameAssistBot) or interaction.guild is None:
        await interaction.response.send_message("Bot is not fully initialized.", ephemeral=True)
        return
    if not _can_manage_admin_commands(interaction, bot.settings):
        await interaction.response.send_message(
            "Only a server owner or member with Manage Server permission can open this panel.", ephemeral=True
        )
        return
    stored = await bot.cache.guild_bot_settings(interaction.guild.id)
    modules = normalize_module_settings(stored.get("modules") if stored else None,
                                        enabled_default=interaction.guild.id == bot.settings.discord_guild_id)
    if stored is None and interaction.guild.id != bot.settings.discord_guild_id:
        embed = discord.Embed(
            title="Welcome to SC Companion",
            description=("First, choose how you want to set up channels. The bot can make them for you, "
                         "or you can pick each channel yourself."),
            color=discord.Color.blurple(),
        )
        await interaction.response.send_message(embed=embed, view=ChannelSetupChoiceView(modules), ephemeral=True)
        return
    pending = len(await bot.cache.pending_review_requests()) if interaction.guild.id == bot.settings.discord_guild_id else 0
    await interaction.response.send_message(
        embed=build_native_admin_embed(interaction.guild, modules, pending),
        view=NativeAdminView(modules, str((stored or {}).get("channel_setup_mode") or "manual")),
        ephemeral=True,
    )


@admin_group.command(name="panel", description="Open this server's native bot administration panel.")
async def admin_panel_command(interaction: discord.Interaction) -> None:
    await respond_with_admin_panel(interaction)


@admin_group.command(name="guide", description="Show simple steps for setting up the bot.")
async def admin_guide_command(interaction: discord.Interaction) -> None:
    bot = interaction.client
    if not isinstance(bot, GameAssistBot) or not _can_manage_admin_commands(interaction, bot.settings):
        await interaction.response.send_message("You need Manage Server permission to open the setup guide.", ephemeral=True)
        return
    await interaction.response.send_message(embed=build_bot_setup_guide_embed(), ephemeral=True)


@admin_group.command(name="uninstall", description="Remove this server's bot setup before uninstalling the bot.")
async def admin_uninstall_command(interaction: discord.Interaction) -> None:
    bot = interaction.client
    if not isinstance(bot, GameAssistBot) or interaction.guild is None or not _can_manage_admin_commands(interaction, bot.settings):
        await interaction.response.send_message("You need Manage Server permission to uninstall the bot.", ephemeral=True)
        return
    await interaction.response.send_message(
        embed=build_uninstall_warning_embed(), view=ConfirmBotUninstallView(), ephemeral=True
    )


@admin_group.command(name="channel", description="Assign a feature's commands to a channel in this server.")
@app_commands.describe(module="Feature key shown in /admin panel", channel="Command channel; omit to allow any channel")
async def admin_channel_command(interaction: discord.Interaction, module: str,
                                channel: discord.TextChannel | None = None) -> None:
    bot = interaction.client
    if not isinstance(bot, GameAssistBot) or interaction.guild is None or not _can_manage_admin_commands(interaction, bot.settings):
        await interaction.response.send_message("You need Manage Server permission to change bot routing.", ephemeral=True)
        return
    key = module.strip().casefold().replace(" ", "_")
    if key not in BOT_MODULES:
        await interaction.response.send_message("Unknown feature. Use `/admin panel` to see available features.", ephemeral=True)
        return
    stored = await bot.cache.guild_bot_settings(interaction.guild.id)
    modules = normalize_module_settings(stored.get("modules") if stored else None)
    modules[key]["channel_id"] = channel.id if channel else None
    await bot.cache.save_guild_bot_settings(interaction.guild.id, interaction.guild.name, modules, interaction.user.id)
    await bot.ensure_about_panel(interaction.guild)
    await interaction.response.send_message(f"{BOT_MODULES[key]['label']} now works " +
                                            (f"in {channel.mention}." if channel else "in any channel."), ephemeral=True)


PRIMARY_GUILD_ADMIN_COMMAND_NAMES = ("reviews", "review", "hub-health", "hub-repair")


@admin_group.command(name="reviews", description="Show pending global knowledge and timer reviews.")
async def admin_reviews_command(interaction: discord.Interaction) -> None:
    bot = interaction.client
    if not isinstance(bot, GameAssistBot) or interaction.guild_id != bot.settings.discord_guild_id or interaction.user.id not in bot.settings.bot_admin_user_ids:
        await interaction.response.send_message("Central reviews are restricted to the service owner in the primary Discord.", ephemeral=True)
        return
    reviews = await bot.cache.pending_review_requests(20)
    embed = discord.Embed(title="Central Review Queue", color=discord.Color.gold())
    embed.description = "No pending reviews." if not reviews else "Use `/admin review` with the request number."
    for review in reviews:
        summary = "\n".join(f"**{key}:** {value}" for key, value in list(review["payload"].items())[:6])
        embed.add_field(name=f"#{review['id']} — {review['review_type']}", value=summary[:1024] or "No details", inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)


@admin_group.command(name="review", description="Approve or reject a central review request.")
@app_commands.choices(decision=[app_commands.Choice(name="Approve", value="approved"),
                               app_commands.Choice(name="Reject", value="rejected")])
async def admin_review_command(interaction: discord.Interaction, request_id: int,
                               decision: app_commands.Choice[str]) -> None:
    bot = interaction.client
    if not isinstance(bot, GameAssistBot) or interaction.guild_id != bot.settings.discord_guild_id or interaction.user.id not in bot.settings.bot_admin_user_ids:
        await interaction.response.send_message("Central reviews are restricted to the service owner in the primary Discord.", ephemeral=True)
        return
    pending = {item["id"]: item for item in await bot.cache.pending_review_requests(500)}
    review = pending.get(request_id)
    if review is None:
        await interaction.response.send_message("That pending request was not found.", ephemeral=True)
        return
    if decision.value == "approved" and review["review_type"] == "timer":
        await bot.cache.set(exec_override_cache_key(review["origin_guild_id"]), review["payload"], 315360000)
    elif decision.value == "approved" and review["review_type"] == "mining":
        await add_community_mining_location(bot.cache, review["payload"])
    changed = await bot.cache.review_request(request_id, decision.value, interaction.user.id, str(interaction.user))
    await bot.log_audit_event("Central Review Completed", {"Request": request_id, "Decision": decision.name,
                                                             "Reviewer": _audit_user(interaction.user)})
    await interaction.response.send_message(
        f"Request #{request_id} was {decision.value}." if changed else "That request was already reviewed.", ephemeral=True)


@admin_group.command(name="channels", description="Show command channel routing.")
async def admin_channels_command(interaction: discord.Interaction) -> None:
    bot = interaction.client
    if not isinstance(bot, GameAssistBot):
        await interaction.response.send_message("Bot is not fully initialized.", ephemeral=True)
        return
    if not _can_manage_admin_commands(interaction, bot.settings):
        await interaction.response.send_message("You do not have permission to view bot management details.", ephemeral=True)
        return

    if interaction.guild is not None and interaction.guild_id != bot.settings.discord_guild_id:
        stored = await bot.cache.guild_bot_settings(interaction.guild.id)
        modules = normalize_module_settings(stored.get("modules") if stored else None)
        embed = build_native_admin_embed(interaction.guild, modules)
    else:
        embed = build_admin_channels_embed(bot.settings)
    await interaction.response.send_message(embed=embed, ephemeral=True)


@admin_group.command(name="health", description="Show bot health and configuration status.")
async def admin_health_command(interaction: discord.Interaction) -> None:
    bot = interaction.client
    if not isinstance(bot, GameAssistBot):
        await interaction.response.send_message("Bot is not fully initialized.", ephemeral=True)
        return
    if not _can_manage_admin_commands(interaction, bot.settings):
        await interaction.response.send_message("You do not have permission to view bot management details.", ephemeral=True)
        return

    embed = build_admin_health_embed(bot)
    if interaction.guild_id != bot.settings.discord_guild_id:
        stored = await bot.cache.guild_bot_settings(interaction.guild_id or 0)
        enabled = sum(bool(item["enabled"]) for item in normalize_module_settings(stored.get("modules") if stored else None).values())
        embed = discord.Embed(title="Bot Management - Server Health",
                              description=f"Status: Online\nConfiguration: {'Saved' if stored else 'Not saved'}\nEnabled features: {enabled}\nServer isolation: Active",
                              color=discord.Color.green())
    await interaction.response.send_message(embed=embed, ephemeral=True)


@admin_group.command(name="hub-health", description="Check protected Discord Bot Hub components.")
async def admin_hub_health_command(interaction: discord.Interaction) -> None:
    bot = interaction.client
    if not isinstance(bot, GameAssistBot):
        await interaction.response.send_message("Bot is not fully initialized.", ephemeral=True)
        return
    if interaction.guild_id != bot.settings.discord_guild_id or interaction.user.id not in bot.settings.bot_admin_user_ids:
        await interaction.response.send_message("You do not have permission to inspect the bot hub.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    issues = await bot.inspect_discord_bot_hub()
    embed = discord.Embed(
        title="Discord Bot Hub - Integrity Check",
        description=(
            "All protected components are healthy."
            if not issues
            else "The following issues need repair:\n" + "\n".join(f"• {issue}" for issue in issues[:20])
        ),
        color=discord.Color.green() if not issues else discord.Color.orange(),
        timestamp=discord.utils.utcnow(),
    )
    await interaction.followup.send(embed=embed, ephemeral=True)


@admin_group.command(name="hub-repair", description="Repair protected Discord Bot Hub components now.")
async def admin_hub_repair_command(interaction: discord.Interaction) -> None:
    bot = interaction.client
    if not isinstance(bot, GameAssistBot):
        await interaction.response.send_message("Bot is not fully initialized.", ephemeral=True)
        return
    if interaction.guild_id != bot.settings.discord_guild_id or interaction.user.id not in bot.settings.bot_admin_user_ids:
        await interaction.response.send_message("You do not have permission to repair the bot hub.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    before = await bot.inspect_discord_bot_hub()
    await bot._restore_hub_components()
    after = await bot.inspect_discord_bot_hub()
    await bot.log_audit_event(
        "Discord Bot Hub manual repair",
        {
            "User": _audit_user(interaction.user),
            "Issues before": len(before),
            "Issues after": len(after),
            "Scope": VISITOR_CATEGORY_NAME,
        },
    )
    result = "Repair completed; all protected components are healthy." if not after else (
        f"Repair completed, but {len(after)} issue(s) remain. Check `/admin hub-health`."
    )
    await interaction.followup.send(result, ephemeral=True)


audit_group = app_commands.Group(name="audit", description="Audit log commands.")


@audit_group.command(name="recent", description="Show recent bot audit events.")
@app_commands.describe(limit="Number of recent audit events to show, from 1 to 20.")
async def audit_recent_command(interaction: discord.Interaction, limit: int = 10) -> None:
    bot = interaction.client
    if not isinstance(bot, GameAssistBot):
        await interaction.response.send_message("Bot is not fully initialized.", ephemeral=True)
        return
    if interaction.guild_id != bot.settings.discord_guild_id or interaction.user.id not in bot.settings.bot_admin_user_ids:
        await interaction.response.send_message("You do not have permission to view audit logs.", ephemeral=True)
        return
    if limit < 1 or limit > 20:
        await interaction.response.send_message("Limit must be between 1 and 20.", ephemeral=True)
        return

    events = await bot.cache.recent_audit_events(limit)
    await interaction.response.send_message(embed=build_audit_recent_embed(events), ephemeral=True)


@app_commands.command(name="exec", description="Show the current Executive Hangar clock.")
async def exec_command(interaction: discord.Interaction) -> None:
    bot = interaction.client
    if not isinstance(bot, GameAssistBot):
        await interaction.response.send_message("Bot is not fully initialized.", ephemeral=True)
        return

    await interaction.response.defer(thinking=True, ephemeral=True)
    if interaction.guild_id != bot.settings.discord_guild_id:
        context = await bot.resolve_exec_status_context(timer_scope(bot, interaction.guild_id))
        await interaction.followup.send(embed=build_exec_status_embed(context), ephemeral=True)
        return
    await bot.sync_exec_status_message()

    if bot.settings.exec_status_channel_id:
        await interaction.followup.send(
            f"Executive Hangar panel refreshed in <#{bot.settings.exec_status_channel_id}>.",
            ephemeral=True,
        )
        return

    await interaction.followup.send("Executive Hangar status channel is not configured.", ephemeral=True)


@app_commands.command(name="execset", description="Correct the Executive Hangar timer.")
@app_commands.describe(
    phase="Current Executive Hangar phase.",
    remaining_minutes="Minutes remaining in the selected phase.",
)
@app_commands.choices(
    phase=[
        app_commands.Choice(name="Closed", value="closed"),
        app_commands.Choice(name="Open", value="open"),
        app_commands.Choice(name="Resetting", value="resetting"),
    ]
)
async def execset_command(
    interaction: discord.Interaction,
    phase: app_commands.Choice[str],
    remaining_minutes: int,
) -> None:
    bot = interaction.client
    if not isinstance(bot, GameAssistBot):
        await interaction.response.send_message("Bot is not fully initialized.", ephemeral=True)
        return

    if not _can_manage_exec_timer(interaction, bot.settings):
        await interaction.response.send_message("You do not have permission to update the Executive Hangar timer.", ephemeral=True)
        return

    try:
        cycle_start = calculate_cycle_start_from_phase(phase.value, remaining_minutes)
    except ValueError as error:
        await interaction.response.send_message(str(error), ephemeral=True)
        return

    payload = {
            "cycle_start_unix": cycle_start,
            "updated_by": interaction.user.id,
            "updated_by_name": str(interaction.user),
            "updated_at_unix": discord.utils.utcnow().timestamp(),
            "phase": phase.value,
            "remaining_minutes": remaining_minutes,
        }
    if interaction.guild_id != bot.settings.discord_guild_id:
        review_id = await bot.cache.submit_review_request(
            "timer", payload, submitted_by=interaction.user.id, submitted_by_name=str(interaction.user),
            origin_guild_id=interaction.guild_id, origin_channel_id=interaction.channel_id,
        )
        await interaction.response.send_message(
            f"Timer correction #{review_id} was sent to the central review queue. It will only affect this server after approval.",
            ephemeral=True,
        )
        return
    await bot.cache.set(exec_override_cache_key(None), payload, 315360000)
    await bot.sync_exec_status_message()
    await bot.log_audit_event(
        "Executive Timer Corrected",
        {
            "User": _audit_user(interaction.user),
            "Channel": _audit_channel(interaction.channel_id),
            "Phase": phase.name,
            "Remaining Minutes": remaining_minutes,
        },
        color=discord.Color.gold(),
    )
    await interaction.response.send_message("Executive Hangar timer override saved and status message updated.", ephemeral=True)


@app_commands.command(name="execclear", description="Clear the manual Executive Hangar timer override.")
async def execclear_command(interaction: discord.Interaction) -> None:
    bot = interaction.client
    if not isinstance(bot, GameAssistBot):
        await interaction.response.send_message("Bot is not fully initialized.", ephemeral=True)
        return

    if not _can_manage_exec_timer(interaction, bot.settings):
        await interaction.response.send_message("You do not have permission to clear the Executive Hangar timer.", ephemeral=True)
        return

    await bot.cache.delete(exec_override_cache_key(timer_scope(bot, interaction.guild_id)))
    await bot.sync_exec_status_message()
    await bot.log_audit_event(
        "Executive Timer Override Cleared",
        {
            "User": _audit_user(interaction.user),
            "Channel": _audit_channel(interaction.channel_id),
        },
        color=discord.Color.gold(),
    )
    await interaction.response.send_message("Executive Hangar timer override cleared. Using community timer source again.", ephemeral=True)


def build_exec_status_embed(status_context: dict) -> discord.Embed:
    source_status = status_context["source_status"]
    corrected_status = status_context.get("corrected_status")
    override = status_context.get("override")
    display_status = corrected_status or source_status

    embed = discord.Embed(
        title="Executive Hangar Clock",
        description=f"Status: {display_status.status}\nPhase: {display_status.status_detail}",
        url=display_status.source_url,
        color=discord.Color.green() if display_status.status == "Open" else discord.Color.red(),
    )
    embed.add_field(name="Active Timer", value=_format_exec_status(display_status), inline=False)

    if corrected_status is not None and isinstance(override, dict):
        embed.add_field(name="Website Source Timer", value=_format_exec_status(source_status), inline=False)
        updated_by = override.get("updated_by")
        updated_by_name = override.get("updated_by_name")
        if isinstance(updated_by, int):
            user = f"<@{updated_by}>"
        else:
            user = str(updated_by_name or "Unknown user")
        updated_at = override.get("updated_at_unix")
        updated_line = f"\nUpdated: <t:{int(updated_at)}:R>" if isinstance(updated_at, (int, float)) else ""
        embed.add_field(
            name="Manual Correction",
            value=f"Corrected by: {user}{updated_line}",
            inline=False,
        )

    embed.set_footer(
        text="Source timer: contestedzonetimers.com community timer. Corrected timer shown when manually adjusted."
    )
    return embed


def build_exec_example_embed() -> discord.Embed:
    embed = discord.Embed(
        title="Example /exec Response",
        description="This is an example of what the live Executive Hangar response shows.",
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="Information included",
        value="Current open/closed status, active phase, light state, and Discord-relative time until the next change.",
        inline=False,
    )
    embed.add_field(
        name="How to run it",
        value="1. Type `/exec`.\n2. Select the **/exec** command from **Peep**.\n3. Press **Enter**.",
        inline=False,
    )
    embed.set_footer(text="The separate Executive Hangar Clock embed in this channel updates automatically.")
    return embed


def _visitor_example_embed(
    title: str,
    command: str,
    description: str,
    fields: tuple[tuple[str, str], ...],
) -> discord.Embed:
    embed = discord.Embed(
        title=title,
        description=f"**Example command:** `{command}`\n{description}",
        color=discord.Color.blurple(),
    )
    for name, value in fields:
        embed.add_field(name=name, value=value, inline=False)
    embed.set_footer(text="Example data only. Run the command for current results.")
    return embed


def build_visitor_command_example_embeds() -> dict[str, discord.Embed]:
    """Return one durable, realistic response example for every Visitor command channel."""
    return {
        "ship-search": _visitor_example_embed(
            "Example /ship Response",
            "/ship name: Carrack",
            "Type `/ship`, select the `name` option, enter or choose `Carrack`, then submit. Results summarize role, manufacturer, crew, cargo, and useful specifications.",
            (("Anvil Carrack", "Expedition · Crew 4–6 · Cargo 456 SCU"), ("Key data", "Length 126.5 m · Large hangar · Medical facility"), ("Source", "Current game-data reference link")),
        ),
        "trade-tools": _visitor_example_embed(
            "Example Trade Response",
            "/trade routing starting_point: Area18 investment: 500000",
            "Type `/trade`, select `routing`, choose `starting_point`, and enter or choose `Area18`. Optionally set `ship`, `investment`, `max_stops`, or `stay_system`, then submit.",
            (("Buy", "Medical Supplies at Area18 · 18.25 aUEC/unit"), ("Sell", "Baijini Point · 19.41 aUEC/unit"), ("Estimate", "Cost 492,750 aUEC · Revenue 524,070 aUEC · Profit 31,320 aUEC"), ("Commodity search", "Type `/commodity`, select `name`, enter or choose `Gold`, then submit for market locations and prices.")),
        ),
        "mining-tools": _visitor_example_embed(
            "Example /mining Response",
            "/mining material: Quantanium",
            "Type `/mining`, select the `material` option, enter or choose `Quantanium`, then submit. Optional `system` and `planet` options narrow the results.",
            (("Best locations", "Lyria · Aaron Halo · microTech moon belts"), ("Scan signature", "High-value volatile mineral; confirm cluster composition before extraction"), ("Handling", "Transport promptly after collection and monitor instability"), ("Community data", "Type `/miningadd`, then fill `material`, `system`, `location_type`, and `location` to submit a verified location."), ("Industry tools", "Use `/industry split` for crew payouts, `/industry refinery` for completion times, or `/industry brief` for an operation brief.")),
        ),
        "blueprints": _visitor_example_embed(
            "Example Blueprint Response",
            "/blueprint name: NDB-28 Repeater qualities: Titanium=750, Gold=820, Lindinium=910",
            "Type `/blueprint`, select `name`, and choose the blueprint. Add `qualities` to calculate crafted stats. Enter one number, such as `750`, to apply it to every material, or enter comma-separated `Material=quality` pairs to give every required material a different value. Qualities must be from 0 to 1000.",
            (("NDB-28 Repeater", "Vehicle weapon · Blueprint available"), ("Required materials", "Titanium 0.64 SCU · Gold 0.22 SCU · Lindinium 0.13 SCU"), ("Quality calculation", "Titanium Q750: Integrity +5.0%\nGold Q820: Impact Force +3.2%\nLindinium Q910: Impact Force +4.1%"), ("Command tips", "Use `qualities: 750` for the same quality on all materials. Use `/myblueprints` to search saved blueprints.")),
        ),
        "missions-wikelo": _visitor_example_embed(
            "Example Mission & Wikelo Response",
            "/mission rep_giver: Wikelo",
            "Type `/mission` and choose a name or filter. Use `/wikelo` when you want to search Wikelo contracts by reward or required item.",
            (("Mission search", "Filter by region, reputation giver, reputation level, or mission type."), ("Wikelo search", "Use `/wikelo` and choose a reward or mission such as `Golem` to see the turn-in list, required reputation, and reputation awarded.")),
        ),
        "item-locator": _visitor_example_embed(
            "Example Item Locator Response",
            "/item search name: FS-9 LMG",
            "Type `/item`, select `search`, choose the `name` option, enter or choose `FS-9 LMG`, then submit. Optional `category`, `section`, `size`, and `location` options narrow the results.",
            (("FS-9 LMG", "Personal Weapons · Light machine gun"), ("Purchase locations", "CenterMass, Area18 · Live Fire Weapons, Port Tressler"), ("Details", "Price, stock status, manufacturer, size, and source link when available")),
        ),
        "inventory-search": _visitor_example_embed(
            "Example /inventory search Response",
            "/inventory search item: FS-9 station: Port Tressler",
            "Type `/inventory`, select `search`, choose `item`, and enter or choose `FS-9`. Add `station` only if you want to limit the search to `Port Tressler`, then submit.",
            (("FS-9 LMG × 2", "Port Tressler · Personal Weapons / Weapons"), ("FS-9 Magazine × 14", "Port Tressler · Personal Weapons / Ammunition"), ("Privacy", "Only your linked inventory is searched")),
        ),
        "timers": _visitor_example_embed(
            "Example Timer Responses",
            "/exec",
            "Use `/exec` for the Executive Hangar clock. Use `/cztimer` to create and manage this server's Contested Zone timers.",
            (("Executive Hangar", "Current phase, lights, and next change time."), ("Contested Zone", "Persistent keycard, compboard, and door countdowns for this Discord.")),
        ),
    }


def _format_exec_status(status: ExecHangarStatus) -> str:
    return (
        f"Status: {status.status}\n"
        f"Phase: {status.status_detail}\n"
        f"Lights: {status.lights}\n"
        f"Next Change: <t:{status.next_change_unix}:R>\n"
        f"At: <t:{status.next_change_unix}:T>"
    )


def build_admin_channels_embed(settings: Settings) -> discord.Embed:
    embed = build_command_channel_directory_embed(settings)
    embed.title = "Bot Management - Command Channels"

    special_channels = [
        _line("Command Reference", f"<#{settings.commands_channel_id}>" if settings.commands_channel_id else None),
        _line("Audit Log", f"<#{settings.audit_log_channel_id}>" if settings.audit_log_channel_id else None),
        _line("Executive Status", f"<#{settings.exec_status_channel_id}>" if settings.exec_status_channel_id else None),
        _line("CZ Dashboard", f"<#{settings.cz_timers_channel_id}>" if settings.cz_timers_channel_id else None),
    ]
    embed.add_field(
        name="Bot Channels",
        value="\n".join(line for line in special_channels if line) or "No bot channels configured.",
        inline=False,
    )
    return embed


def build_admin_health_embed(bot: GameAssistBot) -> discord.Embed:
    settings = bot.settings
    now = int(discord.utils.utcnow().timestamp())
    description = [
        _line("Status", "Online"),
        _line("Uptime", _format_duration(max(0, now - bot.started_at_unix))),
        _line("Guild ID", str(settings.discord_guild_id) if settings.discord_guild_id else "Global commands"),
        _line("Command Channels", str(len(settings.command_channel_ids))),
        _line("Audit Log", f"<#{settings.audit_log_channel_id}>" if settings.audit_log_channel_id else "Not configured"),
        _line("Change Command Roles", _format_role_ids(settings.exec_admin_role_ids)),
        _line("Admin/Audit Roles", _format_role_ids(settings.bot_admin_role_ids)),
        _line("Admin/Audit Users", _format_user_ids(settings.bot_admin_user_ids)),
        _line("Database", settings.database_path),
        _line("Cache TTL", _format_duration(settings.cache_ttl_seconds)),
    ]
    return discord.Embed(
        title="Bot Management - Health",
        description="\n".join(line for line in description if line),
        color=discord.Color.green(),
    )


def build_audit_recent_embed(events: list[dict]) -> discord.Embed:
    embed = discord.Embed(
        title="Audit - Recent Events",
        color=discord.Color.blurple(),
        timestamp=discord.utils.utcnow(),
    )
    if not events:
        embed.description = "No audit events recorded yet."
        return embed

    for event in events:
        fields = event.get("fields") if isinstance(event.get("fields"), dict) else {}
        summary_parts = []
        for key in ("Command", "User", "Channel", "Action", "Timer", "Material", "Phase"):
            value = fields.get(key)
            if value:
                summary_parts.append(f"{key}: {value}")
        summary = "\n".join(summary_parts) or "No details recorded."
        created_at = event.get("created_at")
        when = f" <t:{created_at}:R>" if isinstance(created_at, int) else ""
        embed.add_field(
            name=f"{event.get('title', 'Audit Event')}{when}",
            value=_truncate_audit_value(summary),
            inline=False,
        )
    return embed


def _can_manage_exec_timer(interaction: discord.Interaction, settings: Settings) -> bool:
    return _can_manage_change_commands(interaction, settings)


def _can_manage_change_commands(interaction: discord.Interaction, settings: Settings) -> bool:
    user = interaction.user
    if not isinstance(user, discord.Member):
        return False

    if any(role.name.casefold() == BOT_MANAGER_ROLE_NAME.casefold() for role in user.roles):
        return True

    if settings.exec_admin_role_ids:
        user_role_ids = {role.id for role in user.roles}
        return bool(user_role_ids.intersection(settings.exec_admin_role_ids))

    return user.guild_permissions.manage_guild


def _can_manage_admin_commands(interaction: discord.Interaction, settings: Settings) -> bool:
    user = interaction.user
    if getattr(user, "id", None) in settings.bot_admin_user_ids:
        return True
    if not isinstance(user, discord.Member):
        return False

    if any(role.name.casefold() == BOT_MANAGER_ROLE_NAME.casefold() for role in user.roles):
        return True

    if settings.bot_admin_role_ids:
        user_role_ids = {role.id for role in user.roles}
        return bool(user_role_ids.intersection(settings.bot_admin_role_ids))

    return user.guild_permissions.manage_guild


def _format_role_ids(role_ids: tuple[int, ...]) -> str:
    return ", ".join(f"<@&{role_id}>" for role_id in role_ids) if role_ids else "Manage Server fallback"


def _format_user_ids(user_ids: tuple[int, ...]) -> str:
    return ", ".join(f"<@{user_id}>" for user_id in user_ids) if user_ids else "None configured"


@app_commands.command(name="cztimer", description="Start a local contested-zone countdown.")
@app_commands.describe(
    timer="The contested-zone timer to track.",
    started_minutes_ago="Optional minutes already elapsed.",
)
@app_commands.choices(
    timer=[
        app_commands.Choice(name="Blue keycard terminal - 15 min", value="blue_keycard"),
        app_commands.Choice(name="Compboard/tablet - 30 min", value="compboard"),
        app_commands.Choice(name="Red supervisor keycard - 30 min", value="red_keycard"),
        app_commands.Choice(name="Ruin timer door cycle - 20 min", value="ruin_timer_door"),
    ]
)
async def cztimer_command(
    interaction: discord.Interaction,
    timer: app_commands.Choice[str],
    started_minutes_ago: int = 0,
) -> None:
    if started_minutes_ago < 0:
        await interaction.response.send_message("Elapsed minutes cannot be negative.", ephemeral=True)
        return

    durations = {
        "blue_keycard": ("Blue Keycard Terminal", 15 * 60),
        "compboard": ("Compboard / Tablet", 30 * 60),
        "red_keycard": ("Red Supervisor Keycard", 30 * 60),
        "ruin_timer_door": ("Ruin Timer Door Cycle", 20 * 60),
    }
    label, duration = durations[timer.value]
    ends_at = calculate_countdown_end_unix(duration, started_minutes_ago)

    embed = discord.Embed(
        title=label,
        description=f"Ready <t:{ends_at}:R>\nReady at <t:{ends_at}:T>",
        color=discord.Color.orange(),
    )
    embed.set_footer(text="Local helper timer based on known contested-zone durations")
    await interaction.response.send_message(embed=embed, ephemeral=True)


class CZTimerDashboardView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(label="Start Blue Keycards", style=discord.ButtonStyle.primary, custom_id="cz:start:blue_keycard", row=0)
    async def start_blue_keycard(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        await handle_cz_timer_button(interaction, "start", "blue_keycard")

    @discord.ui.button(label="Reset Blue Keycards", style=discord.ButtonStyle.secondary, custom_id="cz:reset:blue_keycard", row=0)
    async def reset_blue_keycard(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        await handle_cz_timer_button(interaction, "reset", "blue_keycard")

    @discord.ui.button(label="Start Compboards", style=discord.ButtonStyle.primary, custom_id="cz:start:compboard", row=1)
    async def start_compboard(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        await handle_cz_timer_button(interaction, "start", "compboard")

    @discord.ui.button(label="Reset Compboards", style=discord.ButtonStyle.secondary, custom_id="cz:reset:compboard", row=1)
    async def reset_compboard(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        await handle_cz_timer_button(interaction, "reset", "compboard")

    @discord.ui.button(label="Start Red Keycards", style=discord.ButtonStyle.primary, custom_id="cz:start:red_keycard", row=2)
    async def start_red_keycard(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        await handle_cz_timer_button(interaction, "start", "red_keycard")

    @discord.ui.button(label="Reset Red Keycards", style=discord.ButtonStyle.secondary, custom_id="cz:reset:red_keycard", row=2)
    async def reset_red_keycard(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        await handle_cz_timer_button(interaction, "reset", "red_keycard")

    @discord.ui.button(label="Start Timer Doors", style=discord.ButtonStyle.primary, custom_id="cz:start:timer_door", row=3)
    async def start_timer_door(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        await handle_cz_timer_button(interaction, "start", "timer_door")

    @discord.ui.button(label="Reset Timer Doors", style=discord.ButtonStyle.secondary, custom_id="cz:reset:timer_door", row=3)
    async def reset_timer_door(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        await handle_cz_timer_button(interaction, "reset", "timer_door")

    @discord.ui.button(label="Reset All", style=discord.ButtonStyle.danger, custom_id="cz:reset:all", row=4)
    async def reset_all(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        del button
        await handle_cz_timer_button(interaction, "reset_all", None)


async def handle_cz_timer_button(
    interaction: discord.Interaction,
    action: str,
    timer_key: str | None,
) -> None:
    bot = interaction.client
    if not isinstance(bot, GameAssistBot):
        await interaction.response.send_message("Bot is not fully initialized.", ephemeral=True)
        return

    scope = timer_scope(bot, interaction.guild_id)
    timers = await get_cz_dashboard_timers(bot.cache, scope)
    now = int(discord.utils.utcnow().timestamp())

    if action == "start" and timer_key in CZ_TIMER_DEFINITIONS:
        label, duration = CZ_TIMER_DEFINITIONS[timer_key]
        timers[timer_key] = {
            "end_unix": now + duration,
            "updated_by": interaction.user.id,
            "updated_by_name": str(interaction.user),
            "updated_at_unix": now,
        }
        message = f"{label} timer started."
    elif action == "reset" and timer_key in CZ_TIMER_DEFINITIONS:
        label, _duration = CZ_TIMER_DEFINITIONS[timer_key]
        timers.pop(timer_key, None)
        message = f"{label} timer reset."
    elif action == "reset_all":
        timers = {}
        message = "All CZ timers reset."
    else:
        await interaction.response.send_message("Unknown CZ timer action.", ephemeral=True)
        return

    await set_cz_dashboard_timers(bot.cache, timers, scope)
    embed = build_cz_dashboard_embed(timers)
    await interaction.response.edit_message(embed=embed, view=CZTimerDashboardView())
    await bot.log_audit_event(
        "CZ Timer Updated",
        {
            "User": _audit_user(interaction.user),
            "Channel": _audit_channel(interaction.channel_id),
            "Action": action,
            "Timer": CZ_TIMER_DEFINITIONS[timer_key][0] if timer_key in CZ_TIMER_DEFINITIONS else "All",
        },
        color=discord.Color.orange(),
    )
    await interaction.followup.send(message, ephemeral=True)


async def get_cz_dashboard_timers(cache: SQLiteCache, guild_id: int | None = None) -> dict:
    timers = await cache.get(cz_timers_cache_key(guild_id))
    return timers if isinstance(timers, dict) else {}


async def set_cz_dashboard_timers(cache: SQLiteCache, timers: dict, guild_id: int | None = None) -> None:
    await cache.set(cz_timers_cache_key(guild_id), timers, 315360000)


def build_cz_dashboard_embed(timers: dict) -> discord.Embed:
    embed = discord.Embed(
        title="Contested Zone Timers",
        description="Use the buttons below to start or reset shared CZ timers.",
        color=discord.Color.orange(),
    )

    for key, (label, duration) in CZ_TIMER_DEFINITIONS.items():
        timer = timers.get(key)
        value = _format_cz_timer_value(timer, duration)
        embed.add_field(name=label, value=value, inline=False)

    embed.set_footer(text="Shared dashboard. Timers update when buttons are used and refresh every 60s while the bot is running.")
    return embed


def build_cz_example_embed() -> discord.Embed:
    embed = discord.Embed(
        title="Example /cztimer Response",
        description="This is an example of how shared Contested Zone timer responses work.",
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="After starting a timer",
        value="The response confirms the selected objective, shows when it will be ready, and records who started it.",
        inline=False,
    )
    embed.add_field(
        name="How to run it",
        value="Type `/cztimer`, select the required `timer` option, choose the objective, optionally set `started_minutes_ago`, then submit. You can also use the live dashboard buttons below.",
        inline=False,
    )
    embed.set_footer(text="The live dashboard updates automatically every 60 seconds.")
    return embed


def _format_cz_timer_value(timer: object, duration: int) -> str:
    if not isinstance(timer, dict):
        return f"Ready\nDefault duration: {_format_duration(duration)}"

    end_unix = timer.get("end_unix")
    if not isinstance(end_unix, int):
        return f"Ready\nDefault duration: {_format_duration(duration)}"

    now = int(discord.utils.utcnow().timestamp())
    user_id = timer.get("updated_by")
    user = f"<@{user_id}>" if isinstance(user_id, int) else str(timer.get("updated_by_name") or "Unknown user")

    if end_unix <= now:
        return f"Ready\nLast started by: {user}"

    return f"Ready <t:{end_unix}:R>\nAt <t:{end_unix}:T>\nStarted by: {user}"


def _format_duration(seconds: int) -> str:
    minutes = seconds // 60
    return f"{minutes} min"


def build_ship_embed(result: ShipResult) -> discord.Embed:
    embed = discord.Embed(
        title=result.name,
        description=result.description or "No basic description available.",
        url=result.source_url,
        color=discord.Color.dark_teal(),
    )

    overview = [
        _line("Manufacturer", result.manufacturer),
        _line("Type", result.vehicle_type),
        _line("Role", result.role),
        _line("Size", result.size),
        _line("Status", result.status),
    ]
    embed.add_field(name="Overview", value="\n".join(line for line in overview if line) or "Unknown", inline=False)

    specs = [
        _line("Cargo", f"{_format_number(result.cargo_capacity)} SCU" if result.cargo_capacity is not None else None),
        _line("Crew", str(result.crew) if result.crew is not None else None),
    ]
    embed.add_field(name="Specs", value="\n".join(line for line in specs if line) or "Unknown", inline=False)

    embed.add_field(name="Pledge Store", value=_format_pledge(result), inline=False)
    embed.add_field(name="In-Game Purchase", value=_format_purchases(result), inline=False)
    embed.set_footer(text=f"Source: {result.source_name} + UEX pledge/pricing data")
    return embed


def build_commodity_embed(
    result: CommodityResult,
    quantity_scu: float | None = None,
    system: str | None = None,
    purchase_system: str | None = None,
    sell_system: str | None = None,
) -> discord.Embed:
    purchase_filter = purchase_system or system
    sell_filter = sell_system or system
    description = [
        _line("Code", result.code),
        _line("Purchase System", purchase_filter),
        _line("Sell System", sell_filter),
    ]

    embed = discord.Embed(
        title=result.name,
        description="\n".join(line for line in description if line),
        url=result.wiki_url,
        color=discord.Color.gold(),
    )
    embed.add_field(
        name="Purchase Locations",
        value=_format_markets(result.buy_from),
        inline=False,
    )
    embed.add_field(
        name="Sell Locations",
        value=_format_markets(result.sell_to),
        inline=False,
    )
    if quantity_scu is not None:
        embed.add_field(
            name=f"Estimate for {_format_number(quantity_scu)} SCU",
            value=_format_commodity_estimate(result, quantity_scu),
            inline=False,
        )
    uex_url = _uex_commodity_url(result.name)
    embed.add_field(
        name="Source",
        value=f"{result.source_name} · [View on UEX]({uex_url})",
        inline=False,
    )
    return embed


def _uex_commodity_url(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-")
    return f"https://uexcorp.space/commodities/info/name/{slug}/"


def build_mining_embed(
    result: MiningLocationResult,
    system: str | None = None,
    planet: str | None = None,
    page: int = 1,
) -> discord.Embed:
    page_count = _mining_location_page_count(result)
    description = [
        _line("Code", result.code),
        _format_mining_signature_block(result.rock_signatures),
        _line("System Filter", system),
        _line("Location Filter", planet),
        _line("Location Basis", result.location_basis),
        _line("Refined Sell", _format_currency(result.refined_sell_price, "aUEC") if result.refined_sell_price else None),
        _line("Raw Sell", _format_currency(result.raw_sell_price, "aUEC") if result.raw_sell_price else None),
        _line("Tip", "Use the system selector or optional `system` field to narrow results." if page_count > 1 and not system else None),
    ]
    flags = _format_mining_flags(result)
    if flags:
        description.append(_line("Flags", flags))

    embed = discord.Embed(
        title=f"{result.material_name} Mining",
        description="\n".join(line for line in description if line),
        url=result.source_url,
        color=discord.Color.dark_gold(),
    )
    field_name = "Mining Locations"
    if page_count > 1:
        field_name = f"{field_name} (Page {page}/{page_count})"
    embed.add_field(name=field_name, value=_format_mining_location_page(result, page), inline=False)
    embed.set_footer(text=f"Source: {result.source_name} mining locations")
    return embed


async def build_multi_mining_signature_embed(
    sources: SourceRegistry,
    query: str,
    terms: list[str],
) -> discord.Embed:
    results: list[tuple[str, MiningLocationResult, list[int]]] = []
    missing: list[str] = []
    for term in terms:
        result = await sources.lookup_mining_material(term)
        if result is None:
            missing.append(term)
            continue
        signatures = _mining_term_signatures(result, term)
        results.append((term, result, signatures))

    material_names = _unique_preserve_order([result.material_name for _, result, _ in results])
    shared_signatures = _shared_mining_signatures([signatures for _, _, signatures in results])
    description = [
        _line("Search", query),
        _line("Materials", ", ".join(material_names) if material_names else None),
    ]
    if missing:
        description.append(_line("Not Found", ", ".join(missing)))

    embed = discord.Embed(
        title="Mining Signature Match",
        description="\n".join(line for line in description if line) or "No matching materials found.",
        color=discord.Color.dark_gold(),
    )
    embed.add_field(
        name="Rock Signatures",
        value=_format_rock_signatures(shared_signatures)
        if shared_signatures
        else "No shared rock signatures found for those materials.",
        inline=False,
    )
    embed.set_footer(text="Multi-material mining searches show shared rock signatures only.")
    return embed


def build_blueprint_embed(
    result: BlueprintResult,
    name: str | None = None,
    category: str | None = None,
    material: str | None = None,
    mission_type: str | None = None,
    contractor: str | None = None,
    mission_page: int = 1,
    quality_values: dict[str, float] | None = None,
) -> discord.Embed:
    description = [
        _line("Category", result.category),
        _line("Craft Time", _format_seconds_duration(result.craft_time_seconds)),
        _line("Tiers", str(result.tiers) if result.tiers is not None else None),
        _line("Search", name),
        _line("Category Filter", category),
        _line("Material Filter", material),
        _line("Mission Type Filter", mission_type),
        _line("Contractor Filter", contractor),
    ]
    embed = discord.Embed(
        title=result.name,
        description="\n".join(line for line in description if line),
        color=discord.Color.dark_gold(),
    )
    embed.add_field(name="Materials", value=_format_blueprint_ingredients(result.ingredients), inline=False)
    if quality_values:
        embed.add_field(
            name="Quality Calculation",
            value=_format_blueprint_quality_calculation(result.ingredients, quality_values),
            inline=False,
        )
    page_count = _blueprint_mission_page_count(result.missions)
    field_name = "Blueprint Missions"
    if page_count > 1:
        field_name = f"{field_name} (Page {mission_page}/{page_count})"
    embed.add_field(
        name=field_name,
        value=_format_blueprint_missions(result.missions, page=mission_page),
        inline=False,
    )
    embed.set_footer(text=result.version or "Current version")
    return embed


def build_mission_embed(result: MissionResult) -> discord.Embed:
    embed = discord.Embed(
        title=result.name,
        color=discord.Color(0x2F8FE5),
    )
    if result.contractor:
        embed.add_field(name="Reputation Giver", value=result.contractor, inline=True)
    if result.region:
        embed.add_field(name="Region", value=result.region, inline=True)
    if result.mission_type:
        embed.add_field(name="Mission Type", value=result.mission_type, inline=True)
    standing = " · ".join(filter(None, [
        result.min_standing_name,
        f"{result.min_standing_reputation:g} rep"
        if result.min_standing_reputation is not None else None,
    ]))
    if standing:
        embed.add_field(name="Required Reputation", value=standing, inline=False)
    rewards = []
    for reward in result.blueprint_rewards:
        chance = f" ({reward.drop_chance * 100:g}%)" if reward.drop_chance is not None else ""
        rewards.append(f"• {reward.name}{chance}")
    embed.add_field(
        name="Blueprint Rewards",
        value="\n".join(rewards)[:1024] if rewards else "No blueprint reward in the current dataset.",
        inline=False,
    )
    embed.set_footer(text=" · ".join(filter(None, [
        "Crusader Industries Contract Network",
        result.version,
    ])))
    return embed


def build_blueprint_selection_embed(
    results: list[BlueprintResult],
    category: str | None = None,
    material: str | None = None,
    mission_type: str | None = None,
    contractor: str | None = None,
    page: int = 1,
    has_next: bool = False,
) -> discord.Embed:
    description = [
        _line("Category Filter", category),
        _line("Material Filter", material),
        _line("Mission Type Filter", mission_type),
        _line("Contractor Filter", contractor),
    ]
    embed = discord.Embed(
        title="Blueprint Results",
        description="\n".join(line for line in description if line) or "Available blueprints matching your filters.",
        color=discord.Color.dark_gold(),
    )
    lines = []
    for index, result in enumerate(results[:25], start=1):
        lines.append(f"{index}. {result.name} - {_blueprint_result_label(result)}")

    embed.add_field(
        name="Available Blueprints",
        value=_limit_lines(lines, 1000),
        inline=False,
    )
    page_hint = f"Page {page}"
    if has_next:
        page_hint = f"{page_hint} | More results available"
    embed.set_footer(text=f"{page_hint} | Select a blueprint below to view materials and mission details.")
    return embed


def _blueprint_result_label(result: BlueprintResult) -> str:
    details = [value for value in [result.category, result.component_size] if value]
    return " | ".join(details) if details else "Blueprint"


def build_item_locator_selection_embed(
    results: list[ItemLocatorResult],
    name: str | None = None,
    category: str | None = None,
    section: str | None = None,
    size: str | None = None,
    location: str | None = None,
    page: int = 1,
    has_next: bool = False,
) -> discord.Embed:
    description = [
        _line("Search", name),
        _line("Category Filter", category),
        _line("Section Filter", section),
        _line("Size Filter", size),
        _line("Location Filter", location),
    ]
    embed = discord.Embed(
        title="Item Locator Results",
        description="\n".join(line for line in description if line) or "In-game buyable items matching your filters.",
        color=discord.Color.green(),
    )
    lines = [
        f"{index}. {result.name} - {_item_locator_result_label(result)}"
        for index, result in enumerate(results[:25], start=1)
    ]
    embed.add_field(name="Available Items", value=_limit_lines(lines, 1000), inline=False)
    page_hint = f"Page {page}"
    if has_next:
        page_hint = f"{page_hint} | More results available"
    embed.set_footer(text=f"{page_hint} | Select an item below to view buy locations.")
    return embed


def build_item_locator_embed(
    result: ItemLocatorResult,
    name: str | None = None,
    category: str | None = None,
    section: str | None = None,
    size: str | None = None,
    location: str | None = None,
) -> discord.Embed:
    description = [
        _line("Section", result.section),
        _line("Category", result.category),
        _line("Size", _item_size_label(result.size)),
        _line("Manufacturer", result.company_name),
        _line("Search", name),
        _line("Category Filter", category),
        _line("Section Filter", section),
        _line("Size Filter", size),
        _line("Location Filter", location),
    ]
    embed = discord.Embed(
        title=result.name,
        description="\n".join(line for line in description if line),
        url=result.wiki_url or result.source_url,
        color=discord.Color.green(),
    )
    embed.add_field(name="Purchase Locations", value=_format_item_purchase_locations(result.purchases), inline=False)
    embed.set_footer(text=f"Source: {result.source_name}")
    return embed


def build_loot_item_embed(
    result: LootItemResult, sightings: list[dict] | None = None
) -> discord.Embed:
    details = [
        _line("Lootable", "Yes — game catalog flag"),
        _line("Type", result.classification),
        _line("Category", result.category),
        _line("Manufacturer", result.manufacturer),
        _line("Size", _item_size_label(result.size)),
        _line("Rarity", result.rarity),
        _line("Game Version", result.game_version),
    ]
    description = "\n".join(line for line in details if line)
    if result.description:
        summary = " ".join(result.description.split())
        description = f"{description}\n\n{summary[:700]}" if description else summary[:700]
    embed = discord.Embed(
        title=result.name,
        description=description,
        url=result.wiki_url,
        color=discord.Color.gold(),
    )
    prices = []
    if result.marketplace_sell_average:
        prices.append(f"Player marketplace average: {_format_currency(result.marketplace_sell_average, 'aUEC')}")
    if result.terminal_sell_average:
        prices.append(f"Terminal sell average: {_format_currency(result.terminal_sell_average, 'aUEC')}")
    embed.add_field(
        name="UEX Pricing",
        value="\n".join(prices) if prices else "No current UEX price average found.",
        inline=False,
    )
    embed.add_field(
        name="Locations",
        value=_format_approved_loot_sightings(sightings or []),
        inline=False,
    )
    embed.add_field(
        name="Links",
        value=f"[Star Citizen Wiki]({result.wiki_url}) • [Live prices on UEX]({result.uex_url})",
        inline=False,
    )
    if result.image_url:
        embed.set_thumbnail(url=result.image_url)
    embed.set_footer(text="Item data: Star Citizen Wiki API • Prices: UEX • Unofficial community tool")
    return embed


def _wikelo_reward_summary(result: WikeloMissionResult) -> str:
    names = [reward.name for reward in result.rewards]
    return ", ".join(names) if names else "Reward details unavailable"


def build_wikelo_browse_embed(
    results: list[WikeloMissionResult], page: int, has_next: bool,
) -> discord.Embed:
    lines = [
        f"{index}. **{discord.utils.escape_markdown(result.name)}** — "
        f"{discord.utils.escape_markdown(_wikelo_reward_summary(result))}"
        for index, result in enumerate(results, start=(page - 1) * 25 + 1)
    ]
    embed = discord.Embed(
        title="Wikelo Contracts",
        description=_limit_lines(lines, 3900),
        color=discord.Color.gold(),
    )
    page_hint = f"Page {page}"
    if has_next:
        page_hint += " · More contracts available"
    embed.set_footer(text=f"{page_hint} · Select a contract below to view requirements")
    return embed


def build_wikelo_embed(result: WikeloMissionResult) -> discord.Embed:
    embed = discord.Embed(title=result.name, color=discord.Color(0xE4A63A), url=result.source_url or None)

    def format_items(items) -> str:
        lines = []
        for item in items:
            quantity = f"{item.quantity:g}" if isinstance(item.quantity, (int, float)) else str(item.quantity)
            lines.append(f"• {quantity}{' SCU' if item.unit == 'SCU' else '×'} {item.name}")
        return "\n".join(lines)[:1024]

    embed.add_field(name="Reward", value=format_items(result.rewards) or "Reward details unavailable", inline=False)
    embed.add_field(name="Turn In", value=format_items(result.requirements) or "No turn-in items listed", inline=False)
    if any(item.name == "Metamaterial Test #146" for item in result.requirements):
        embed.add_field(
            name="How to Get Metamaterial Test #146",
            value=(
                "Accept **Extra Special Wolf** to unlock its blueprint, then craft it with "
                "2 SCU Titanium (Q900+), 2 SCU Riccite (Q800+), and 4 Yormandi Eyes."
            ),
            inline=False,
        )
    embed.add_field(
        name="Wikelo Reputation Required",
        value=f"{result.reputation_required_name} ({result.reputation_required:g} rep)",
        inline=True,
    )
    embed.add_field(
        name="Wikelo Reputation Awarded",
        value=f"+{result.reputation_reward:g} rep" if result.reputation_reward is not None else "No Wikelo reputation awarded",
        inline=True,
    )
    embed.add_field(name="Availability", value="Released" if result.released else "Unreleased / verify in game", inline=True)
    embed.set_footer(text=" · ".join(filter(None, ["Wikelo Emporium", result.version])))
    return embed


def _format_approved_loot_sightings(sightings: list[dict]) -> str:
    if not sightings:
        return (
            "No community sighting has been approved for this item yet. "
            "Use `/loot found` to submit one."
        )
    lines = []
    for sighting in sightings[:5]:
        details = [sighting.get("celestial_body"), sighting.get("location_type"), sighting.get("game_version")]
        if sighting.get("confidence"):
            reports = int(sighting.get("report_count") or 0)
            details.append(
                f"{sighting['confidence']} confidence"
                + (f" ({reports} report{'s' if reports != 1 else ''})" if reports else "")
            )
        suffix = " · ".join(str(value) for value in details if value)
        timestamp = sighting.get("latest_confirmed_at") or sighting.get("reviewed_at")
        confirmed = f" · approved <t:{timestamp}:R>" if isinstance(timestamp, int) else ""
        lines.append(f"• **{sighting['location']}**{f' — {suffix}' if suffix else ''}{confirmed}")
    return "\n".join(lines)[:1024]


def build_loot_sighting_review_embed(report: dict) -> discord.Embed:
    status = str(report.get("status") or "pending")
    colors = {
        "pending": discord.Color.orange(),
        "approved": discord.Color.green(),
        "rejected": discord.Color.red(),
    }
    embed = discord.Embed(
        title=f"Loot Sighting #{report.get('id')} — {status.title()}",
        description=f"**{report.get('item_name', 'Unknown item')}**",
        color=colors.get(status, discord.Color.blurple()),
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(name="Location", value=str(report.get("location") or "Unknown")[:1024], inline=False)
    embed.add_field(name="Planet / Moon", value=report.get("celestial_body") or "Not supplied", inline=True)
    embed.add_field(name="Location Type", value=report.get("location_type") or "Not supplied", inline=True)
    embed.add_field(name="Game Version", value=report.get("game_version") or "Not supplied", inline=True)
    embed.add_field(
        name="Reported By",
        value=f"<@{report.get('reporter_id')}> · {report.get('reporter_name')}",
        inline=False,
    )
    if report.get("notes"):
        embed.add_field(name="Verification Notes", value=str(report["notes"])[:1024], inline=False)
    if report.get("screenshot_url"):
        embed.add_field(name="Evidence", value=f"[Open original screenshot]({report['screenshot_url']})", inline=False)
        embed.set_image(url=str(report["screenshot_url"]))
    if status != "pending":
        embed.add_field(
            name="Reviewed By",
            value=f"<@{report.get('reviewer_id')}> · {report.get('reviewer_name')}",
            inline=False,
        )
    embed.set_footer(
        text="Only Bot Managers can approve or reject. Approval publishes the location in item searches."
    )
    return embed


def _is_bot_manager(user: discord.User | discord.Member) -> bool:
    return isinstance(user, discord.Member) and any(
        role.name.casefold() == BOT_MANAGER_ROLE_NAME.casefold() for role in user.roles
    )


def _is_image_attachment(attachment: discord.Attachment) -> bool:
    if attachment.content_type and attachment.content_type.casefold().startswith("image/"):
        return True
    return Path(attachment.filename).suffix.casefold() in {".png", ".jpg", ".jpeg", ".webp", ".gif"}


def build_loot_command_example_embed() -> discord.Embed:
    embed = discord.Embed(
        title="Example /loot search Response",
        description=(
            "**Example command:** `/loot search name:ADP-mk4 Arms Justified`\n"
            "Searches the current local lootable-item catalog, then adds cached UEX pricing."
        ),
        color=discord.Color.gold(),
    )
    embed.add_field(
        name="ADP-mk4 Arms Justified",
        value=(
            "Lootable: Yes — game catalog flag\n"
            "Type: Arms · Category: Heavy · Clark Defense Systems\n"
            "Rarity and current game version are shown when available."
        ),
        inline=False,
    )
    embed.add_field(
        name="UEX Pricing",
        value=(
            "Player marketplace average and terminal sell average appear when available.\n"
            "Every result includes a **Live prices on UEX** link."
        ),
        inline=False,
    )
    embed.add_field(
        name="Location Confidence",
        value=(
            "Approved community sightings show their location, patch, type, and approval age. "
            "Use `/loot report` to submit a sighting with an optional screenshot."
        ),
        inline=False,
    )
    embed.add_field(
        name="Community Review",
        value=(
            "Reports go to the private audit-log review queue. Only members with the Bot Manager role "
            "can approve or reject them."
        ),
        inline=False,
    )
    embed.add_field(
        name="Privacy",
        value="Command results are visible only to the person who requested them.",
        inline=False,
    )
    embed.add_field(
        name="Try a few searches",
        value=(
            "`/loot search name:ADP-mk4 Arms Justified`\n"
            "`/loot search name:ADP-mk4 Core Justified`\n"
            "`/loot search name:ADP-mk4 Helmet Justified`"
        ),
        inline=False,
    )
    embed.set_footer(text="Example data only • The deployment refreshes this post without creating duplicates")
    return embed


def build_inventory_search_embed(
    results: list[dict],
    item: str | None = None,
    station: str | None = None,
    category: str | None = None,
    item_type: str | None = None,
    size: str | None = None,
) -> discord.Embed:
    filters = [
        _line("Item", item),
        _line("Station", station),
        _line("Category", category),
        _line("Type", item_type),
        _line("Size", size),
    ]
    filter_text = "\n".join(line for line in filters if line)
    lines: list[str] = []
    shown = 0
    for row in results:
        name = discord.utils.escape_markdown(str(row.get("name") or "Unknown item"))
        location = discord.utils.escape_markdown(str(row.get("location") or "Unknown location"))
        details = [row.get("category"), row.get("item_type")]
        if row.get("item_size"):
            details.append(f"Size {row['item_size']}")
        detail_text = " / ".join(discord.utils.escape_markdown(str(value)) for value in details if value)
        line = f"**{name}** × {_format_number(row.get('quantity'))}"
        if not station:
            line = f"{line} — {location}"
        if detail_text:
            line = f"{line}\n{detail_text}"
        candidate = "\n".join([*lines, line])
        if len(candidate) > 3600:
            break
        lines.append(line)
        shown += 1

    description_parts = [filter_text, "\n".join(lines)]
    embed = discord.Embed(
        title="Your Website Inventory",
        description="\n\n".join(part for part in description_parts if part),
        color=discord.Color.blurple(),
    )
    footer = f"Showing {shown} of {len(results)} matching item{'s' if len(results) != 1 else ''}. Results are private to you."
    embed.set_footer(text=footer)
    return embed


def _item_locator_result_label(result: ItemLocatorResult) -> str:
    details = [
        value
        for value in [result.section, result.category, _item_size_label(result.size)]
        if value
    ]
    return " | ".join(details) if details else "Item"


def _item_size_label(size: str | None) -> str | None:
    if not size:
        return None
    return f"Size {size}" if str(size).isdigit() else str(size)


def _format_item_purchase_locations(purchases: list[ItemPurchaseLocation]) -> str:
    if not purchases:
        return "No current in-game purchase locations found."

    lines = []
    for purchase in purchases[:25]:
        place = " / ".join(part for part in [purchase.system, purchase.planet, purchase.location] if part)
        if not place:
            place = "Unknown location"
        lines.append(
            f"{_format_currency(purchase.price, 'aUEC')} at {purchase.terminal_name} - {place}"
        )
    if len(purchases) > 25:
        lines.append(f"{len(purchases) - 25} more location(s) available in UEX.")
    return _limit_lines(lines, 1000)


def _format_blueprint_ingredients(ingredients: list[BlueprintIngredient]) -> str:
    if not ingredients:
        return "No material data found."

    lines = []
    for ingredient in ingredients:
        quantity = _format_number(ingredient.quantity) if ingredient.quantity is not None else "Unknown"
        unit = ingredient.unit or "SCU"
        slot = f" ({ingredient.slot.title()})" if ingredient.slot else ""
        lines.append(f"{ingredient.name}: {quantity} {unit}{slot}")
        for effect in ingredient.quality_effects or []:
            low = (effect.modifier_at_min - 1) * 100
            high = (effect.modifier_at_max - 1) * 100
            lines.append(
                f"  {effect.stat}: Q{effect.quality_min:g} {low:+g}% -> "
                f"Q{effect.quality_max:g} {high:+g}%"
            )
    return _limit_lines(lines, 1000)


def _parse_blueprint_qualities(value: str | None) -> dict[str, float]:
    if not value or not value.strip():
        return {}
    raw = value.strip()
    try:
        shared = float(raw)
    except ValueError:
        shared = None
    if shared is not None:
        if not 0 <= shared <= 1000:
            raise ValueError("Blueprint quality must be between 0 and 1000.")
        return {"*": shared}

    qualities = {}
    for entry in raw.replace(";", ",").split(","):
        if not entry.strip():
            continue
        if "=" not in entry:
            raise ValueError("Use `Material=quality`, for example `Titanium=750, Gold=800`.")
        material, number = (part.strip() for part in entry.split("=", 1))
        try:
            quality = float(number)
        except ValueError as error:
            raise ValueError(f"Quality for `{material}` must be a number from 0 to 1000.") from error
        if not material or not 0 <= quality <= 1000:
            raise ValueError("Every material quality must be between 0 and 1000.")
        qualities[_normalize_choice(material)] = quality
    return qualities


def _format_blueprint_quality_calculation(
    ingredients: list[BlueprintIngredient], quality_values: dict[str, float]
) -> str:
    lines = []
    for ingredient in ingredients:
        quality = quality_values.get(_normalize_choice(ingredient.name), quality_values.get("*"))
        if quality is None:
            continue
        amount = _format_number(ingredient.quantity) if ingredient.quantity is not None else "Unknown"
        lines.append(f"**{ingredient.name} - {amount} {ingredient.unit or 'SCU'} - Q{quality:g}**")
        for effect in ingredient.quality_effects or []:
            span = effect.quality_max - effect.quality_min
            progress = 1 if span == 0 else max(0, min(1, (quality - effect.quality_min) / span))
            modifier = effect.modifier_at_min + progress * (effect.modifier_at_max - effect.modifier_at_min)
            lines.append(f"{effect.stat}: x{modifier:.3f} ({(modifier - 1) * 100:+.1f}%)")
    return _limit_lines(lines, 1000) if lines else "No entered material names matched this blueprint."


def _blueprint_mission_lines(missions: list[BlueprintMission]) -> list[str]:
    groups = []
    group_indexes = {}
    for mission in missions:
        rep = mission.min_standing_name or "Unknown"
        if mission.min_standing_reputation is not None:
            rep = f"{rep} ({_format_number(mission.min_standing_reputation)} rep)"
        drop = _format_drop_chance(mission.drop_chance) or "Unknown"
        key = (
            mission.contractor or "Unknown",
            drop,
        )
        if key not in group_indexes:
            group_indexes[key] = len(groups)
            groups.append(
                {
                    "contractor": mission.contractor or "Unknown",
                    "rep": rep,
                    "rep_value": _mission_rep_value(mission),
                    "drop": drop,
                    "missions": [],
                    "seen_missions": set(),
                }
            )
        group = groups[group_indexes[key]]
        if _mission_rep_value(mission) < group["rep_value"]:
            group["rep"] = rep
            group["rep_value"] = _mission_rep_value(mission)

        mission_name = mission.name or "Unknown mission"
        if mission_name in group["seen_missions"]:
            continue
        group["seen_missions"].add(mission_name)
        group["missions"].append(mission_name)

    lines = []
    for group in groups:
        lines.append(
            " | ".join(
                [
                    f"- Contractor: {group['contractor']}",
                    f"Minimum Rep: {group['rep']}",
                    f"Drop Rate: {group['drop']}",
                ]
            )
        )
        for mission_name in group["missions"]:
            lines.append(f"  - {mission_name}")
    return lines


def _format_blueprint_missions(missions: list[BlueprintMission], page: int = 1) -> str:
    if not missions:
        return "No mission drop data found."

    lines = _blueprint_mission_lines(missions)
    start = max(0, page - 1) * BLUEPRINT_MISSION_LINES_PER_PAGE
    page_lines = lines[start : start + BLUEPRINT_MISSION_LINES_PER_PAGE]
    if not page_lines:
        return "No mission drop data found for this page."
    return _limit_lines(page_lines, 1000)


def _blueprint_mission_page_count(missions: list[BlueprintMission]) -> int:
    line_count = len(_blueprint_mission_lines(missions))
    return max(1, (line_count + BLUEPRINT_MISSION_LINES_PER_PAGE - 1) // BLUEPRINT_MISSION_LINES_PER_PAGE)


def _mission_rep_value(mission: BlueprintMission) -> float:
    if mission.min_standing_reputation is None:
        return float("inf")
    return float(mission.min_standing_reputation)


def _format_drop_chance(value: int | float | None) -> str | None:
    if value is None:
        return None
    percent = float(value) * 100 if float(value) <= 1 else float(value)
    return f"{_format_number(percent)}%"


def build_trade_route_embed(
    result: TradeRouteResult,
    starting_point: str,
    max_stops: int,
    stay_system: str | None = None,
) -> discord.Embed:
    loop_line = (
        "Loop: each sell stop is the next buy stop, and the final sell stop returns to the start."
        if not result.requires_empty_return_to_start
        else "Loop: trade legs are chained, then return empty to the starting point because UEX does not list it as a buyer."
    )
    description = [
        _line("Ship", result.ship),
        _line("Starting Point", starting_point),
        _line("Cargo", f"{_format_number(result.cargo_capacity_scu)} SCU"),
        _line("Starting Cash", _format_currency(result.investment, "aUEC")),
        _line("Max Stops", str(max_stops)),
        _line("Estimated Loop Profit", _format_currency(_trade_route_total_profit(result), "aUEC")),
        _line("Estimated Ending Cash", _format_currency(_trade_route_ending_cash(result), "aUEC")),
        _line("Stay In System", stay_system),
        loop_line,
    ]
    embed = discord.Embed(
        title="Circular Route",
        description="\n".join(line for line in description if line),
        color=discord.Color.teal(),
    )

    for index, leg in enumerate(result.legs, start=1):
        embed.add_field(
            name=f"Leg {index}: {leg.commodity_name} - {_format_currency(leg.profit, 'aUEC')} profit",
            value=_format_trade_route_leg(leg),
            inline=False,
        )

    if result.requires_empty_return_to_start and result.legs:
        final_location = _format_route_location(
            result.legs[-1].sell_system,
            result.legs[-1].sell_planet,
            result.legs[-1].sell_location,
            result.legs[-1].sell_terminal,
        )
        embed.add_field(
            name="Return",
            value=f"Fly empty from {final_location} back to {starting_point}.",
            inline=False,
        )

    embed.set_footer(text=f"Source: {result.source_name} average prices, stock, and demand")
    return embed


def _trade_route_total_profit(result: TradeRouteResult) -> float:
    return sum(float(leg.profit) for leg in result.legs)


def _trade_route_ending_cash(result: TradeRouteResult) -> float:
    return float(result.investment) + _trade_route_total_profit(result)


def _format_trade_route_leg(leg: TradeRouteLeg) -> str:
    buy_location = _format_route_location(leg.buy_system, leg.buy_planet, leg.buy_location, leg.buy_terminal)
    sell_location = _format_route_location(leg.sell_system, leg.sell_planet, leg.sell_location, leg.sell_terminal)
    return (
        f"Buy: {_format_currency(leg.buy_price, 'aUEC')}/SCU at {buy_location}\n"
        f"Sell: {_format_currency(leg.sell_price, 'aUEC')}/SCU at {sell_location}\n"
        f"Quantity: {_format_number(leg.quantity_scu)} SCU | "
        f"Cost: {_format_currency(leg.investment_used, 'aUEC')}"
    )


def _format_route_location(
    system: str | None,
    planet: str | None,
    location: str | None,
    terminal: str,
) -> str:
    parts = [part for part in [system, planet, location or terminal] if part]
    return " / ".join(parts) or terminal


def build_commands_reference_embeds(settings: Settings | None = None) -> list[discord.Embed]:
    reference_path = Path("docs/commands.md")
    markdown = reference_path.read_text(encoding="utf-8").strip()
    if markdown.startswith("# Discord Bot Commands"):
        markdown = markdown.removeprefix("# Discord Bot Commands").strip()

    embeds = []
    if settings and settings.command_channel_ids:
        embeds.append(build_command_channel_directory_embed(settings))

    sections = _command_reference_sections(markdown)
    for command_name, body in sections:
        chunks = _chunk_text(body, 3500)
        for index, chunk in enumerate(chunks):
            title = f"Discord Bot Commands - {command_name}"
            if len(chunks) > 1:
                title = f"{title} ({index + 1}/{len(chunks)})"
            embed = discord.Embed(
                title=title,
                description=chunk,
                color=discord.Color.blurple(),
            )
            embed.set_footer(text="Auto-updated from docs/commands.md when the bot starts")
            embeds.append(embed)

    return embeds


def _message_embed_matches(message: discord.Message, embed: discord.Embed) -> bool:
    if len(message.embeds) != 1:
        return False
    return message.embeds[0].to_dict() == embed.to_dict()


def build_command_channel_directory_embed(settings: Settings) -> discord.Embed:
    channel_commands: dict[int, list[str]] = {}
    for command_name, channel_id in settings.command_channel_ids.items():
        channel_commands.setdefault(channel_id, []).append(f"/{command_name}")
    blueprint_channel = settings.command_channel_ids.get("blueprint")
    if blueprint_channel and "mission" not in settings.command_channel_ids:
        blueprint_commands = channel_commands.setdefault(blueprint_channel, [])
        if "/mission" not in blueprint_commands:
            blueprint_commands.append("/mission")
        if "/myblueprints" not in blueprint_commands:
            blueprint_commands.append("/myblueprints")
    inventory_commands = channel_commands.setdefault(INVENTORY_CHANNEL_ID, [])
    if "/inventory search" not in inventory_commands:
        inventory_commands.append("/inventory search")
    loot_commands = channel_commands.setdefault(LOOT_CHANNEL_ID, [])
    if "/loot search" not in loot_commands:
        loot_commands.append("/loot search")

    lines = []
    for channel_id, command_names in sorted(channel_commands.items(), key=lambda item: min(item[1])):
        commands = ", ".join(sorted(command_names))
        lines.append(f"<#{channel_id}>: {commands}")

    embed = discord.Embed(
        title="Discord Bot Commands - Channel Directory",
        description="\n".join(lines) if lines else "No command channel restrictions configured.",
        color=discord.Color.blurple(),
    )
    embed.set_footer(text="Channel names are rendered by Discord from the configured channel IDs")
    return embed


def build_visitor_channel_directory_embed(visitor_channels: dict[str, int]) -> discord.Embed:
    grouped: dict[str, list[str]] = {}
    for command_name, channel_name in VISITOR_COMMAND_CHANNELS.items():
        if channel_name in visitor_channels:
            grouped.setdefault(channel_name, []).append(f"/{command_name}")
    lines = [
        f"<#{visitor_channels[channel_name]}>: {', '.join(sorted(commands))}"
        for channel_name, commands in grouped.items()
    ]
    embed = discord.Embed(
        title="Discord Bot Commands - Channel Directory",
        description="\n".join(lines) if lines else "No command channels are available.",
        color=discord.Color.blurple(),
    )
    embed.set_footer(text="Each command is limited to its listed channel")
    return embed


def _command_reference_sections(markdown: str) -> list[tuple[str, str]]:
    sections: list[tuple[str, str]] = []
    current_title: str | None = None
    current_lines: list[str] = []

    for line in markdown.splitlines():
        if line.startswith("## "):
            if current_title is not None:
                sections.append((current_title, "\n".join(current_lines).strip()))
            current_title = line.removeprefix("## ").strip()
            current_lines = []
            continue

        if current_title is not None:
            current_lines.append(line)

    if current_title is not None:
        sections.append((current_title, "\n".join(current_lines).strip()))

    return sections


def _chunk_text(text: str, max_length: int) -> list[str]:
    paragraphs = text.split("\n\n")
    chunks: list[str] = []
    current = ""

    for paragraph in paragraphs:
        candidate = f"{current}\n\n{paragraph}".strip() if current else paragraph
        if len(candidate) <= max_length:
            current = candidate
            continue

        if current:
            chunks.append(current)
        current = paragraph

    if current:
        chunks.append(current)

    return chunks


def _format_markets(markets: list[CommodityMarket]) -> str:
    if not markets:
        return "No current locations found."

    lines = []
    for index, market in enumerate(markets, start=1):
        system = market.system or "Unknown"
        planet = market.planet or "Unknown"
        location = market.location or market.terminal_name
        demand = f"{_format_number(market.demand)} SCU" if market.demand is not None else "Unknown"
        line = (
            f"{index}. {_format_currency(market.price, 'aUEC')}/SCU avg | "
            f"System: {system} | Planet: {planet} | Location: {location} | Demand: {demand}"
        )
        if market.updated_at:
            line += f" | Updated: <t:{market.updated_at}:R>"
        candidate = "\n".join([*lines, line])
        if len(candidate) > 1000:
            lines.append("More locations available in UEX.")
            break
        lines.append(line)
    return "\n".join(lines)


def _format_location_group(locations: list[str]) -> str:
    if not locations:
        return "No matching locations found."

    lines = []
    for location in locations:
        candidate = "\n".join([*lines, location])
        if len(candidate) > 1000:
            lines.append("More locations available in UEX.")
            break
        lines.append(location)
    return "\n".join(lines)


def _has_mining_multi_separator(value: str) -> bool:
    return bool(re.search(r"\s*(,|;|\+|&|\band\b)\s*", value, flags=re.IGNORECASE))


def _mining_multi_search_terms(value: str) -> list[str]:
    if not _has_mining_multi_separator(value):
        return [value.strip()] if value.strip() else []
    return [
        term.strip()
        for term in re.split(r"\s*(?:,|;|\+|&|\band\b)\s*", value, flags=re.IGNORECASE)
        if term.strip()
    ]


def _mining_space_search_terms(value: str) -> list[str]:
    return [term.strip() for term in value.split() if term.strip()]


def _mining_autocomplete_prefix(value: str) -> tuple[str, str]:
    match = re.search(r"^(?P<prefix>.*(?:,|;|\+|&|\band\b)\s*)(?P<partial>[^,;+&]*)$", value, flags=re.IGNORECASE)
    if match is None:
        return "", value
    return match.group("prefix"), match.group("partial")


def _mining_term_signatures(result: MiningLocationResult, term: str) -> list[int]:
    signatures = result.rock_signatures or []
    signature = _mining_signature_number(term)
    if signature is None:
        return signatures
    return [
        base_signature
        for base_signature in signatures
        if _mining_signature_matches_cluster(signature, base_signature)
    ]


def _mining_signature_number(value: object) -> int | None:
    text = str(value or "").replace(",", "").strip()
    return int(text) if text.isdigit() else None


def _mining_signature_matches_cluster(signature: int, base_signature: int) -> bool:
    return signature == base_signature or (signature % base_signature == 0 and 1 <= signature // base_signature <= 6)


def _shared_mining_signatures(signature_groups: list[list[int]]) -> list[int]:
    if not signature_groups or any(not signatures for signatures in signature_groups):
        return []
    shared = set(signature_groups[0])
    for signatures in signature_groups[1:]:
        shared.intersection_update(signatures)
    return sorted(shared)


def _unique_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for value in values:
        key = _normalize_text(value)
        if key in seen:
            continue
        seen.add(key)
        unique.append(value)
    return unique


async def add_community_mining_location(cache: SQLiteCache, entry: dict) -> None:
    entries = await _community_mining_entries(cache)
    key = _normalize_text(entry.get("material"))
    material_entries = entries.setdefault(key, [])
    new_entry = {
        "material": str(entry.get("material") or "").strip(),
        "system": str(entry.get("system") or "").strip(),
        "location_type": str(entry.get("location_type") or "").strip(),
        "location": str(entry.get("location") or "").strip(),
        "reported_by": str(entry.get("reported_by") or "").strip(),
    }
    duplicate = any(
        _normalize_text(existing.get("system")) == _normalize_text(new_entry["system"])
        and _normalize_text(existing.get("location_type")) == _normalize_text(new_entry["location_type"])
        and _normalize_text(existing.get("location")) == _normalize_text(new_entry["location"])
        for existing in material_entries
        if isinstance(existing, dict)
    )
    if not duplicate:
        material_entries.append(new_entry)
    await cache.set(MINING_COMMUNITY_LOCATIONS_CACHE_KEY, entries, 315360000)


async def apply_community_mining_locations(
    cache: SQLiteCache,
    result: MiningLocationResult,
) -> MiningLocationResult:
    entries = await _community_mining_entries(cache)
    material_entries = entries.get(_normalize_text(result.material_name), [])
    if not material_entries:
        return result

    groups_by_system: dict[str, MiningSystemLocations] = {
        _normalize_text(group.system): group
        for group in result.location_groups or []
    }
    groups = list(result.location_groups or [])
    systems = list(result.systems)
    lagrange_points = list(result.lagrange_points)
    planets = list(result.planets)
    moons = list(result.moons)
    points_of_interest = list(result.points_of_interest)

    for entry in material_entries:
        if not isinstance(entry, dict):
            continue
        system = str(entry.get("system") or "").strip()
        location_type = str(entry.get("location_type") or "").strip()
        location = str(entry.get("location") or "").strip()
        if not system or location_type not in _mining_location_type_labels() or not location:
            continue

        system_key = _normalize_text(system)
        if system_key not in groups_by_system:
            group = MiningSystemLocations(system=system, lagrange_points=[], planets=[], moons=[], points_of_interest=[])
            groups_by_system[system_key] = group
            groups.append(group)
        group = groups_by_system[system_key]
        _append_unique(getattr(group, location_type), f"{location} (Community)")
        _append_unique(systems, system)
        _append_unique(
            {
                "lagrange_points": lagrange_points,
                "planets": planets,
                "moons": moons,
                "points_of_interest": points_of_interest,
            }[location_type],
            f"{location} (Community)",
        )

    return MiningLocationResult(
        material_name=result.material_name,
        code=result.code,
        kind=result.kind,
        refined_sell_price=result.refined_sell_price,
        raw_sell_price=result.raw_sell_price,
        is_harvestable=result.is_harvestable,
        is_volatile_qt=result.is_volatile_qt,
        is_volatile_time=result.is_volatile_time,
        is_explosive=result.is_explosive,
        systems=systems,
        lagrange_points=lagrange_points,
        planets=planets,
        moons=moons,
        points_of_interest=points_of_interest,
        source_url=result.source_url,
        source_name=result.source_name,
        location_basis=result.location_basis,
        rock_signatures=result.rock_signatures or [],
        location_groups=groups,
    )


async def _community_mining_entries(cache: SQLiteCache) -> dict:
    entries = await cache.get(MINING_COMMUNITY_LOCATIONS_CACHE_KEY)
    return entries if isinstance(entries, dict) else {}


def _mining_location_type_labels() -> dict[str, str]:
    return {
        "lagrange_points": "Lagrange Points",
        "planets": "Planets",
        "moons": "Moons",
        "points_of_interest": "Points of Interest",
    }


def _append_unique(values: list[str], value: str) -> None:
    if all(_normalize_text(existing) != _normalize_text(value) for existing in values):
        values.append(value)


def _mining_location_lines(result: MiningLocationResult) -> list[str]:
    groups = result.location_groups or []
    if not groups and result.systems:
        groups = [
            type(
                "MiningLocationGroup",
                (),
                {
                    "system": "All Systems",
                    "lagrange_points": result.lagrange_points,
                    "planets": result.planets,
                    "moons": result.moons,
                    "points_of_interest": result.points_of_interest,
                },
            )()
        ]

    lines: list[str] = []
    for group in groups:
        detail_lines = [
            _mining_location_detail_line("Lagrange Points", group.lagrange_points),
            _mining_location_detail_line("Planets", group.planets),
            _mining_location_detail_line("Moons", group.moons),
            _mining_location_detail_line("Points of Interest", group.points_of_interest),
        ]
        detail_lines = [line for line in detail_lines if line]
        if not detail_lines:
            continue
        if lines:
            lines.append("")
        lines.append(f"**{group.system}**")
        lines.extend(detail_lines)

    return lines or ["No matching locations found."]


def _mining_system_group_has_locations(group: MiningSystemLocations) -> bool:
    return any([group.lagrange_points, group.planets, group.moons, group.points_of_interest])


def _mining_result_for_system(result: MiningLocationResult, system: str) -> MiningLocationResult:
    normalized_system = _normalize_text(system)
    group = next(
        (
            group
            for group in result.location_groups or []
            if _normalize_text(group.system) == normalized_system
        ),
        None,
    )
    if group is None:
        return result

    return MiningLocationResult(
        material_name=result.material_name,
        code=result.code,
        kind=result.kind,
        refined_sell_price=result.refined_sell_price,
        raw_sell_price=result.raw_sell_price,
        is_harvestable=result.is_harvestable,
        is_volatile_qt=result.is_volatile_qt,
        is_volatile_time=result.is_volatile_time,
        is_explosive=result.is_explosive,
        systems=[group.system],
        lagrange_points=group.lagrange_points,
        planets=group.planets,
        moons=group.moons,
        points_of_interest=group.points_of_interest,
        source_url=result.source_url,
        source_name=result.source_name,
        location_basis=result.location_basis,
        rock_signatures=result.rock_signatures,
        location_groups=[group],
    )


def _mining_location_detail_line(label: str, locations: list[str]) -> str | None:
    if not locations:
        return None
    return f"{label}: {', '.join(locations)}"


def _normalize_text(value: object) -> str:
    return " ".join(str(value or "").lower().replace("-", " ").split())


def _mining_location_pages(result: MiningLocationResult) -> list[list[str]]:
    lines = _mining_location_lines(result)
    pages = [
        lines[index : index + MINING_LOCATION_LINES_PER_PAGE]
        for index in range(0, len(lines), MINING_LOCATION_LINES_PER_PAGE)
    ]
    return pages or [["No matching locations found."]]


def _mining_location_page_count(result: MiningLocationResult) -> int:
    return len(_mining_location_pages(result))


def _format_mining_location_page(result: MiningLocationResult, page: int = 1) -> str:
    pages = _mining_location_pages(result)
    page = max(1, min(page, len(pages)))
    return _limit_lines(pages[page - 1], 1000)


def _format_mining_flags(result: MiningLocationResult) -> str | None:
    flags = []
    if result.is_harvestable:
        flags.append("Harvestable")
    if result.is_volatile_qt:
        flags.append("QT sensitive")
    if result.is_volatile_time:
        flags.append("Time sensitive")
    if result.is_explosive:
        flags.append("Explosive")
    return ", ".join(flags) if flags else None


def _format_rock_signatures(signatures: list[int] | None) -> str:
    if not signatures:
        return "No rock signature data found."

    lines = []
    for signature in signatures[:8]:
        clusters = [f"{count}x {_format_number(signature * count)}" for count in range(1, 7)]
        lines.append(f"{_format_number(signature)}: {' | '.join(clusters)}")
    if len(signatures) > 8:
        lines.append("Additional signatures are available from the Star Citizen Wiki.")
    return _limit_lines(lines, 1000)


def _format_mining_signature_block(signatures: list[int] | None) -> str:
    return f"Rock Signatures:\n{_format_rock_signatures(signatures)}"


def _format_commodity_estimate(result: CommodityResult, quantity_scu: float) -> str:
    lines = []
    if result.buy_from:
        purchase = result.buy_from[0]
        lines.append(
            f"Estimated buy cost: {_format_currency(purchase.price * quantity_scu, 'aUEC')} "
            f"at {purchase.location or purchase.terminal_name}"
        )
    else:
        lines.append("Estimated buy cost: No purchase location found.")

    if result.sell_to:
        sale = result.sell_to[0]
        lines.append(
            f"Estimated sell payout: {_format_currency(sale.price * quantity_scu, 'aUEC')} "
            f"at {sale.location or sale.terminal_name}"
        )
    else:
        lines.append("Estimated sell payout: No sell location found.")

    return "\n".join(lines)


def _format_pledge(result: ShipResult) -> str:
    pledge = result.pledge
    if pledge is None:
        return "No pledge store data found."

    lines = []
    if pledge.is_on_sale is True:
        lines.append("Availability: Available")
    elif pledge.is_on_sale is False:
        lines.append("Availability: Not currently listed as on sale")
    else:
        lines.append("Availability: Unknown")

    if pledge.price is not None:
        lines.append(f"Pledge price: {_format_currency(pledge.price, pledge.currency)}")
    if pledge.warbond_price is not None:
        lines.append(f"Warbond: {_format_currency(pledge.warbond_price, pledge.currency)}")
    if pledge.package_price is not None:
        lines.append(f"Package: {_format_currency(pledge.package_price, pledge.currency)}")
    if pledge.pledge_url:
        lines.append(f"[Open pledge page]({pledge.pledge_url})")
    else:
        lines.append("[Open RSI pledge store](https://robertsspaceindustries.com/en/pledge)")

    return "\n".join(lines)


def _format_purchases(result: ShipResult) -> str:
    if not result.purchases:
        return "No in-game purchase locations found."

    lines = []
    for purchase in result.purchases:
        terminal = purchase.terminal_name
        if purchase.uex_link:
            terminal = f"[{terminal}]({purchase.uex_link})"
        location = f" - {purchase.location}" if purchase.location else ""
        lines.append(f"{_format_currency(purchase.price, 'aUEC')} at {terminal}{location}")
    return "\n".join(lines)


def _line(label: str, value: str | None) -> str | None:
    return f"{label}: {value}" if value else None


def _format_currency(value: int | float, currency: str) -> str:
    amount = _format_number(value)
    if currency == "aUEC":
        return f"{amount} aUEC"
    if currency == "USD":
        return f"${amount} USD"
    return f"{amount} {currency}"


def _format_number(value: int | float | None) -> str:
    if value is None:
        return "Unknown"
    if isinstance(value, float) and not value.is_integer():
        return f"{value:,.2f}"
    return f"{int(value):,}"


def _format_seconds_duration(seconds: int | None) -> str | None:
    if seconds is None:
        return None
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} min"
    hours = minutes // 60
    remaining_minutes = minutes % 60
    return f"{hours} hr {remaining_minutes} min" if remaining_minutes else f"{hours} hr"


def _limit_lines(lines: list[str], max_length: int) -> str:
    kept = []
    for line in lines:
        candidate = "\n".join([*kept, line])
        if len(candidate) > max_length:
            kept.append("More available.")
            break
        kept.append(line)
    return "\n".join(kept)


def _focused_option_name(interaction: discord.Interaction) -> str:
    def find_focused(options: list[dict]) -> str | None:
        for option in options:
            if option.get("focused"):
                return str(option.get("name") or "")
            nested = option.get("options")
            if isinstance(nested, list):
                focused = find_focused(nested)
                if focused:
                    return focused
        return None

    data = interaction.data if isinstance(interaction.data, dict) else {}
    options = data.get("options")
    if not isinstance(options, list):
        return ""
    return find_focused(options) or ""


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    install_secret_redaction()


async def main() -> None:
    configure_logging()
    settings = Settings.from_env()
    cache = await SQLiteCache.create(settings.database_path)
    sources = await build_default_registry(settings, cache)

    async with GameAssistBot(settings, cache, sources) as bot:
        await bot.start(settings.discord_token)


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
