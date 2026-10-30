# Development announcements

Peep owns private announcement drafts and approvals in its home Discord. SC Companion delivers approved updates to each participating server, using the same shared database pattern as feedback routing in the opposite direction. Peep keeps its existing OAuth, identity, feedback, support, and admin responsibilities. Neither deployment nor normal chat messages create or approve broadcasts.

## Set up a receiving server

Use SC Companion's `/admin panel` → Development Announcements to create or repair `development-announcements` under `🚀 SC COMPANION`. Existing channels move into that category without resetting permissions. Creating a channel does not subscribe the server.

Choose **Opt In to Broadcasts** in the setup response, or use `/development subscription enabled:True channel:#development-announcements`. A server owner, member with Manage Server, or configured bot admin may configure the subscription. To use a different channel, run the command again with that channel. Set enabled to False to opt out. Broadcast subscriptions are managed entirely in Discord.

## Private broadcast workspace

Peep creates `development-announcements` under the existing **audit log** category on startup. `/broadcast channel` repairs it. It copies the category's viewer overwrites, denies @everyone visibility, and grants the owner and Peep access. Other audit viewers have read-only access. Discord Administrator permissions still bypass channel overwrites, but the bot enforces owner-only broadcast commands even for administrators.

All broadcast operations except channel setup must run in this private workspace. Draft previews, approval/rejection notices, and requested delivery reviews are visible to its authorized readers. Re-run `/broadcast channel` after changing audit-category roles to apply the current access rules.

## Draft and approve in Peep

1. In Peep's home Discord, run `/broadcast draft` with the project name, started/completed stage, and announcement text. Only the home-server owner can do this; configured bot admins cannot draft or approve.
2. Inspect the preview and saved recipients in the private channel. Use `/broadcast review` to retrieve it later, `/broadcast recipients` for a paginated full audience, and `/broadcast pending` to list recent records.
3. Run `/broadcast approve` with the draft ID to authorize delivery. To change the content, reject the draft with `/broadcast reject` and create a replacement. Draft content and audience are immutable.
4. SC Companion picks up approved broadcasts within about one minute. `/broadcast review` shows per-server delivery receipts: published, skipped, failed, uncertain, or sending. A processed broadcast means delivery attempts were handled; inspect individual receipts to see failures.

Approval freezes the text and destination list shown in the draft. A newly subscribed server does not receive old broadcasts. Opting out or changing the destination before delivery causes that recipient to be skipped. Messages suppress mentions and push notifications; this does not change members' personal channel-mute settings.

Durable claims prevent double delivery from concurrent workers, repeated approvals, and restarts. An ambiguous Discord failure is marked uncertain (or sending if the process stopped) and is never automatically retried. Inspect the destination using the announcement ID in the footer before creating any replacement broadcast. There is no bulk delete/retract feature. Drafting the same project again creates a distinct broadcast, so check recent records before approving duplicates.

The previous `/announcement` commands remain available for local testing drafts. They never broadcast to other servers.

## Deployment and rollout

Peep and SC Companion must use the same PostgreSQL `DATABASE_PATH`. Deploy the shared broadcast code only to both bot runtimes; Peep remains on its own deployment branch with its own token. No credentials are exchanged between applications.

`ANNOUNCEMENT_TESTING_ONLY` defaults to true. Set the same `AWARD_TEST_GUILD_ID` on both bot services; while testing-only is enabled, only that server can subscribe or receive deliveries. The sender and receiver independently enforce this restriction. If the ID is missing, the pilot fails closed.

After testing and explicit rollout approval, set `ANNOUNCEMENT_TESTING_ONLY=false` on both bot services to include every opted-in SC Companion server. Existing servers are never subscribed automatically.

Do not deploy the website for this release. Before any authorized push, check whether shared source changes would trigger its auto-deploy and prevent that deployment. The existing website remains at its current release.

Every bot push/deployment requires user confirmation under AGENTS.md. No deployment is authorized by implementing this feature.

## Reputation setup

In the testing Discord, `/admin panel` also includes Reputation Setup. Select a reviewer role, then enable/create or repair the application, private review, and activity panels. The controls share settings with the website. Disabling preserves existing records and channels.
