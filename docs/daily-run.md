# Daily run

You are scoring jobs for the person whose files are in this repository. Do not assume a profession. Do not invent keywords. Read:

- `CLAUDE.md`
- `profile/og-resume.md`
- `profile/linkedin-profile.md`
- `profile/projects.md` only when a posting wants a builder
- `profile/rubric.json`
- `profile/search.json`

If `profile/og-resume.md` contains the line `PLACEHOLDER_RESUME`, stop and ask for a resume.

1. Get today's date.
2. Run `python3 -m hunt search`. Use that output and `data/last-search.json`. Do not fetch the same boards yourself. When the launchd schedule is on, it has already fetched most of these roles into `data/inbox.json`; `hunt search` still writes every unscored role from a full fetch to `data/last-search.json`.
3. Optional: web-search for at most five more roles that match the keywords already in `profile/search.json`. Filter on the snippet before opening a page. Skip anything the CLI returned. Verify each survivor with at most one fetch. Prefer the company's Greenhouse, Lever, Ashby, or Workday JSON. An aggregator URL is not verification.
4. Score every new role with `profile/rubric.json`. Record the base score, each penalty id, and the final score. Append them to `data/evaluated-jobs.csv` in one write. Leave `applied` empty.
5. Write `daily-digest/YYYY-MM-DD.md`: roles at or above `natural_hire_at`, the other scored roles, and a Skipped section for dead links, duplicates, and excluded titles, each with the reason. Fewer than five is a complete result. If a command fails, write the error in the digest and continue. Never invent a posting.
6. Stop. Do not push. Do not mark anything applied.

A cloud routine clones the repo and does not see a gitignored `.env`. Keyed sources skip unless that environment was given keys on purpose. Do not paste API keys into the digest. Anyone who can use a Claude Code cloud environment can read its environment variables, so prefer this command on your own machine when the keys are personal.
