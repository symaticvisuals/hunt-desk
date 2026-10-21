# Job hunt

A local job search you can point at any role. Keywords, the scoring rubric, and API keys live in files you edit. Python only fetches, filters, and dedupes. An agent scores, and only when you ask it to.

The shape comes from [How to Build a Job Hunt System with Claude Code](https://www.scottypeterson.net/blog/how-to-build-a-job-hunt-system-with-claude-code). This checkout already has one person's resume filled in. Replace those files to make it someone else's.

## Make it yours

The first launch asks for a resume when `profile/og-resume.md` is missing or still a placeholder. Choose a PDF, Word file, or text file, or paste the text. The desk fills title keywords and the years cap from the roles and dates it can read. Edit those fields, then save.

Before that save, the screen audits the resume: missing email, missing years, vague openers such as "Responsible for", impact claims with no number, and leftover TODO text. **Rewrite weak lines** follows the humanizer rules a program can apply without a model: weak openers, prose dashes, "serves as", and chat leftovers. It does not invent employers, tools, or numbers. An agent rewriting the same file by hand still follows the full humanizer skill.

An Overleaf project URL clones through Git into `resumes/overleaf/<id>`. Put the token from Overleaf's Menu, then Git, in `OVERLEAF_GIT_TOKEN`. The username is `git`. A rewrite edits the `.tex` and keeps a `.bak`. It does not publish until you run:

```
uv run python -m hunt overleaf push <project-id>
```

To do it again, press `ctrl+n` in the desk, or run:

```
uv run python -m hunt setup path/to/resume.pdf
```

The longer rewrite is `resume-rewrite`. It inventories the file, waits for your numbers, rewrites up to four roles, then checks that nothing was invented. A clean run writes `resumes/updated-resume.md`, refreshes `profile/og-resume.md`, and keeps the previous master as `profile/og-resume.md.bak`. Two things keep the master as it is and leave the draft for you to paste in: a `[metric]` that still needs a number, or a resume with no experience heading to replace. It then searches, asks the classifier which roles to keep, and scores those. A failed fact check leaves the master resume unchanged and does not search.

Press `w` and choose the `api` row. That calls the Claude API from this app. If `ANTHROPIC_API_KEY` is missing, the desk asks for it and saves it in `.env`. A terminal run of `hunt run api` or `hunt run managed` asks the same way. The value is never printed. Press `k` to change it later. The same run from the shell:

```
uv run python -m hunt run api resume-rewrite
```

`profile/models.json` picks the model and effort for each step. Inventory uses Haiku at low effort. Each role rewrite uses Sonnet at high effort. The voice pass uses Opus at medium effort. The fact check uses Sonnet, so the model that wrote the final prose is not the model that judges it. On the rate card from 2026-10-06 that is $0.10/$0.50, $2/$10, and $4/$20 per million input/output tokens.

If the inventory finds a missing number and you have not supplied answers, the run stops and writes the questions to `data/resume-rewrite.md`. Put one answer per question in `data/resume-answers.md` (or the answers field) and run it again. Unknown is a valid answer. Optional job text aims the voice pass at one posting.

The `managed` row runs the same rewrite as one Claude Managed Agents session. Anthropic hosts the loop. Haiku inventories, Sonnet rewrites each role, Opus writes the draft, and a second Sonnet agent fact-checks it. That check is an extra pass on every run. The session stops at an $8 list-price budget. Dynamic workflows are off, because four roles fit in one session.

The agents are files under `agents/`, and the cloud environment is `environments/resume-rewrite.yaml`. Sync them once, after reading the plan:

```
ant apply --dry-run agents environments
ant apply --yes agents environments
```

`ant apply` writes `claude-lock.json`. Commit that file. Then:

```
uv run python -m hunt run managed resume-rewrite
```

The resume is uploaded for that session and mounted read-only. The draft comes back to `data/resume-rewrite.md`. Web search is off. A tool call that pauses for approval is denied, because the desk is not watching the container.

The `grok` and `claude` rows open those apps on the same workflow. `/resume-rewrite` inside Grok does that too.

Open the desk and change it there. The screen writes the files.

| Key | What it updates |
|---|---|
| `/` | Keywords in `profile/search.json`, then the list refreshes |
| `a` | A company on `data/watchlist.json`, after the public board answers |
| `e` | Pay band and years cap in `profile/rubric.json` |
| `c` | The company's class: product, services, staffing, or training. It sticks for every posting from that company |
| `k` | Keep this role even though a worth rule hid or flagged it |
| `r` | The master resume |
| `enter` | The full role. The right-hand pane already follows the cursor |
| `s` | Claude score, only after the classifier says keep |
| `S` | Claude score anyway |
| `g` | A fresh board search |

Clef-flash returns a probability. It does not rewrite the posting. The boilerplate strip is plain Python and happens before Clef-flash or Claude sees the text. The desk calls Ollama at `http://127.0.0.1:11434/v1/systemone`. Pull the model once:

```
ollama pull clef-flash
```

`CLEF_URL` and `CLEF_MODEL` override the address and the model name.

Claude Haiku can classify instead of Clef-flash, with no Ollama needed. Open Settings (`,`) and press Use Haiku, or set `"classifier": "haiku"` in `profile/models.json`. Haiku asks the same two questions through the Claude API and answers yes or no, so the desk shows `Haiku keep` with no probability. It needs `ANTHROPIC_API_KEY`, costs about $0.0003 a role, and scheduled checks use it too. The model and effort are the `classify` step in `profile/models.json`. Roles already classified keep their decision after a switch. `uv run python -m hunt classify --limit 15` classifies from a terminal with whichever one is selected.

A file that still contains the line `PLACEHOLDER_RESUME` opens the resume editor on launch.

## Run it

From this directory:

```
uv run python -m hunt
```

Headless commands, for a schedule:

```
uv run python -m hunt search
uv run python -m hunt sources
uv run python -m hunt score
```

`search` writes `data/last-search.json` and does not score. The desk reads that file.

Paste one JSON object per line into `python3 -m hunt dedupe` to mark `NEW` or `DUP` without opening the CSV.

## Tests

```
uv run python -m unittest hunt.test_evaluated hunt.test_filter hunt.test_desk
```

Scheduling, and why this is a skill rather than a Claude dynamic workflow, is in `docs/claude.md`.
