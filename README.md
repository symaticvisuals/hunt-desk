# Job hunt

A local job search you can point at any role, from engineering to product management. You upload a resume, pick the titles you want, and the search runs on its own: it fetches postings, sorts them, scores the ones worth a look, and writes a one-page PDF resume for the best of them. Claude spend stays inside two daily caps you set.

The shape comes from [How to Build a Job Hunt System with Claude Code](https://www.scottypeterson.net/blog/how-to-build-a-job-hunt-system-with-claude-code).

## Start here

You need a Mac and an [Anthropic API key](https://platform.claude.com/settings/keys). From the folder you cloned:

```sh
./start
```

`./start` installs uv, the Python packages, and Tectonic (the TeX engine that builds the PDFs, about 30 MB, no sudo). It copies the starting files in `profile/defaults/` and `data/defaults/` into place and opens the desk. Run it again any time; it skips what is already done. `./start --no-tex` skips Tectonic, and resumes are then written as `.tex` only.

The desk walks you through three screens:

1. **API key.** Saved in `.env` and never printed. You can skip it and add it later in Settings (`,`).
2. **Resume.** A PDF, Word file, or pasted text. Claude can rework weak lines, or you keep it as is.
3. **What you want.** Claude proposes titles from the resume, including adjacent ones it marks `adjacent`. Each title shows how many openings the boards have right now, as the count comes in. Edit titles, places, the most years a posting may ask for, how fresh a posting must be, and the two daily caps, then save. Saving turns on background checks every 15 minutes.

Your resume, preferences, and everything under `data/` and `resumes/` stay out of git.

## What it costs

Two caps in Step 2 (or `p` later) bound what background checks spend each day:

| Cap | Default | Pays for |
|---|---|---|
| Sorting | $0.50 a day | Claude Haiku deciding whether each new posting is worth reading, about $0.0003 a role |
| Scoring and resumes | $3.00 a day | Claude scoring each kept role, and a one-page resume for each role above the cutoff |

Batches are reserved at an upper-bound estimate before they go and settle at the billed amount when they end, so a cap is never passed by the background. A cap of 0 turns that step off. The status line shows today's spend, such as `classify $0.12/$0.50 · score $1.40/$3.00`. Pressing `A` can send more than fits if you say so.

## Resumes

Every role that scores above the cutoff in `profile/rubric.json` gets a folder under `resumes/<company>/<role>/`:

| File | What it is |
|---|---|
| `resume.pdf` | One page on Jake Gutierrez's LaTeX template |
| `resume.tex`, `resume.json` | The source, and the structured answer it was built from |
| `notes.md` | What moved and why, what the job wants that the resume does not show, numbers to check, cuts made for one page, and the ATS check |
| `ats.json`, `jd.md` | The check results, and the posting |

Tailoring only reorders and reframes lines that are already in your master resume. It never adds an employer, title, date, number, or tool. The wording follows the humanizer skill in `prompts/humanizer/` (MIT, Siqi Chen). The ATS check reads the text back out of the PDF and confirms one page, your name, companies, and section titles, an email and a phone number, the posting's keywords, no banned phrases, no new numbers, no placeholders, no repeated bullets, and no three bullets in a row with the same opening word. A role whose PDF did not build, ran over a page, or failed a check shows `⚠` next to its score.

| Key | Does |
|---|---|
| `t` | Tailor a scored role now |
| `v` | Open its PDF |
| `y` | Copy a prompt for Claude.ai that returns the `.tex` by hand |

Check a `.tex` you got back that way:

```sh
uv run python -m hunt resume check resume.tex --job <posting url>
uv run python -m hunt resume prompt <posting url>
```

Folders from before the PDF output hold `resume.md` and are left as they are.

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

| Key | Does |
|---|---|
| `o` | Open the apply link |
| `s` | Claude score, only after the classifier says keep. `S` on the full role scores anyway |
| `A` | Score every unscored role on the list in one batch, within today's budget |
| `t` / `v` / `y` | Tailor now / open the PDF / copy the Claude.ai prompt |
| `a` / `u` | I applied / undo it (in the Applied view) |
| `x` | Not for me. It stays hidden |
| `c` | The company's class: product, services, staffing, or training. It sticks for every posting from that company |
| `k` | Keep this role even though a worth rule hid or flagged it |
| `p` / `r` | Preferences / the master resume |
| `,` | Settings: schedule, check now, classifier, keys, companies, workflows |
| `h` / `tab` | Hidden roles and why / applied roles |
| `?` | All keys |

The list is ordered newest posting first.

Clef-flash is optional. It is a local model that sorts postings for free, instead of Haiku, if you run Ollama. It returns a probability. It does not rewrite the posting. The boilerplate strip is plain Python and happens before Clef-flash or Claude sees the text. The desk calls Ollama at `http://127.0.0.1:11434/v1/systemone`. Pull the model once:

```
ollama pull clef-flash
```

`CLEF_URL` and `CLEF_MODEL` override the address and the model name.

Claude Haiku is the default for a fresh clone, with no Ollama needed. With Clef-flash selected and Ollama down, sorting falls back to Haiku when a key is set. Switch in Settings (`,`), or set `"classifier"` in `profile/models.json`. Haiku asks the same two questions through the Claude API and answers yes or no, so the desk shows `Haiku keep` with no probability. It needs `ANTHROPIC_API_KEY`, costs about $0.0003 a role within the sorting cap, and scheduled checks use it too. The model and effort are the `classify` step in `profile/models.json`. Roles already classified keep their decision after a switch. `uv run python -m hunt classify --limit 15` classifies from a terminal with whichever one is selected.

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
uv run python -m unittest discover -s hunt -p 'test_*.py' -t .
```

Scheduling, and why this is a skill rather than a Claude dynamic workflow, is in `docs/claude.md`.
