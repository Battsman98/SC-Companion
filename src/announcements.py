"""Explicitly approved project updates; never invoked by deployment/startup hooks."""
import time
import asyncio
import uuid

import discord
from discord import app_commands


class AnnouncementStore:
    def __init__(self, connection):
        self.db = connection
        self.db.execute("""CREATE TABLE IF NOT EXISTS project_announcements (
            id TEXT PRIMARY KEY, guild_id INTEGER NOT NULL, channel_id INTEGER NOT NULL,
            project TEXT NOT NULL, phase TEXT NOT NULL, body TEXT NOT NULL,
            state TEXT NOT NULL, author_id INTEGER NOT NULL, approver_id INTEGER,
            message_id INTEGER, created_at INTEGER NOT NULL,
            UNIQUE(guild_id, project, phase))""")
        self.db.commit()

    def create(self, guild_id, channel_id, project, phase, body, author_id):
        project, body = project.strip(), body.strip()
        if not project or not body or len(project) > 150 or len(body) > 3500:
            raise ValueError("Use a project name of 1–150 characters and an update of 1–3500 characters.")
        if phase not in {"started", "completed"}:
            raise ValueError("Choose started or completed.")
        draft_id = uuid.uuid4().hex
        cursor = self.db.execute("""INSERT INTO project_announcements
            (id, guild_id, channel_id, project, phase, body, state, author_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, 'draft', ?, ?)
            ON CONFLICT(guild_id, project, phase) DO NOTHING""",
            (draft_id, guild_id, channel_id, project, phase, body, author_id, int(time.time())))
        self.db.commit()
        if cursor.rowcount != 1:
            raise ValueError("That project/stage already exists. Review its existing draft; do not announce it twice.")
        return draft_id

    def get(self, guild_id, draft_id):
        row = self.db.execute("SELECT id, channel_id, project, phase, body, state, message_id FROM project_announcements WHERE guild_id = ? AND id = ?", (guild_id, draft_id)).fetchone()
        return dict(zip(("id", "channel_id", "project", "phase", "body", "state", "message_id"), row)) if row else None

    def transition(self, guild_id, draft_id, state, approver_id, snapshot=None):
        sql = "UPDATE project_announcements SET state = ?, approver_id = ? WHERE guild_id = ? AND id = ? AND state = 'draft'"
        parameters = (state, approver_id, guild_id, draft_id)
        if snapshot is not None:
            sql += " AND body = ? AND channel_id = ?"
            parameters += (snapshot['body'], snapshot['channel_id'])
        cursor = self.db.execute(sql, parameters)
        self.db.commit()
        return cursor.rowcount == 1

    def finish(self, guild_id, draft_id, message_id):
        self.db.execute("UPDATE project_announcements SET state = 'published', message_id = ? WHERE guild_id = ? AND id = ? AND state = 'sending'", (message_id, guild_id, draft_id))
        self.db.commit()


def permitted(interaction):
    return denial_reason(interaction) is None


def denial_reason(interaction):
    settings = interaction.client.settings
    if settings.runtime_profile != "public":
        return "Use SC Companion for announcements, not Peep."
    if not settings.award_test_guild_id or interaction.guild_id != settings.award_test_guild_id:
        return "Announcements are available only in the configured testing Discord."
    guild = getattr(interaction, "guild", None)
    if (guild is not None and guild.owner_id == interaction.user.id
            or interaction.user.id in settings.bot_admin_user_ids):
        return None
    return "Only the testing-server owner or a configured bot admin can manage announcements."


def preview(draft):
    embed = discord.Embed(title=f"Project {draft['phase']}: {draft['project']}",
                          description=draft['body'], color=0x3498DB)
    embed.set_footer(text=f"SC Companion • Announcement {draft['id']}")
    return embed


class Announcements(app_commands.Group):
    async def interaction_check(self, interaction):
        if permitted(interaction):
            return True
        await interaction.response.send_message(denial_reason(interaction), ephemeral=True)
        return False


announcement_group = Announcements(name="announcement", description="Draft and approve testing-server project announcements.", guild_only=True)


def store(interaction):
    return AnnouncementStore(interaction.client.cache._connection)


