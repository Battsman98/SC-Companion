"""Peep-approved broadcasts delivered by SC Companion through the shared database."""
import json
import logging

import discord
from discord import app_commands

def preview(draft):
    embed = discord.Embed(title=f"Project {draft['phase']}: {draft['project']}",
                          description=draft['body'], color=0x3498DB)
    embed.set_footer(text=f"SC Companion • Announcement {draft['id']}")
    return embed



from src.broadcast_store import BroadcastStore


def store(interaction):
    return BroadcastStore(interaction.client.cache._connection)


def permitted(interaction):
    settings = interaction.client.settings
    return (settings.runtime_profile == 'peep'
            and bool(settings.discord_guild_id)
            and interaction.guild_id == settings.discord_guild_id
            and interaction.user.id == interaction.guild.owner_id)


class BroadcastCommands(app_commands.Group):
    async def interaction_check(self, interaction):
        if permitted(interaction):
            if interaction.command.name == "channel":
                return True
            from src.broadcast_room import room_key
            saved = await interaction.client.cache.get(room_key(interaction.guild_id)) or {}
            if saved.get("channel_id") == interaction.channel_id:
                return True
            await interaction.response.send_message(
                "Use the private development-announcements channel under audit log. Run /broadcast channel to create or repair it.", ephemeral=True)
            return False
        await interaction.response.send_message('Only the home-server owner can manage broadcasts through Peep in its home Discord.', ephemeral=True)
        return False


broadcast_group = BroadcastCommands(name='broadcast', description='Privately draft and approve development announcements through Peep.', guild_only=True)


def destinations(draft):
    targets = json.loads(draft['targets'])
    summary = '\n'.join(f'Server `{guild}` → <#{channel}>' for guild, channel in targets[:10])
    return f'{len(targets)} opted-in server(s)\n{summary}' + ('\nUse /broadcast recipients for the full list.' if len(targets) > 10 else '')


@broadcast_group.command(name='draft', description='Prepare an immutable announcement and its opted-in audience.')
@app_commands.choices(phase=[app_commands.Choice(name='New project / started', value='started'), app_commands.Choice(name='Project completed', value='completed')])
async def draft_command(interaction: discord.Interaction, project: str, phase: str, update: str):
    db = store(interaction)
    try:
        key = db.create(interaction.guild_id, project, phase, update,
                        db.targets(interaction.client.settings.award_test_guild_id, getattr(interaction.client.settings, "announcement_testing_only", True)), interaction.user.id)
    except ValueError as error:
        await interaction.response.send_message(str(error), ephemeral=True)
        return
    draft = db.get(interaction.guild_id, key)
    await interaction.response.send_message(f"Draft only. Recipients:\n{destinations(draft)}\nReview, then `/broadcast approve {key}`. To change it, reject and create a new draft.", embed=preview(draft), ephemeral=False, allowed_mentions=discord.AllowedMentions.none())


@broadcast_group.command(name='review', description='Review the saved text, recipients, and delivery receipts.')
async def review_command(interaction: discord.Interaction, draft_id: str):
    db = store(interaction)
    draft = db.get(interaction.guild_id, draft_id)
    if not draft:
        await interaction.response.send_message('Broadcast not found.', ephemeral=True)
        return
    receipts = db.db.execute('SELECT guild_id,state,message_id,error FROM development_deliveries WHERE broadcast_id=?', (draft_id,)).fetchall()
    status = '\n'.join(f'{guild}: {state}' + (f' (message {message})' if message else '') + (f' — {error}' if error else '') for guild,state,message,error in receipts)
    await interaction.response.send_message(f"Status: {draft['state']}\n{destinations(draft)}\n{status}"[:1900], embed=preview(draft), ephemeral=False, allowed_mentions=discord.AllowedMentions.none())


