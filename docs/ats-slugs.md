# How a careers URL becomes a watchlist row

Open the company's careers page and see where "view open roles" goes.

| URL | `platform` | What to copy |
|---|---|---|
| `boards.greenhouse.io/<slug>` or `job-boards.greenhouse.io/<slug>` | `greenhouse` | `slug` |
| `jobs.lever.co/<slug>` | `lever` | `slug` |
| `jobs.ashbyhq.com/<slug>` | `ashby` | `slug` |
| `https://<tenant>.<host>.myworkdayjobs.com/.../<site>` | `workday` | `tenant`, `host` (`wd1`, `wd5`, …), `site` |

Add a row to `data/watchlist.json` only after you want that company watched:

```json
{"company": "Example", "platform": "greenhouse", "slug": "example"}
```

Workday needs the extra fields:

```json
{"company": "Example", "platform": "workday", "tenant": "example", "host": "wd5", "site": "External"}
```

A Workday board is searched with each keyword in `profile/search.json`, inside your countries when the board has a country filter. See `docs/sources.md`.

The digest may suggest a row. You add it. The CLI does not write the watchlist.