async def ensure_development_panel(bot, guild):
    from src.broadcasts import destination_available
    if bot.settings.runtime_profile != "public" or not destination_available(bot.settings, guild.id):
        raise ValueError("Development announcements are limited to the testing Discord.")
    if not hasattr(bot, "_development_setup_lock"):
        bot._development_setup_lock = asyncio.Lock()
    async with bot._development_setup_lock:
        key = f"guild:{guild.id}:development-announcements"
        saved = await bot.cache.get(key) or {}
        category = discord.utils.find(
            lambda item: item.name.casefold() in {"sc companion", "🚀 sc companion"}, guild.categories)
        if category is None:
            category = await guild.create_category("🚀 SC COMPANION", reason="Set up development announcements")
        channel = guild.get_channel(saved.get("channel_id", 0))
        if not isinstance(channel, discord.TextChannel):
            channel = discord.utils.get(guild.text_channels, name="development-announcements")
        if channel is None:
            channel = await guild.create_text_channel(
                "development-announcements", category=category,
                topic="Approved SC Companion project starts and completed features.",
                overwrites={guild.default_role: discord.PermissionOverwrite(send_messages=False),
                            guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, embed_links=True)},
                reason="Requested development announcement setup")
        elif channel.category_id != category.id:
            await channel.edit(category=category, sync_permissions=False,
                               reason="Move development announcements into SC Companion")
        saved["channel_id"] = channel.id
        await bot.cache.set(key, saved, 315360000)
        embed = discord.Embed(title="Development Announcements", description=(
            "Approved SC Companion project starts and completed features appear here.\n\n"
            "**Central broadcasts:** Opt in with `/development subscription`, choosing this channel. "
            "The owner drafts and approves central updates using Peep’s `/broadcast` commands in its home Discord. "
            "Use subscription with enabled=False to opt out.\n\n"
            "Deployments never approve project announcements automatically."), color=0x3498DB)
        embed.set_footer(text="SC Companion • Development announcement setup")
        message = None
        if saved.get("panel_message_id"):
            try:
                message = await channel.fetch_message(saved["panel_message_id"])
            except discord.NotFound:
                pass
        if message is None:
            async for candidate in channel.history(limit=100):
                if candidate.author.id == bot.user.id and candidate.embeds and candidate.embeds[0].footer.text == embed.footer.text:
                    message = candidate
                    break
        if message is None:
            message = await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none(), silent=True)
        elif message.author.id == bot.user.id:
            await message.edit(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        else:
            raise ValueError("Saved panel belongs to another application; no message was changed.")
        saved.update(panel_message_id=message.id, requested=False, error=None)
        await bot.cache.set(key, saved, 315360000)
        return channel


async def setup_development_announcements(interaction):
    from src.broadcasts import subscription_permitted, DevelopmentSubscriptionView
    if not subscription_permitted(interaction):
        await interaction.response.send_message("A manager of an eligible server must configure development announcements.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    try:
        channel = await ensure_development_panel(interaction.client, interaction.guild)
    except (discord.HTTPException, ValueError):
        await interaction.followup.send("Could not create the channel/panel. Check SC Companion's Manage Channels, View Channel, Read Message History, Send Messages, and Embed Links permissions, then retry.", ephemeral=True)
        return
    await interaction.followup.send(f"Channel and setup panel ready in {channel.mention}. Opt in below to receive approved Peep broadcasts. Creating the channel alone does not subscribe this server.", ephemeral=True, view=DevelopmentSubscriptionView(channel))


@announcement_group.command(name="channel", description="Create or repair the development announcement channel and setup panel.")
async def channel_command(interaction: discord.Interaction):
    await setup_development_announcements(interaction)


@announcement_group.command(name="revise", description="Correct a draft or reopen a rejected announcement for review.")
async def revise_command(interaction: discord.Interaction, draft_id: str, channel: discord.TextChannel, update: str):
    if channel.guild.id != interaction.guild_id or not 1 <= len(update.strip()) <= 3500:
        await interaction.response.send_message("Choose a channel in this server and an update of 1–3500 characters.", ephemeral=True)
        return
    db = store(interaction)
    cursor = db.db.execute("UPDATE project_announcements SET body = ?, channel_id = ?, state = 'draft', approver_id = NULL WHERE guild_id = ? AND id = ? AND state IN ('draft', 'rejected')", (update.strip(), channel.id, interaction.guild_id, draft_id))
    db.db.commit()
    await interaction.response.send_message("Revised. Use `/announcement review` before approving." if cursor.rowcount == 1 else "No editable draft with that ID.", ephemeral=True)


@announcement_group.command(name="draft", description="Preview a project update without publishing it.")
@app_commands.choices(phase=[app_commands.Choice(name="New project / started", value="started"), app_commands.Choice(name="Project completed", value="completed")])
async def draft_command(interaction: discord.Interaction, channel: discord.TextChannel,
                        project: str, phase: str, update: str):
    if channel.guild.id != interaction.guild_id:
        await interaction.response.send_message("Choose a channel in the testing Discord.", ephemeral=True)
        return
    db = store(interaction)
    try:
        draft_id = db.create(interaction.guild_id, channel.id, project, phase, update, interaction.user.id)
    except ValueError as error:
        await interaction.response.send_message(str(error), ephemeral=True)
        return
    await interaction.response.send_message(
        f"Draft for {channel.mention}. Review this exact text, then use `/announcement approve {draft_id}` to publish or `/announcement reject {draft_id}` to discard.",
        embed=preview(db.get(interaction.guild_id, draft_id)), ephemeral=True,
        allowed_mentions=discord.AllowedMentions.none())


@announcement_group.command(name="pending", description="List saved drafts and any deliveries needing inspection.")
async def pending_command(interaction: discord.Interaction):
    rows = store(interaction).db.execute("SELECT id, project, phase, state FROM project_announcements WHERE guild_id = ? AND state IN ('draft', 'sending') ORDER BY created_at DESC LIMIT 15", (interaction.guild_id,)).fetchall()
    text = "\n".join(f"`{row[0]}` — {row[1][:60]} ({row[2]}, {row[3]})" for row in rows)
    await interaction.response.send_message(text or "No pending announcements.", ephemeral=True, allowed_mentions=discord.AllowedMentions.none())


@announcement_group.command(name="review", description="Show the exact saved announcement and destination.")
async def review_command(interaction: discord.Interaction, draft_id: str):
    draft = store(interaction).get(interaction.guild_id, draft_id)
    if not draft:
        await interaction.response.send_message("Announcement not found.", ephemeral=True)
        return
    await interaction.response.send_message(f"Destination: <#{draft['channel_id']}> • Status: {draft['state']}", embed=preview(draft), ephemeral=True, allowed_mentions=discord.AllowedMentions.none())


@announcement_group.command(name="reject", description="Discard a draft without posting it.")
async def reject_command(interaction: discord.Interaction, draft_id: str):
    changed = store(interaction).transition(interaction.guild_id, draft_id, "rejected", interaction.user.id)
    await interaction.response.send_message("Draft rejected." if changed else "No pending draft with that ID.", ephemeral=True)


@announcement_group.command(name="approve", description="Approve and publish the saved text once, to its saved channel.")
async def approve_command(interaction: discord.Interaction, draft_id: str):
    db = store(interaction)
    draft = db.get(interaction.guild_id, draft_id)
    if not draft or draft['state'] != 'draft':
        await interaction.response.send_message("No pending draft with that ID. Previously attempted announcements are never resent automatically.", ephemeral=True)
        return
    channel = interaction.guild.get_channel(draft['channel_id'])
    if not isinstance(channel, discord.TextChannel):
        await interaction.response.send_message("The saved destination no longer exists. Reject this draft and prepare a corrected one.", ephemeral=True)
        return
    permissions = channel.permissions_for(interaction.guild.me)
    if not permissions.view_channel or not permissions.send_messages or not permissions.embed_links:
        await interaction.response.send_message("Grant SC Companion View Channel, Send Messages, and Embed Links in the destination first.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    if not db.transition(interaction.guild_id, draft_id, "sending", interaction.user.id, snapshot=draft):
        await interaction.followup.send("This draft changed or has already been handled. Review it again.", ephemeral=True)
        return
    # Commit the claim before contacting Discord: ambiguous failures never trigger a duplicate.
    try:
        message = await channel.send(embed=preview(draft), allowed_mentions=discord.AllowedMentions.none())
        db.finish(interaction.guild_id, draft_id, message.id)
    except Exception:
        await interaction.followup.send("Delivery could not be confirmed. Inspect the destination for this announcement ID. It is held as 'sending' and will not be retried automatically.", ephemeral=True)
        return
    await interaction.followup.send(f"Announcement published: {message.jump_url}", ephemeral=True)
