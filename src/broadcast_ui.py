"""Two-step owner-only announcement authoring UI."""
import discord
from src.broadcast_store import BroadcastStore


async def allowed(interaction):
    from src.broadcasts import permitted
    from src.broadcast_room import room_key
    if permitted(interaction):
        room = await interaction.client.cache.get(room_key(interaction.guild_id)) or {}
        if interaction.channel_id == room.get('channel_id'):
            return True
    await interaction.response.send_message('Only the server owner can use these controls, in the private announcement channel.', ephemeral=True)
    return False


class OwnerView(discord.ui.View):
    async def interaction_check(self, interaction):
        return await allowed(interaction)


class DraftModal(discord.ui.Modal, title='Draft announcement'):
    announcement = discord.ui.TextInput(label='What would you like to announce?', style=discord.TextStyle.paragraph,
                                        placeholder='Type your announcement here…', max_length=3500)

    async def on_submit(self, interaction):
        if not await allowed(interaction):
            return
        from src.broadcasts import preview
        db = BroadcastStore(interaction.client.cache._connection)
        settings = interaction.client.settings
        try:
            key = db.create(interaction.guild_id, 'Development Announcement', 'completed',
                str(self.announcement), db.targets(settings.award_test_guild_id, settings.announcement_testing_only), interaction.user.id)
        except ValueError as error:
            await interaction.response.send_message(str(error), ephemeral=True)
            return
        await interaction.response.send_message('Draft saved. Click **Approve** to review and submit.',
            embed=preview(db.get(interaction.guild_id, key)), ephemeral=True, allowed_mentions=discord.AllowedMentions.none())


class SubmitView(OwnerView):
    def __init__(self, draft_id):
        super().__init__(timeout=900)
        self.draft_id = draft_id

    @discord.ui.button(label='Submit announcement', style=discord.ButtonStyle.success)
    async def submit(self, interaction, button):
        db = BroadcastStore(interaction.client.cache._connection)
        changed = db.decide(interaction.guild_id, self.draft_id, 'approved', interaction.user.id)
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(
            content='Submitted. SC Companion will deliver the approved announcement.' if changed else 'This draft has already been handled.', view=self)

    @discord.ui.button(label='Discard draft', style=discord.ButtonStyle.secondary)
    async def discard(self, interaction, button):
        db = BroadcastStore(interaction.client.cache._connection)
        changed = db.decide(interaction.guild_id, self.draft_id, 'rejected', interaction.user.id)
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(content='Draft discarded.' if changed else 'This draft has already been handled.', view=self)


async def show_review(interaction, key):
    from src.broadcasts import preview, destinations
    draft = BroadcastStore(interaction.client.cache._connection).get(interaction.guild_id, key)
    if not draft or draft['state'] != 'draft':
        await interaction.response.send_message('That draft is no longer available.', ephemeral=True)
        return
    await interaction.response.send_message('Review your announcement below. Nothing is sent until you click **Submit announcement**.\n\n'
        + destinations(draft), embed=preview(draft), view=SubmitView(key), ephemeral=True, allowed_mentions=discord.AllowedMentions.none())


class DraftSelect(discord.ui.Select):
    def __init__(self, rows):
        super().__init__(placeholder='Choose the draft to review', options=[
            discord.SelectOption(label=f'{i+1}. {body[:90]}',value=key) for i,(key,body) in enumerate(rows)])

    async def callback(self, interaction):
        await show_review(interaction, self.values[0])


async def choose_draft(interaction):
    rows = BroadcastStore(interaction.client.cache._connection).db.execute(
        "SELECT id,body FROM development_broadcasts WHERE source_guild_id=? AND state='draft' ORDER BY created_at DESC,id DESC LIMIT 25",
        (interaction.guild_id,)).fetchall()
    if not rows:
        await interaction.response.send_message('No drafts yet. Click **Draft** to write an announcement.', ephemeral=True)
    elif len(rows) == 1:
        await show_review(interaction, rows[0][0])
    else:
        view = OwnerView(timeout=900)
        view.add_item(DraftSelect(rows))
        await interaction.response.send_message('Choose a draft to review (up to 25 most recent).',view=view,ephemeral=True)


class BroadcastPanel(OwnerView):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label='Draft', style=discord.ButtonStyle.primary, custom_id='peep:broadcast:draft')
    async def draft(self, interaction, button):
        await interaction.response.send_modal(DraftModal())

    @discord.ui.button(label='Approve', style=discord.ButtonStyle.success, custom_id='peep:broadcast:approve')
    async def approve(self, interaction, button):
        await choose_draft(interaction)
