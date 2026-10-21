# Sources

Turn a source on by adding its name to `sources` in `profile/search.json`. A key in `.env` does nothing until the name is listed. A listed source with a missing key prints `skip <name>: set <VAR>` and the run continues.

Probed on 2026-10-15. These returned JSON with no key, and they are on in the shipped `search.json`:

| Name | Endpoint |
|---|---|
| greenhouse | `https://boards-api.greenhouse.io/v1/boards/<slug>/jobs` |
| lever | `https://api.lever.co/v0/postings/<slug>?mode=json` |
| ashby | `https://api.ashbyhq.com/posting-api/job-board/<slug>` |
| arbeitnow | `https://www.arbeitnow.com/api/job-board-api` |
| jobicy | `https://jobicy.com/api/v2/remote-jobs` |
| muse | `https://www.themuse.com/api/public/jobs` |
| himalayas | `https://himalayas.app/jobs/api` |

Workday boards are searched, not read whole: a big employer lists thousands of roles worldwide. The request is `POST https://<tenant>.<host>.myworkdayjobs.com/wday/cxs/<tenant>/<site>/jobs`, once per keyword in `search.json`, up to 60 results each. When the board has a country filter for every country in `location` (`countries` plus `remote_only_countries`), each country is searched separately, and a posting listed as "2 Locations" is labelled with that country so the place screen can read it. A board with no country filter is searched worldwide and the place screen does the rest. A board costs about 1 + keywords × countries × 3 requests a run.

Keyed adapters are listed in the shipped `search.json`. With an empty `.env` they print `skip` and are not called. Set the variables from `.env.example`, and for Adzuna set `source_options.adzuna.country`. Remove a name from `sources` to turn it off:

| Name | Variables | Also required |
|---|---|---|
| adzuna | `ADZUNA_APP_ID`, `ADZUNA_APP_KEY` | `source_options.adzuna.country` (no default country) |
| jooble | `JOOBLE_API_KEY` | |
| findwork | `FINDWORK_API_KEY` | |
| reed | `REED_API_KEY` | |
| usajobs | `USAJOBS_API_KEY`, `USAJOBS_EMAIL` | |

AI Dev Jobs did not resolve. Fantastic.jobs, TheirStack, Jobspipe, and Jobven are commercial catalogs without a stable no-key URL, so they have no adapter. The form-filling Job Application API is out of scope.

JobSpy (`python-jobspy`) is the `jobspy` source. The search uses Naukri, LinkedIn, Indeed, and Glassdoor. It runs one search for each of the first four `keywords` in each city in `source_options.jobspy.locations`. Both come from preferences: the first four target roles and the cities. Set `search_term` to search one phrase instead. The schedule runs each site as its own unit (`jobspy:linkedin`, `jobspy:naukri`) and sets `hours_old` to the time since that site's last clean run, so `hours_old` in `search.json` only applies when no schedule state exists. Indeed and Glassdoor search the country site named by `country_indeed`, which preferences set from your first country (India gives `in.indeed.com` and `glassdoor.co.in`). Google Jobs returned no job data when probed on 2026-10-15, so it is off. ZipRecruiter covers the US and Canada, Bayt the Middle East, BDJobs Bangladesh, and HelloWork France; add one to `sites` only if you want that market.

When remote work is allowed, preferences also write `remote_countries`. Each one gets its own remote search per keyword (for example `ai engineer @ United States remote`), on top of the city searches. Naukri runs only the India searches.

JobSpy needs no API key. `JOBSPY_PROXIES` in `.env` is optional (`host:port` values separated by commas). `hours_old` is 48 when no schedule state exists, so the first pull is the last two days.

`fetch_description` is on unless you set it to `false`. JobSpy then opens each posting page, so the classifier and the years screen read the posting and not just the title. That is one extra request per posting, and a full pull takes a few minutes. A page that does not load leaves that row without a description. The classifier redoes a title-only decision once a later pull brings the text. Naukri's experience range goes at the top of the description, for example `Experience: 4-9 years.`

A company with 1000 or more employees is treated as a big organisation for the years screen. Naukri and LinkedIn do not send company size, so their rows are never big. The direct apply URL is `job_url_direct` when the board sends one.

The desk and `hunt search` print one line per JobSpy search, labelled with the site, for example `jobspy:naukri ai engineer @ Pune: 15 listings`. A board that blocks a search (LinkedIn 429, Naukri 406) prints `error jobspy:linkedin <keyword> @ <city>: ...`, the other boards' rows are kept, and the schedule backs that site off.

Aggregator rows are leads. Greenhouse, Lever, Ashby, and Workday URLs are the posting.

`python3 -m hunt sources` prints `on`, `off`, or `skip`. It never prints a key.