@broadcast_group.command(name='pending', description='List recent broadcasts, including drafts and approved deliveries.')
async def pending_command(interaction: discord.Interaction):
    rows = store(interaction).db.execute('SELECT id,project,state FROM development_broadcasts WHERE source_guild_id=? ORDER BY created_at DESC LIMIT 12', (interaction.guild_id,)).fetchall()
    await interaction.response.send_message('\n'.join(f'`{key}` — {project[:60]} ({state})' for key,project,state in rows) or 'No broadcasts.', ephemeral=False, allowed_mentions=discord.AllowedMentions.none())


@broadcast_group.command(name='approve', description='Approve the saved text and recipients for SC Companion delivery.')
async def approve_command(interaction: discord.Interaction, draft_id: str):
    changed = store(interaction).decide(interaction.guild_id, draft_id, 'approved', interaction.user.id)
    await interaction.response.send_message('Approved. SC Companion will deliver within about one minute. Use /broadcast review for receipts.' if changed else 'No pending draft with that ID. Already handled broadcasts are never queued twice.', ephemeral=not changed)


@broadcast_group.command(name='reject', description='Discard an unapproved broadcast.')
async def reject_command(interaction: discord.Interaction, draft_id: str):
    changed = store(interaction).decide(interaction.guild_id, draft_id, 'rejected', interaction.user.id)
    await interaction.response.send_message('Draft rejected.' if changed else 'No pending draft with that ID.', ephemeral=not changed)


async def deliver_broadcasts(bot):
    if bot.settings.runtime_profile != 'public':
        return
    db = BroadcastStore(bot.cache._connection)
    rows = db.db.execute("SELECT id,project,phase,body,targets FROM development_broadcasts WHERE state='approved' ORDER BY created_at LIMIT 50").fetchall()
    for key,project,phase,body,targets in rows:
        for guild_id,channel_id in json.loads(targets):
            if not db.claim(key,guild_id,channel_id):
                continue
            try:
                if not destination_available(bot.settings, guild_id):
                    db.receipt(key,guild_id,'skipped',error='Destination excluded by current rollout restrictions.')
                    continue
                if (guild_id,channel_id) not in [tuple(row) for row in db.targets(bot.settings.award_test_guild_id, getattr(bot.settings, "announcement_testing_only", True))]:
                    db.receipt(key,guild_id,'skipped',error='Subscription disabled or channel changed after drafting.')
                    continue
                guild = bot.get_guild(guild_id)
                channel = guild.get_channel(channel_id) if guild else None
                if not isinstance(channel,discord.TextChannel):
                    db.receipt(key,guild_id,'failed',error='Destination unavailable; no message sent.')
                    continue
                permissions = channel.permissions_for(guild.me)
                if not (permissions.view_channel and permissions.send_messages and permissions.embed_links):
                    db.receipt(key,guild_id,'failed',error='Missing View Channel, Send Messages, or Embed Links; no message sent.')
                    continue
                message = await channel.send(embed=preview(dict(id=key,project=project,phase=phase,body=body)), allowed_mentions=discord.AllowedMentions.none(), silent=True)
                db.receipt(key,guild_id,'published',message.id)
            except Exception:
                logging.exception('Broadcast %s to guild %s could not be confirmed',key,guild_id)
                db.receipt(key,guild_id,'uncertain',error='Inspect the channel using the announcement ID. No automatic retry.')
        db.db.execute("UPDATE development_broadcasts SET state='processed' WHERE id=? AND state='approved'", (key,))
        db.db.commit()


def destination_available(settings, guild_id):
    return bool(guild_id) and (not getattr(settings, "announcement_testing_only", True)
                               or guild_id == settings.award_test_guild_id)


def subscription_permitted(interaction):
    return (interaction.client.settings.runtime_profile == "public"
            and destination_available(interaction.client.settings, interaction.guild_id)
            and (interaction.user.id == interaction.guild.owner_id
                 or interaction.user.guild_permissions.manage_guild
                 or interaction.user.id in interaction.client.settings.bot_admin_user_ids))


