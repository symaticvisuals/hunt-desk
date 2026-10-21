# Which Claude Code feature runs which step

Checked against the Claude Code docs on 2026-10-15.

[Dynamic workflows](https://code.claude.com/docs/en/workflows) are JavaScript scripts that fan work out across many subagents. The script itself cannot read the filesystem or run the shell. That is the wrong tool for this daily loop, which is one fetch, one dedupe, and one score. The `ultracode` keyword also does not start a workflow from `claude -p` or from a scheduled prompt. This repo does not ship a `.claude/workflows` script.

| Step | Who holds the plan | What you run |
|---|---|---|
| Fetch, filter, dedupe | Python | `python3 -m hunt search` |
| Score and write the digest | One agent following instructions | `/job-hunt`, or `python3 -m hunt score` |
| Laptop open, including local `.env` | You, or a Desktop scheduled task | [Desktop scheduled tasks](https://code.claude.com/docs/en/desktop-scheduled-tasks) |
| Laptop closed | Cloud routine on a fresh clone | [`/schedule`](https://code.claude.com/docs/en/routines). Minimum interval is one hour. |
| Inside an open session only | Not this hunt | [`/loop`](https://code.claude.com/docs/en/scheduled-tasks) stops when the session closes |

`python3 -m hunt score` calls `claude -p` as described in [Run Claude Code programmatically](https://code.claude.com/docs/en/headless). It does not pass `--bare`, so the session still loads `CLAUDE.md` and this skill. It uses `--permission-mode acceptEdits` so the digest and CSV can be written. If `claude` is not installed, the command prints that and leaves the search cache in place.

Create a cloud routine yourself with `/schedule` and the prompt in `docs/daily-run.md`. This repo does not create one. Personal API keys stay in `.env` on your machine.
