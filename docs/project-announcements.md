# Project announcements (testing pilot)

SC Companion can publish approved feature/project starts and completions. There is no deployment or startup posting hook. Creating a draft never sends a public announcement.

This pilot registers `/announcement` only in `AWARD_TEST_GUILD_ID`, the existing testing Discord. Every command also checks the public runtime and that guild, then permits the testing-server owner or an account listed in `BOT_ADMIN_USER_IDS`. Peep and other servers do not receive the commands. Other server administrators cannot approve announcements unless explicitly listed as bot admins.

1. Run `/announcement channel` to create/find `development-announcements`, or use an existing text/announcement channel.
2. Run `/announcement draft`, selecting the destination, project name, started/completed stage, and public-facing update text.
3. Inspect the private preview. `/announcement pending` lists recent drafts; `/announcement review` retrieves the saved text and destination after a restart.
4. Use `/announcement revise` to correct the text/destination, or `/announcement reject` to discard it. Rejected drafts can be revised and reviewed again.
5. Run `/announcement approve` with the draft ID to publish that saved announcement once. Mentions are disabled and announcement-channel messages are not crossposted.

Use a consistent project name: the database permits one announcement per project/stage/server. Drafts and publication receipts are durable. An atomic claim prevents concurrent approvals or restarts from resending an announcement. If delivery is uncertain, the record stays `sending`; inspect the destination using the ID in the embed footer before manual recovery. There is deliberately no automatic retry.

Other SC Companion servers remain excluded. A future rollout needs an explicit per-server opt-in/channel configuration and separate deployment approval.