class DevelopmentCommands(app_commands.Group):
    async def interaction_check(self, interaction):
        if subscription_permitted(interaction):
            return True
        await interaction.response.send_message("A server manager must configure subscriptions. During the pilot, only the testing Discord can subscribe.", ephemeral=True)
        return False


development_group = DevelopmentCommands(name="development", description="Choose whether this server receives development broadcasts.", guild_only=True)


@development_group.command(name="subscription", description="Opt in or out of Peep-approved announcements in a chosen channel.")
async def subscription_command(interaction: discord.Interaction, enabled: bool, channel: discord.TextChannel | None = None):
    if enabled:
        if channel is None or channel.guild.id != interaction.guild_id:
            await interaction.response.send_message("Select a text channel in this Discord to opt in.", ephemeral=True)
            return
        permissions = channel.permissions_for(interaction.guild.me)
        if not (permissions.view_channel and permissions.send_messages and permissions.embed_links):
            await interaction.response.send_message("Grant SC Companion View Channel, Send Messages, and Embed Links first.", ephemeral=True)
            return
    store(interaction).subscribe(interaction.guild_id, channel.id if channel else 0, enabled, interaction.user.id)
    await interaction.response.send_message(
        f"Opted in: approved Peep broadcasts will arrive in {channel.mention}." if enabled else "Opted out. Unsent broadcasts will be skipped; existing messages stay.",
        ephemeral=True, allowed_mentions=discord.AllowedMentions.none())


@broadcast_group.command(name="recipients", description="Inspect all saved recipients, ten per page.")
async def recipients_command(interaction: discord.Interaction, draft_id: str, page: app_commands.Range[int, 1] = 1):
    draft = store(interaction).get(interaction.guild_id, draft_id)
    if not draft:
        await interaction.response.send_message("Broadcast not found.", ephemeral=True)
        return
    targets = json.loads(draft["targets"])
    selected = targets[(page-1)*10:page*10]
    text = "\n".join(f"Server `{guild}` → <#{channel}>" for guild,channel in selected)
    await interaction.response.send_message(f"Recipients page {page} ({len(targets)} total):\n{text or 'No recipients on this page.'}", ephemeral=True, allowed_mentions=discord.AllowedMentions.none())


class DevelopmentSubscriptionView(discord.ui.View):
    def __init__(self, channel):
        super().__init__(timeout=900)
        self.channel = channel

    async def interaction_check(self, interaction):
        if subscription_permitted(interaction):
            return True
        await interaction.response.send_message("A manager of an eligible server must configure subscriptions.", ephemeral=True)
        return False

    @discord.ui.button(label="Opt In to Broadcasts", style=discord.ButtonStyle.primary)
    async def enable(self, interaction: discord.Interaction, button: discord.ui.Button):
        await subscription_command.callback(interaction, True, self.channel)

    @discord.ui.button(label="Opt Out of Broadcasts", style=discord.ButtonStyle.secondary)
    async def disable(self, interaction: discord.Interaction, button: discord.ui.Button):
        await subscription_command.callback(interaction, False)


@broadcast_group.command(name="channel", description="Create or repair Peep's private broadcast channel under audit log.")
async def channel_command(interaction: discord.Interaction):
    from src.broadcast_room import ensure_broadcast_room
    await interaction.response.defer(ephemeral=True)
    try:
        channel = await ensure_broadcast_room(interaction.client)
    except (discord.HTTPException, ValueError):
        await interaction.followup.send("Could not prepare the private channel. Check the audit log category and Peep's Manage Channels, View Channel, Send Messages, Embed Links, and Read Message History permissions.", ephemeral=True)
        return
    await interaction.followup.send(f"Private broadcast workspace ready: {channel.mention}. Audit-category viewers can read it; only the server owner can draft or approve.", ephemeral=True)
