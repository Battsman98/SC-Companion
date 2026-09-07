# Multi-Server Bot Management

SC Companion can be installed in multiple Discord servers while the original community server keeps its existing configuration.

## Server manager setup

1. Sign out of `sccompanion.org`, then sign back in with Discord. The new login requests permission to read the list of servers you manage; it does not grant the website permission to change those servers.
2. Open **Bot Management**.
3. Choose a server where you are the owner or have Discord's **Manage Server** permission.
4. If needed, select **Invite Bot** and approve the Discord installation.
5. Return to the panel and select **Refresh**.
6. Enable the desired modules and optionally select one command channel for each module.
7. Select **Save Bot Settings**.

Choosing **Any channel** allows that module throughout the server wherever the bot can read and reply. Choosing a channel restricts the module to that channel.

## Available modules

- Ship Search
- Mining Tools
- Blueprints
- Missions & Wikelo
- Item Locator
- Inventory Search
- Trade Tools

The primary SC Companion server retains its existing environment-based command routing until its configuration is saved in the panel. Commands that have not been made multi-server-safe—including shared timers and primary-server administration—remain unavailable in secondary servers.

## Security model

- The server list comes from Discord OAuth and is stored server-side, not in the browser cookie.
- Configuration endpoints require a signed-in user.
- Every save verifies the user's current membership and Manage Server/Administrator permission directly with Discord.
- Selected channels must exist in that server and must be text, announcement, forum, or media channels.
- API credentials and the Discord bot token remain server-side.

## Operations

Slash commands are registered globally so new installations can use them. Discord can take time to propagate changes to global commands. The configured primary server also receives a guild-specific synchronization for faster development updates.

Before operating in more than 100 Discord servers, complete Discord application verification and obtain approval for any privileged intents the bot still requires, including Server Members Intent.

The settings are stored in `guild_bot_settings`. Manageable-server choices captured at login are stored in `user_managed_guilds` and refreshed whenever the user signs in again.
