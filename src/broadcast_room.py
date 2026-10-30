"""Private Peep broadcast workspace, with visibility inherited from Audit Log."""
import asyncio
import discord

CATEGORY_ID = 1516295744603164732
CHANNEL_NAME = 'development-announcements'
PANEL_FOOTER = 'Peep • Development broadcast controls'


def room_key(guild_id):
    return f'guild:{guild_id}:peep-broadcast-room'


def room_overwrites(guild):
    category = guild.get_channel(CATEGORY_ID)
    if not isinstance(category, discord.CategoryChannel):
        category = discord.utils.find(lambda item: item.name.casefold() == 'audit log', guild.categories)
    if category is None:
        raise ValueError('The existing audit log category could not be found.')
    overwrites = category.overwrites
    for target, overwrite in overwrites.items():
        overwrite.send_messages = False
        overwrite.send_messages_in_threads = False
        overwrite.create_public_threads = False
        overwrite.create_private_threads = False
        overwrite.add_reactions = False
    everyone = overwrites.get(guild.default_role, discord.PermissionOverwrite())
    everyone.view_channel = False
    everyone.send_messages = False
    overwrites[guild.default_role] = everyone
    if guild.owner is None or guild.me is None:
        raise ValueError('The server owner or Peep member could not be resolved.')
    for member in (guild.owner, guild.me):
        overwrites[member] = discord.PermissionOverwrite(
            view_channel=True, send_messages=True, read_message_history=True,
            embed_links=True, use_application_commands=True)
    return category, overwrites


async def ensure_broadcast_room(bot):
    if bot.settings.runtime_profile != 'peep':
        return None
    guild = bot.get_guild(bot.settings.discord_guild_id or 0)
    if guild is None:
        raise ValueError('Peep home Discord could not be resolved.')
    if not hasattr(bot, '_broadcast_room_lock'):
        bot._broadcast_room_lock = asyncio.Lock()
    async with bot._broadcast_room_lock:
        category, overwrites = room_overwrites(guild)
        saved = await bot.cache.get(room_key(guild.id)) or {}
        channel = guild.get_channel(saved.get('channel_id', 0))
        if not isinstance(channel, discord.TextChannel):
            channel = discord.utils.get(category.text_channels, name=CHANNEL_NAME)
        if channel is None:
            channel = await guild.create_text_channel(
                CHANNEL_NAME, category=category, overwrites=overwrites,
                topic='Private development broadcast drafts, approvals, and delivery reviews. Server owner controls publishing.',
                reason='Create private development broadcast workspace')
        elif channel.category_id != category.id or channel.overwrites != overwrites:
            await channel.edit(category=category, overwrites=overwrites,
                               reason='Apply audit-category visibility and owner-only broadcast controls')
        saved['channel_id'] = channel.id
        await bot.cache.set(room_key(guild.id), saved, 315360000)
        embed = discord.Embed(title='Development Announcements', description=(
            'Private workspace for drafting and approving SC Companion announcements.\n\n'
            '**Server owner:** `/broadcast draft` → `/broadcast review` → `/broadcast approve`. '
            'Use `/broadcast pending` for saved drafts and `/broadcast reject` to discard one. '
            '`/broadcast review` also shows delivery results.\n\n'
            '**Audit viewers:** You can read the drafts and status updates here. Only the server owner can operate the broadcast commands.\n\n'
            'Nothing is published to receiving servers until the owner explicitly approves. '
            'Delivery remains restricted to the configured rollout and opted-in channels.'), color=0x3498DB)
        embed.set_footer(text=PANEL_FOOTER)
        message = None
        if saved.get('panel_message_id'):
            try:
                message = await channel.fetch_message(saved['panel_message_id'])
            except discord.NotFound:
                pass
        if message is None:
            async for candidate in channel.history(limit=100):
                if candidate.author.id == bot.user.id and candidate.embeds and candidate.embeds[0].footer.text == PANEL_FOOTER:
                    message = candidate
                    break
        if message is None:
            message = await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none(), silent=True)
        elif message.author.id == bot.user.id:
            await message.edit(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        else:
            raise ValueError('The saved setup panel belongs to another application.')
        saved['panel_message_id'] = message.id
        await bot.cache.set(room_key(guild.id), saved, 315360000)
        return channel
