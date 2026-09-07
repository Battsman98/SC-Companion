# In-Game Assistance Discord Bot

Python Discord bot for collecting information from approved websites/APIs and serving it through Discord commands for in-game assistance.

## Features

- Slash command-ready Discord bot using `discord.py`
- Environment-based secrets through `.env`
- Star Citizen lookup support through Star Citizen Wiki data
- Website/API source abstraction for adding more game data providers
- SQLite cache to avoid repeated website requests
- VPS-friendly `systemd` service example

## Discord Commands

- `/status` checks whether the bot is online.
- `/lookup` searches Star Citizen game information.
- `/ship` looks up a Star Citizen ship or vehicle.
- `/commodity` looks up commodity pricing and locations.
- `/industry split`, `/industry refinery`, and `/industry brief` plan crew payouts, completion times, and operation posts.
- `/blueprint` searches crafting blueprints, materials, missions, and rep requirements.
- `/mission` searches missions by name, region, reputation giver, reputation level, and type.
- `/wikelo` searches Wikelo rewards and shows the mission name and exact turn-in requirements.
- `/trade routing` calculates UEX-based circular trade route candidates.
- The configured trading forum automatically requires WTS, WTB, or WTT and adds Wiki item details and imagery to new listings.
- `/trade listing` provides catalog-backed item-name suggestions and creates the correctly named forum listing with its tag and aUEC price.
- `/trade store` imports either a view-only Google Sheet or an Inventory Scanner `.xlsx` export into a STORE forum post. Google Sheets synchronize once daily (or immediately with `/trade store-refresh`); uploaded workbooks preserve scanner quantities and selling costs as a snapshot.
- `/exec` shows the Executive Hangar clock.
- `/execset` corrects the Executive Hangar clock for approved users.
- `/execclear` clears an Executive Hangar manual override.
- `/cztimer` starts a contested-zone helper countdown.

See `docs/commands.md` for the full command reference.

Set `COMMANDS_CHANNEL_ID` in `.env` to have the bot auto-post/update the command reference in a Discord channel on startup.

Set `COMMAND_CHANNEL_IDS` in `.env` to restrict commands to specific Discord channels. Use command names without the leading slash:

```env
COMMAND_CHANNEL_IDS=ship:111111111111111111,commodity:222222222222222222,trade routing:333333333333333333,item locator:444444444444444444
```

Commands not listed in `COMMAND_CHANNEL_IDS` can be used in any channel.

Set `AUDIT_LOG_CHANNEL_ID` in `.env` to post a remote audit view of command usage, blocked command attempts, and manual changes such as Executive Hangar corrections, CZ timer updates, and community mining location additions.

On startup, the bot also creates private `website-changelog` and `discord-changelog` channels under the `audit log` category (`1516295744603164732`). Discord worker deployments are recorded automatically, and the running worker monitors the website health revision once per minute so website-only deployments are recorded without restarting Discord. Each recorded revision is announced at most once even if either service restarts, and each entry includes the commit subject as a brief description. Bot Managers can read these channels; only the bot can post. Changelog posts are silent and do not mention an owner, role, or `@everyone`.

Every command-focused channel in the Discord Bot Hub also receives a durable example embed showing the command syntax and representative response data. These examples are refreshed on startup without duplicating posts. In the Executive Hangar and Contested Zone channels, the example appears first and the live auto-updating dashboard is posted directly below it. The bot automatically removes a duplicate legacy `Visitor Bot Hub`, preserving unique channels by moving them into `Discord Bot Hub` and deleting only duplicate managed channels.

The Discord Bot Hub is the sole public command area. On startup, known bot-managed text channels under the `Star Citizen` category are removed, while unrelated discussion channels are preserved. Each public command is accepted only in its associated Hub channel; for example, `/ship` is limited to `ship-search`, trade commands to `trade-tools`, and `/mining` plus `/miningadd` to `mining-tools`.

The `@everyone` role can view and interact with the bot throughout `Discord Bot Hub`, including its text, thread, forum, and voice features. Individual slash commands remain restricted to their associated Hub channels.

The Hub also contains a read-only `member-applications` embed with an Apply button. Visitors answer two private button-based questions, then enter their RSI handle and the Star Citizen organizations they belong to in a private details modal. Applicants with no organizations can enter `None`. Completed applications are posted to `membership-application-reviews`, which only the server owner and bot can access. Approving an application replaces the applicant's `Visitor` role with the server's existing `Members` role; denying it leaves their roles unchanged. The bot never creates a replacement membership role.

Set `UEX_API_TOKEN` to the bearer token from your registered UEX API application. The token is used only by the Python backend for documented `api.uexcorp.uk` requests and must not be exposed to browser JavaScript or committed to source control. Mining locations and signatures use the public Star Citizen Wiki API, which does not require a token.

Set `EXEC_STATUS_CHANNEL_ID` in `.env` to have the bot keep a public Executive Hangar status message updated every 60 seconds.

Set comma-separated `EXEC_ADMIN_ROLE_IDS` in `.env` to restrict change commands to specific Discord roles. This includes `/execset`, `/execclear`, and `/miningadd`.

Set comma-separated `BOT_ADMIN_ROLE_IDS` and/or `BOT_ADMIN_USER_IDS` in `.env` to restrict bot management and audit commands. This includes `/admin channels`, `/admin health`, and `/audit recent`. If no admin role IDs or user IDs are configured, users with Manage Server can use those commands.

Set `CZ_TIMERS_CHANNEL_ID` in `.env` to have the bot keep a public Contested Zone timer dashboard with clickable start/reset buttons.

The migration preserves hangar, blueprint, inventory, timer, audit, and website
analytics data. Do not run the local bot and Render worker with the same token at
the same time after cutover. The old SQLite file is not deleted and remains a
rollback copy.

### Database storage controls

The production database remains capped at 1 GB and storage autoscaling is
disabled in `render.yaml`. The web service logs database usage and its five
largest tables hourly, warns at 80% usage, and exposes the same safe sizing data
under `database` in `/api/health`. Set `DATABASE_STORAGE_LIMIT_BYTES` if the
configured Render capacity changes.

Inventory scanner diagnostics retain OCR and timing metadata for six hours but
do not persist uploaded image bytes. On PostgreSQL startup, the transient
diagnostics table is truncated to immediately release legacy image storage.
Hourly maintenance removes expired cache entries and scanner
diagnostics, website visit aggregates older than 90 days, and language samples
older than 180 days. User-owned hangar, inventory, blueprint, refinery, and loot
records are not removed by storage maintenance.

## GitHub Notes

Never commit `.env` or bot tokens. Commit `.env.example` instead.

See `docs/development-flow.md` for the local-to-GitHub-to-VPS workflow.
