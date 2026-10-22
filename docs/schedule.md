# Schedule

The scraper runs in the background through launchd on macOS. Saving preferences in Step 2 turns it on. Turn it off or on again from Settings, or from a terminal:

```sh
uv run python -m hunt schedule install
uv run python -m hunt schedule status
uv run python -m hunt schedule uninstall
```

`install` writes `~/Library/LaunchAgents/com.hunt.tick.<hash>.plist`. The agent runs `uv run python -m hunt tick` every 15 minutes, and once at login. It also writes `data/open-desk.command`, which a notification click opens when `terminal-notifier` is installed (`brew install terminal-notifier`). Without it the banner has no click action.

## What a tick does

1. Exits at once during quiet hours (23:00 to 07:00 by default, set in Settings).
2. Exits when another check is running.
3. Picks the boards that are due. Exits when none are.
4. Fetches them and appends new roles to `data/inbox.json`. A URL listed in `data/pruned.json` is skipped.
5. Fetches the posting text, up to 30 a tick, for Greenhouse and Workday roles that came without it, or with only a requisition ID or a stub under 80 characters, and could still reach the list. Text that replaces a stub drops the classifier's earlier decision, so the role is judged again with the posting. A role whose text is still empty after two tries is hidden as "no description".
6. Classifies new roles with the classifier in `profile/models.json`: Haiku for a fresh clone, or Clef-flash. With Clef-flash selected and Ollama down, a key sends them to Haiku instead. Haiku stops for the day at the sorting cap; the rest wait as `waiting: budget`. If the Claude API fails, the roles wait as "not classified yet" and the next tick retries.
7. Checks up to 30 shortlisted postings a tick, each at most once a day, and hides closed ones.
8. Sends roles above the cutoff that have no resume yet to a tailoring batch, then sends unscored roles on the main list to a scoring batch: clean roles first, then roles marked `?`, by classifier fit, then newest. Both stay inside the scoring cap.
9. Records each board's result in `data/schedule.json` and writes a line per board to `data/schedule.log` (last 500 lines).
10. Removes roles hidden as stale, posted too long ago, or closed from the inbox, and adds their URLs to `data/pruned.json` so a board that still lists one cannot add it again. Dismissed and applied roles stay, and so does a role scored above `screenout_cap` until it closes. Classifier decisions for URLs in neither the inbox nor `data/last-search.json` go too, and so do the posting copies in finished batches; a finished batch keeps its summary and the roles that failed. `hunt cleanup` runs the same prune by hand.
11. Sends a macOS notification when a role reaches the main list for the first time. The very first run after install does not notify.

A tick with no board due still does step 8.

Every tick outside quiet hours also checks `data/batches.json`. A finished Batch API batch is applied at once (scores written, the tailoring batch sent, or tailored resumes written), even when no board is due, and a notification says what happened.

A tick calls Claude only within the daily caps in `profile/preferences.json`, and never before those caps have been saved once. With both caps at 0, a tick never calls Claude. Spend is kept in `data/spend.json`. `data/schedule.out` catches anything launchd itself prints, such as a crash.

## Parallel work

| Step | At once | Change it in |
|---|---|---|
| Boards and JobSpy sites | up to 8 units | `FETCH_WORKERS` in `hunt/search.py` |
| Watchlist boards on one platform | 4 | `BOARD_WORKERS` in `hunt/search.py` |
| Searches inside one JobSpy site | 1, so LinkedIn and Naukri see one request at a time | |
| Classification (Clef-flash or Haiku) | 3 | `CLEF_PARALLEL` in `.env` or the environment |
| Live checks | 8 | `LIVE_WORKERS` in `hunt/schedule.py` |
| Claude scoring and tailoring | 3 | `SCORE_WORKERS` in `hunt/cli.py` |

Ollama answers parallel requests only up to its own `OLLAMA_NUM_PARALLEL`; extra Clef-flash requests wait in its queue. Each Claude scoring run keeps its packet and logs in `data/work/<role>/`, so runs never share a file.

## Intervals

Each board is its own unit. JobSpy is split by site, so a LinkedIn block never delays Naukri.

| Unit | Base | Fastest | Slowest |
|---|---|---|---|
| Greenhouse, Lever, Ashby, Workday | 60 min | 30 min | 4 h |
| Arbeitnow, Jobicy, Muse, Himalayas, keyed feeds | 2 h | 1 h | 8 h |
| `jobspy:linkedin`, `jobspy:naukri`, other JobSpy sites | 4 h | 2 h | 16 h |

- A run that adds new matching roles shortens the interval by a quarter, down to the fastest.
- Three empty runs in a row lengthen it by half, up to the slowest.
- A block (HTTP 429, 403, or 406, a captcha, a network error, or failures with no rows) waits base × 2ⁿ, at most 24 hours. One clean run resets it. Rows that arrived before the block are kept.
- Each next run moves by up to 10% either way.
- JobSpy asks only for postings since that board's last clean run, plus two hours of overlap (6 to 168 hours; 48 on the first run). The window never goes past `max_posted_days` in `profile/worth.json` plus a day, so a board that was blocked for days does not spend its results on postings the desk hides.

`hunt search` and Check now in the desk run every board at once, ignore quiet hours, and also write `data/last-search.json`.

An open desk does not fetch on its own. It checks the files a tick writes every 15 seconds and reloads when one changed, so new roles show up without a restart.
