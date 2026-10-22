# hunt-desk

A local job search you can point at any role, from engineering to product management. You upload a resume, pick the titles you want, and the search runs on its own: it fetches postings, sorts them, scores the ones worth a look, and writes a one-page PDF resume for the best of them. Claude spend stays inside two daily caps you set.

It runs on your Mac. Your resume, preferences, and results are files in this folder, and git ignores them.

The shape comes from [How to Build a Job Hunt System with Claude Code](https://www.scottypeterson.net/blog/how-to-build-a-job-hunt-system-with-claude-code).

## How it works

1. **Fetch.** Every 15 minutes a background check pulls new postings from the boards that are due: the Greenhouse, Lever, Ashby, and Workday boards of companies on your watchlist, keyless job feeds, any keyed APIs you add, and JobSpy.
2. **Screen.** Plain Python hides roles with the wrong title, place, seniority, job type, pay, company size, or posting age. It also hides staffing and training firms and US remote roles you cannot take. `h` shows each hidden role with the reason. Roles hidden for age or because they closed are removed at the end of each check, and a board cannot add them back. A role scored above the cutoff is never hidden for age, so it waits on the list until you apply, hide it, or it closes.
3. **Sort.** Claude Haiku reads your resume and the posting, names the deciding fact, and says keep or skip. Clef-flash can do this on your machine instead.
4. **Score.** Claude scores each kept role from 0 to 100 against your resume, using only the rules in `profile/rubric.json`.
5. **Tailor.** Each role above the cutoff gets a one-page PDF resume, and an ATS check reads the text back out of the PDF.

## What you need

- **A Mac.** `./start` installs a macOS build of the TeX engine, background checks run through launchd, and notifications use AppleScript.
- **An [Anthropic API key](https://platform.claude.com/settings/keys).** It pays for sorting, scoring, and tailoring, within the caps in [What it costs](#what-it-costs).

`./start` installs [uv](https://docs.astral.sh/uv/), which fetches Python 3.11 or newer if you do not have it.

Optional:

| Tool | What it adds |
|---|---|
| [Claude Code](https://claude.com/claude-code), with `claude` on your PATH | `s` and `t` run through `claude -p`. Without it they use the Messages API with your key. Also the `/job-hunt` skill |
| [Ollama](https://ollama.com) with `clef-flash` | Sorts postings on your machine for free, instead of Haiku |
| `terminal-notifier` (`brew install terminal-notifier`) | A notification click opens the desk |
| Keys for Adzuna, Jooble, Findwork, Reed, or USAJobs | More boards. See [`docs/sources.md`](docs/sources.md) |
| An Overleaf Git token | Rework a resume that lives on Overleaf |

## Start here

```sh
git clone https://github.com/symaticvisuals/hunt-desk.git
cd hunt-desk
./start
```

`./start` installs uv, the Python packages, and Tectonic (the TeX engine that builds the PDFs, about 30 MB, no sudo). It copies the starting files in `profile/defaults/` and `data/defaults/` into place and opens the desk. Run it again any time; it skips what is already done. `./start --no-tex` skips Tectonic, and resumes are then written as `.tex` only.

The desk walks you through three screens:

1. **API key.** Saved in `.env` and never printed. You can skip it and add it later in Settings (`,`).
2. **Resume.** A PDF, Word file, or pasted text. Claude can rework weak lines, or you keep it as is.
3. **What you want.** Claude proposes titles from the resume, including adjacent ones it marks `adjacent`. Each title shows how many openings the boards have right now, as the count comes in. Edit titles, places, the most years a posting may ask for, how fresh a posting must be, and the two daily caps, then save. Saving turns on background checks every 15 minutes.

After that, change preferences, the master resume, keys, and companies in the desk. It writes the profile files for you.

## Using the desk

| Key | Does |
|---|---|
| `o` | Open the apply link |
| `s` | Claude score, only after the classifier says keep. `S` on the full role scores anyway |
| `A` | Score every unscored role on the list in one batch, within today's budget |
| `t` / `v` / `y` | Tailor now / open the PDF / copy the Claude.ai prompt |
| `a` / `u` | I applied / undo it (in the Applied view) |
| `x` | Not for me. It stays hidden |
| `space` / `*` / `esc` | Mark a role / mark every role in the view / clear the marks. With roles marked, `a`, `x`, `u`, `s`, `t`, and `o` act on all of them, and three or more ask first |
| `c` | The company's class: product, services, staffing, or training. It sticks for every posting from that company |
| `k` | Keep this role even though a worth rule hid or flagged it |
| `p` / `r` | Preferences / the master resume, or a new one |
| `,` | Settings: schedule, check now, quiet hours, classifier, API keys, add company, workflows |
| `/` | Filter the view as you type: plain words match company, role, place, source, and reason; `score>80`, `score:none`, `src:workday`, `why:stretch`, `days:7`, and `maybe` narrow it; `-` inverts a token. `esc` clears it |
| `h` / `tab` | Hidden roles and why / applied roles |
| `?` | All keys |

The list is ordered newest posting first. A `?` next to a role means a worth rule flagged it, and the reason is shown with it.

## What it costs

Two caps in Step 2 (or `p` later) bound what background checks spend each day:

| Cap | Default | Pays for |
|---|---|---|
| Sorting | $0.50 a day | Claude Haiku deciding whether each new posting is worth reading, about $0.0003 a role |
| Scoring and resumes | $3.00 a day | Claude scoring each kept role, and a one-page resume for each role above the cutoff |

Batches are reserved at an upper-bound estimate before they go and settle at the billed amount when they end, so a cap is never passed by the background. A cap of 0 turns that step off. The status line shows today's spend, such as `classify $0.12/$0.50 · score $1.40/$3.00`. Pressing `A` can send more than fits if you say so.

## What leaves your machine

hunt has no server and sends no telemetry. It talks to these, and only when the step that needs them runs:

| Where | What is sent | When |
|---|---|---|
| Anthropic | Your master resume and the posting text | Sorting, scoring, tailoring, and the resume rewrite. A Managed Agents rewrite uploads the resume to that session |
| Job boards and feeds | Searches built from your titles and places, and requests for posting pages | Each background check, and `hunt search` |
| Overleaf | A Git clone or push of your project | Only when you give it a project URL |
| Ollama | Nothing leaves your machine. It runs at `127.0.0.1` | Only when Clef-flash is selected |

Keys live in `.env`, which git ignores, and hunt never prints them. Your files live in `profile/`, `data/`, `resumes/`, and `daily-digest/`, and git ignores all of them except the shipped defaults. If you fork this repo, run `git status` before a push anyway.

## Resumes

Every role that scores above the cutoff in `profile/rubric.json` gets a folder under `resumes/<company>/<role>/`:

| File | What it is |
|---|---|
| `resume.pdf` | One page on Jake Gutierrez's LaTeX template |
| `resume.tex`, `resume.json` | The source, and the structured answer it was built from |
| `notes.md` | What moved and why, what the job wants that the resume does not show, numbers to check, cuts made for one page, and the ATS check |
| `ats.json`, `jd.md` | The check results, and the posting |

Tailoring only reorders and reframes lines that are already in your master resume. It never adds an employer, title, date, number, or tool. The wording follows the humanizer skill in `prompts/humanizer/` (MIT, Siqi Chen). The ATS check reads the text back out of the PDF and confirms one page, your name, companies, and section titles, an email and a phone number, the posting's keywords, no banned phrases, no new numbers, no placeholders, no repeated bullets, and no three bullets in a row with the same opening word. A role whose PDF did not build, ran over a page, or failed a check shows `⚠` next to its score.

`t` tailors a scored role now, `v` opens its PDF, and `y` copies a prompt for Claude.ai that returns the `.tex` by hand. Check a `.tex` you got back that way:

```sh
uv run python -m hunt resume check resume.tex --job <posting url>
uv run python -m hunt resume prompt <posting url>
```

A resume tailored before a TeX engine was installed is kept as `.tex`. `./start` builds the missing PDFs once Tectonic is in place, or run:

```sh
uv run python -m hunt resume build
```

Folders from before the PDF output hold `resume.md` and are left as they are.

## Background checks

Saving preferences turns on a launchd agent that runs `hunt tick` every 15 minutes and once at login. A tick skips quiet hours (23:00 to 07:00 by default) and fetches only the boards that are due. It sorts the new roles, checks that shortlisted postings are still open, and sends a macOS notification when a role newly reaches the list. Then it tailors and scores within the daily caps, and removes roles hidden for age or because they closed. With both caps at 0, a tick never calls Claude. Each board's interval adapts: faster when it finds new roles, slower after empty runs, and backed off when it is blocked.

Turn it off or on in Settings (`,`), or from a terminal:

```sh
uv run python -m hunt schedule status
uv run python -m hunt schedule uninstall
uv run python -m hunt schedule install
```

Each step of a tick is in [`docs/schedule.md`](docs/schedule.md).

## Rework your resume

The first launch asks for a resume when `profile/og-resume.md` is missing or still a placeholder. A file that still contains the line `PLACEHOLDER_RESUME` opens the resume editor on launch. Choose a PDF, Word file, or text file, or paste the text. The desk fills title keywords and the years cap from the roles and dates it can read. Edit those fields, then save.

Before that save, the screen audits the resume: missing email, missing years, vague openers such as "Responsible for", impact claims with no number, and leftover TODO text. **Rewrite weak lines** follows the humanizer rules a program can apply without a model: weak openers, prose dashes, "serves as", and chat leftovers. It does not invent employers, tools, or numbers. An agent rewriting the same file by hand still follows the full humanizer skill.

An Overleaf project URL clones through Git into `resumes/overleaf/<id>`. Put the token from Overleaf's Menu, then Git, in `OVERLEAF_GIT_TOKEN`. The username is `git`. A rewrite edits the `.tex` and keeps a `.bak`. It does not publish until you run:

```sh
uv run python -m hunt overleaf push <project-id>
```

To start over with another resume, press `r` in the desk and choose a file, or run:

```sh
uv run python -m hunt setup path/to/resume.pdf
```

### The full rewrite

The longer rewrite is `resume-rewrite`. It inventories the file, waits for your numbers, rewrites up to four roles, then checks that nothing was invented. A clean run writes `resumes/updated-resume.md`, refreshes `profile/og-resume.md`, and keeps the previous master as `profile/og-resume.md.bak`. Two things keep the master as it is and leave the draft for you to paste in: a `[metric]` that still needs a number, or a resume with no experience heading to replace. It then searches, asks the classifier which roles to keep, and scores those. A failed fact check leaves the master resume unchanged and does not search.

In Settings (`,`), choose Workflows, then the `api` row. That calls the Claude API from this app. If `ANTHROPIC_API_KEY` is missing, the desk asks for it and saves it in `.env`. A terminal run of `hunt run api` or `hunt run managed` asks the same way. The value is never printed. Change it later in Settings, under API keys. The same run from the shell:

```sh
uv run python -m hunt run api resume-rewrite
```

`profile/models.json` picks the model and effort for each step. Inventory uses Haiku at low effort. Each role rewrite uses Sonnet at high effort. The voice pass uses Opus at medium effort. The fact check uses Sonnet, so the model that wrote the final prose is not the model that judges it. On the rate card from 2026-10-06 that is $0.10/$0.50, $2/$10, and $4/$20 per million input/output tokens.

If the inventory finds a missing number and you have not supplied answers, the run stops and writes the questions to `data/resume-rewrite.md`. Put one answer per question in `data/resume-answers.md` (or the answers field) and run it again. Unknown is a valid answer. Optional job text aims the voice pass at one posting.

### Managed Agents

The `managed` row runs the same rewrite as one Claude Managed Agents session. Anthropic hosts the loop. Haiku inventories, Sonnet rewrites each role, Opus writes the draft, and a second Sonnet agent fact-checks it. That check is an extra pass on every run. The session stops at an $8 list-price budget. Dynamic workflows are off, because four roles fit in one session.

The agents are files under `agents/`, and the cloud environment is `environments/resume-rewrite.yaml`. Sync them once, after reading the plan:

```sh
ant apply --dry-run agents environments
ant apply --yes agents environments
```

`ant apply` writes `claude-lock.json`. Commit that file. Then:

```sh
uv run python -m hunt run managed resume-rewrite
```

The resume is uploaded for that session and mounted read-only. The draft comes back to `data/resume-rewrite.md`. Web search is off. A tool call that pauses for approval is denied, because the desk is not watching the container.

The `grok` and `claude` rows open those apps on the same workflow. `/resume-rewrite` inside Grok does that too.

## Sorting with Clef-flash

Clef-flash is optional. It is a local model that sorts postings for free, instead of Haiku, if you run Ollama. It returns a probability. It does not rewrite the posting. The boilerplate strip is plain Python and happens before Clef-flash or Claude sees the text. The desk calls Ollama at `http://127.0.0.1:11434/v1/systemone`. Pull the model once:

```sh
ollama pull clef-flash
```

`CLEF_URL` and `CLEF_MODEL` override the address and the model name.

Claude Haiku is the default for a fresh clone, with no Ollama needed. With Clef-flash selected and Ollama down, sorting falls back to Haiku when a key is set. Switch in Settings (`,`), or set `"classifier"` in `profile/models.json`. Haiku reads the whole master resume, kept in the cached part of the prompt, and up to 4,000 characters of the posting. It names the deciding fact in one sentence, then answers yes or no and a fit level, so the desk shows `Haiku keep` with no probability and a `Why:` line under it. It needs `ANTHROPIC_API_KEY`, costs about $0.0002 a role within the sorting cap, and scheduled checks use it too. The model and effort are the `classify` step in `profile/models.json`. Roles already classified keep their decision after a switch. `uv run python -m hunt classify --limit 15` classifies from a terminal with whichever one is selected.

To measure a classifier, `uv run python -m hunt.classify_eval` runs Haiku and Clef-flash on every role Claude already scored, on a scratch copy of the profile, so the real decisions and spend stay as they are. It prints how many roles each one passes, how many of Claude's 80+ roles it keeps, and how well its fit ranks Claude's scores, and adds the run to `data/classify-eval.json`. Pass `--workers 1` for a local Ollama that drops connections.

## Sources

The shipped `profile/search.json` turns on the Greenhouse, Lever, Ashby, and Workday boards of the 20 companies in `data/watchlist.json`, the keyless feeds (Arbeitnow, Jobicy, The Muse, Himalayas, freehire), and JobSpy (LinkedIn, Indeed, Glassdoor, Naukri). Adzuna, Jooble, Findwork, Reed, and USAJobs are listed too, and skip until their keys are in `.env`. Every endpoint and option is in [`docs/sources.md`](docs/sources.md). [`docs/ats-slugs.md`](docs/ats-slugs.md) shows how a careers page becomes a watchlist row, and the desk adds companies from Settings.

## Command line

Open the desk from this directory:

```sh
uv run python -m hunt
```

Every command runs as `uv run python -m hunt <command>`:

| Command | Does |
|---|---|
| `search` | Fetches every source and writes `data/last-search.json`. It does not score |
| `sources` | Prints `on`, `off`, or `skip` for each source. It never prints a key |
| `classify --limit 15` | Sorts unsorted roles with the selected classifier |
| `score` | Runs `/job-hunt` through `claude -p` on the last search |
| `batch` | Lists open batches and applies the finished ones |
| `tick` | One background check, the same one launchd runs |
| `schedule install`, `status`, `uninstall` | The background checks |
| `resume check`, `prompt`, `build` | See [Resumes](#resumes) |
| `setup <file or Overleaf URL>` | Reads a resume into the profile and sets the title keywords and years cap. `--rewrite` edits weak lines in a `.tex` |
| `run api resume-rewrite`, `run managed resume-rewrite` | See [The full rewrite](#the-full-rewrite) |
| `sameness` | How much tailored resumes share, line by line |
| `funnel` | Where every inbox role ends up, stage by stage |
| `last` | Lists the roles in the last search |
| `cleanup` | Removes duplicate scored rows, then the old roles and stored data a check would prune |
| `seed` | Copies the shipped defaults into place. It never overwrites |
| `tex-check` | Compiles a sample resume to test the TeX engine |
| `paths` | Prints the files you can edit by hand |

Paste one JSON object per line into `python3 -m hunt dedupe` to mark `NEW` or `DUP` without opening the CSV.

## Use it from Claude Code

`CLAUDE.md` holds the rules an agent follows in this repo: the rubric, what it may not invent, and which files it must leave alone. In Claude Code, `/job-hunt` runs the search, scores the new roles, and writes a digest under `daily-digest/`. [`docs/claude.md`](docs/claude.md) covers which Claude Code feature fits which step and how to schedule it, and [`docs/daily-run.md`](docs/daily-run.md) is the prompt for a scheduled run.

## Use it with care

- Read every tailored resume before you send it. The checks catch new numbers, placeholders, and repeated lines, but the resume goes out under your name.
- A score is a model's reading of the posting against your rubric. Use it to decide what to read first.
- JobSpy reads LinkedIn, Indeed, Glassdoor, and Naukri pages, and those sites' terms limit automated access. freehire's anonymous API is for personal, non-commercial use. You are responsible for how you use each source. Remove a name from `sources` in `profile/search.json` to turn it off.

## Tests

```sh
uv run python -m unittest discover -s hunt -p 'test_*.py' -t .
```

## Credits

- [Jake Gutierrez's resume template](https://github.com/jakegut/resume) (MIT), based on [sb2nov/resume](https://github.com/sb2nov/resume), in `templates/jake.tex`.
- The humanizer skill by Siqi Chen (MIT), in `prompts/humanizer/`.
- [JobSpy](https://github.com/speedyapply/JobSpy), [Textual](https://textual.textualize.io), and [Tectonic](https://tectonic-typesetting.github.io).

## License

MIT. See [`LICENSE`](LICENSE). `prompts/humanizer/` and `templates/jake.tex` keep their own MIT notices.
