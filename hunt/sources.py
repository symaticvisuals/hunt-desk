"""Job-source adapters. Keys come from the environment. Nothing here is role-specific."""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from dataclasses import dataclass
from html import unescape
from pathlib import Path

from hunt.screen import posted_on

USER_AGENT = "job-hunt/1.0"
_TAG = re.compile(r"(?is)<(script|style).*?>.*?</\1>")
_HTML = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"\s+")


class SourceError(Exception):
    pass


@dataclass
class Job:
    company: str
    title: str
    url: str
    platform: str
    job_id: str
    location: str
    description: str
    source: str
    board: str = ""
    apply_url: str = ""
    posted_at: str = ""
    big: bool = False
    size_known: bool = False
    pay_min: float | None = None
    pay_max: float | None = None
    pay_currency: str = ""
    pay_interval: str = ""
    job_type: str = ""
    work_mode: str = ""

    def as_dict(self) -> dict:
        return {
            "company": self.company,
            "title": self.title,
            "role": self.title,
            "url": self.url,
            "apply_url": self.apply_url or self.url,
            "platform": self.platform,
            "job_id": self.job_id,
            "location": self.location,
            "description": self.description,
            "source": self.source,
            "board": self.board,
            "posted_at": self.posted_at,
            "big": self.big,
            "size_known": self.size_known,
            "pay_min": self.pay_min,
            "pay_max": self.pay_max,
            "pay_currency": self.pay_currency,
            "pay_interval": self.pay_interval,
            "job_type": self.job_type,
            "work_mode": self.work_mode,
        }


@dataclass
class SourceSpec:
    name: str
    env: tuple[str, ...] = ()
    kind: str = "feed"


def html_to_text(value: str, limit: int = 8000) -> str:
    text = unescape(value or "")
    text = _TAG.sub(" ", text)
    text = _HTML.sub(" ", text)
    text = _SPACE.sub(" ", text).strip()
    return text[:limit]


def read_env_file(path: Path) -> dict[str, str]:
    """KEY=value lines from a .env file. Values are never logged."""
    if not path.exists():
        return {}
    found: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        if key:
            found[key] = value
    return found


def load_env(path: Path) -> None:
    """Load KEY=value lines. Existing process environment wins."""
    for key, value in read_env_file(path).items():
        if key not in os.environ:
            os.environ[key] = value


def get_json(
    url: str,
    *,
    label: str,
    method: str = "GET",
    body: dict | None = None,
    headers: dict[str, str] | None = None,
    timeout: int = 25,
) -> dict | list:
    data = None if body is None else json.dumps(body).encode()
    request_headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if body is not None:
        request_headers["Content-Type"] = "application/json"
    if headers:
        request_headers.update(headers)
    request = urllib.request.Request(url, data=data, headers=request_headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        raise SourceError(f"HTTP {exc.code} from {label}") from exc
    except urllib.error.URLError as exc:
        raise SourceError(f"network error from {label}: {exc.reason}") from exc
    try:
        return json.loads(raw.decode())
    except json.JSONDecodeError as exc:
        raise SourceError(f"not JSON from {label}") from exc


def _mark(job: Job, *, apply: str = "", posted="", big: bool = False, size_known: bool = False) -> Job:
    job.apply_url = apply or job.url
    job.posted_at = posted_on(posted)
    job.big = big
    job.size_known = size_known
    return job


def _location(*parts: str, remote: bool = False) -> str:
    bits = [part.strip() for part in parts if part and part.strip()]
    if remote and not any("remote" in bit.lower() for bit in bits):
        bits.append("Remote")
    return "; ".join(bits)


def fetch_greenhouse(board: dict) -> list[Job]:
    slug = board["slug"]
    company = board.get("company") or slug
    payload = get_json(
        f"https://boards-api.greenhouse.io/v1/boards/{urllib.parse.quote(slug)}/jobs",
        label=f"greenhouse {slug}",
    )
    jobs: list[Job] = []
    for item in payload.get("jobs", []):
        location = item.get("location") or {}
        url = item.get("absolute_url") or ""
        jobs.append(
            _mark(
                Job(
                    company=item.get("company_name") or company,
                    title=item.get("title") or "",
                    url=url,
                    platform="greenhouse",
                    job_id=str(item.get("id") or ""),
                    location=_location(location.get("name", "") if isinstance(location, dict) else str(location)),
                    description="",
                    source="greenhouse",
                    board=slug,
                ),
                apply=url,
                posted=item.get("first_published") or item.get("updated_at"),
                big=board.get("big", True),
                size_known=True,
            )
        )
    return jobs


def hydrate_greenhouse(job: Job) -> None:
    if job.description or not job.board or not job.job_id:
        return
    payload = get_json(
        f"https://boards-api.greenhouse.io/v1/boards/{urllib.parse.quote(job.board)}/jobs/{job.job_id}",
        label=f"greenhouse {job.board}/{job.job_id}",
    )
    job.description = html_to_text(payload.get("content") or "")


def fetch_lever(board: dict) -> list[Job]:
    slug = board["slug"]
    company = board.get("company") or slug
    payload = get_json(
        f"https://api.lever.co/v0/postings/{urllib.parse.quote(slug)}?mode=json",
        label=f"lever {slug}",
    )
    jobs: list[Job] = []
    for item in payload:
        categories = item.get("categories") or {}
        workplace = item.get("workplaceType") or ""
        url = item.get("hostedUrl") or ""
        jobs.append(
            _mark(
                Job(
                    company=company,
                    title=item.get("text") or "",
                    url=url,
                    platform="lever",
                    job_id=str(item.get("id") or ""),
                    location=_location(categories.get("location") or "", remote=workplace.lower() == "remote"),
                    description=html_to_text(item.get("descriptionPlain") or item.get("description") or ""),
                    source="lever",
                    board=slug,
                ),
                apply=item.get("applyUrl") or url,
                posted=item.get("createdAt"),
                big=board.get("big", True),
                size_known=True,
            )
        )
    return jobs


def fetch_ashby(board: dict) -> list[Job]:
    slug = board["slug"]
    company = board.get("company") or slug
    payload = get_json(
        f"https://api.ashbyhq.com/posting-api/job-board/{urllib.parse.quote(slug)}",
        label=f"ashby {slug}",
    )
    jobs: list[Job] = []
    for item in payload.get("jobs", []):
        if item.get("isListed") is False:
            continue
        url = item.get("jobUrl") or item.get("applyUrl") or ""
        jobs.append(
            _mark(
                Job(
                    company=company,
                    title=item.get("title") or "",
                    url=url,
                    platform="ashby",
                    job_id=str(item.get("id") or ""),
                    location=_location(item.get("location") or "", remote=bool(item.get("isRemote"))),
                    description=html_to_text(item.get("descriptionPlain") or ""),
                    source="ashby",
                    board=slug,
                ),
                apply=item.get("applyUrl") or url,
                posted=item.get("publishedAt") or item.get("publishedDate"),
                big=board.get("big", True),
                size_known=True,
            )
        )
    return jobs


WORKDAY_PAGE = 20
WORKDAY_PAGES = 3
_MANY_LOCATIONS = re.compile(r"^\s*\d+\s+locations?\s*$", re.IGNORECASE)


def fetch_workday(board: dict, search: dict | None = None) -> list[Job]:
    """A big Workday board lists thousands of roles, so each keyword is searched inside the target countries."""
    keywords = [str(word).strip() for word in (search or {}).get("keywords") or [] if str(word).strip()]
    if not keywords:
        return _workday_query(board, "", {}, "")
    first = _workday_post(board, "", {}, 0)
    places = _workday_countries(first.get("facets"), _target_countries(search)) or [("", {})]
    found: dict[str, Job] = {}
    for country, facets in places:
        for keyword in keywords:
            for job in _workday_query(board, keyword, facets, country):
                found.setdefault(job.url or job.job_id, job)
    return list(found.values())


def _workday_post(board: dict, keyword: str, facets: dict, offset: int) -> dict:
    tenant = board["tenant"]
    host = (board.get("host") or "wd1").removesuffix(".myworkdayjobs.com")
    return get_json(
        f"https://{tenant}.{host}.myworkdayjobs.com/wday/cxs/{tenant}/{board['site']}/jobs",
        label=f"workday {tenant}",
        method="POST",
        body={"appliedFacets": facets, "limit": WORKDAY_PAGE, "offset": offset, "searchText": keyword},
    )


def _workday_query(board: dict, keyword: str, facets: dict, country: str) -> list[Job]:
    """Workday sends the total only with the first page."""
    tenant = board["tenant"]
    host = (board.get("host") or "wd1").removesuffix(".myworkdayjobs.com")
    company = board.get("company") or tenant
    jobs: list[Job] = []
    total = 0
    for page in range(WORKDAY_PAGES):
        offset = page * WORKDAY_PAGE
        payload = _workday_post(board, keyword, facets, offset)
        if page == 0:
            total = int(payload.get("total") or 0)
        postings = payload.get("jobPostings") or []
        for item in postings:
            path = item.get("externalPath") or ""
            url = f"https://{tenant}.{host}.myworkdayjobs.com/{board['site']}{path}" if path else ""
            place = item.get("locationsText") or ""
            if country and _MANY_LOCATIONS.match(place):
                place = f"{country} ({place.strip().lower()})"
            jobs.append(
                _mark(
                    Job(
                        company=company,
                        title=item.get("title") or "",
                        url=url,
                        platform="workday",
                        job_id=path.rsplit("_", 1)[-1] if "_" in path else path,
                        location=_location(place),
                        description=html_to_text(" ".join(item.get("bulletFields") or [])),
                        source="workday",
                        board=tenant,
                    ),
                    apply=url,
                    posted=item.get("postedOn"),
                    big=board.get("big", True),
                    size_known=True,
                )
            )
        if len(postings) < WORKDAY_PAGE or offset + WORKDAY_PAGE >= total:
            break
    return jobs


def _target_countries(search: dict | None) -> list[str]:
    rule = (search or {}).get("location") or {}
    if rule.get("mode") in (None, "any", "remote_only"):
        return []
    names = list(rule.get("countries") or [])
    if rule.get("mode") == "places":
        names += list(rule.get("remote_only_countries") or [])
    return [str(name).strip() for name in names if str(name).strip()]


def _workday_countries(facets, countries: list[str]) -> list[tuple[str, dict]]:
    """One country filter per target country. Empty when any country has no filter, so the search stays global."""
    from hunt.preferences import country_terms

    places = []
    for country in countries:
        terms = set(country_terms(country))
        hit = _workday_facet(facets, terms)
        if hit is None:
            return []
        parameter, value = hit
        places.append((country, {parameter: [value]}))
    return places


def _workday_facet(facets, terms: set[str]) -> tuple[str, str] | None:
    """Boards name the country filter differently, and some nest it under a location group."""
    stack = [(facet.get("facetParameter") or "", facet) for facet in facets or [] if isinstance(facet, dict)]
    while stack:
        parameter, node = stack.pop()
        for value in node.get("values") or []:
            if not isinstance(value, dict):
                continue
            if str(value.get("descriptor") or "").strip().lower() in terms and value.get("id"):
                return parameter, value["id"]
            if value.get("values"):
                stack.append((value.get("facetParameter") or parameter, value))
    return None


DETAIL_PLATFORMS = {"greenhouse", "workday"}
DETAIL_TIMEOUT = 10
_WORKDAY_URL = re.compile(r"^https://([^./]+)\.([^./]+)\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?([^/]+)(/job/.+)$")


def fetch_description(row: dict) -> str:
    """The posting text for a listing that came without it. Empty for a platform with no detail endpoint."""
    platform = row.get("platform") or ""
    if platform == "greenhouse" and row.get("board") and row.get("job_id"):
        board = urllib.parse.quote(str(row["board"]))
        payload = get_json(
            f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs/{row['job_id']}",
            label=f"greenhouse {row['board']}/{row['job_id']}",
            timeout=DETAIL_TIMEOUT,
        )
        return html_to_text(payload.get("content") or "")
    found = _WORKDAY_URL.match(row.get("url") or "") if platform == "workday" else None
    if found:
        tenant, host, site, path = found.groups()
        payload = get_json(
            f"https://{tenant}.{host}.myworkdayjobs.com/wday/cxs/{tenant}/{site}{path}",
            label=f"workday {tenant} detail",
            timeout=DETAIL_TIMEOUT,
        )
        return html_to_text((payload.get("jobPostingInfo") or {}).get("jobDescription") or "")
    return ""


def fetch_board(platform: str, board: dict, search: dict | None = None) -> list[Job]:
    """One watchlist board. Workday is searched by keyword and country; the others list the whole board."""
    if platform == "workday":
        return fetch_workday(board, search)
    return ATS_FETCH[platform](board)


def fetch_arbeitnow(_search: dict, _env: dict[str, str]) -> list[Job]:
    payload = get_json("https://www.arbeitnow.com/api/job-board-api", label="arbeitnow")
    jobs: list[Job] = []
    for item in payload.get("data") or []:
        jobs.append(
            Job(
                company=item.get("company_name") or "",
                title=item.get("title") or "",
                url=item.get("url") or "",
                platform="arbeitnow",
                job_id=str(item.get("slug") or ""),
                location=_location(item.get("location") or "", remote=bool(item.get("remote"))),
                description=html_to_text(item.get("description") or ""),
                source="arbeitnow",
            )
        )
    return jobs


JOBICY_PAGE = 100
JOBICY_PAGES = 3


def fetch_jobicy(_search: dict, _env: dict[str, str]) -> list[Job]:
    """Remote roles, newest first. A page of 100 covers about eight hours, so a few pages cover a day."""
    jobs: list[Job] = []
    cursor = ""
    for _page in range(JOBICY_PAGES):
        query = {"count": JOBICY_PAGE, **({"cursor": cursor} if cursor else {})}
        payload = get_json(f"https://jobicy.com/api/v2/remote-jobs?{urllib.parse.urlencode(query)}", label="jobicy")
        for item in payload.get("jobs") or []:
            jobs.append(
                _mark(
                    Job(
                        company=item.get("companyName") or "",
                        title=item.get("jobTitle") or "",
                        url=item.get("url") or "",
                        platform="jobicy",
                        job_id=str(item.get("id") or ""),
                        location=_location(item.get("jobGeo") or "", remote=True),
                        description=html_to_text(item.get("jobDescription") or item.get("jobExcerpt") or ""),
                        source="jobicy",
                    ),
                    posted=item.get("pubDate"),
                )
            )
        cursor = payload.get("nextCursor") or ""
        if not payload.get("hasMore") or not cursor:
            break
    return jobs


def fetch_muse(_search: dict, _env: dict[str, str]) -> list[Job]:
    payload = get_json("https://www.themuse.com/api/public/jobs?page=1", label="muse")
    jobs: list[Job] = []
    for item in payload.get("results") or []:
        company = item.get("company") or {}
        locations = item.get("locations") or []
        place = "; ".join(loc.get("name", "") for loc in locations if isinstance(loc, dict))
        refs = item.get("refs") or {}
        jobs.append(
            Job(
                company=company.get("name") or "",
                title=item.get("name") or "",
                url=refs.get("landing_page") or "",
                platform="muse",
                job_id=str(item.get("id") or ""),
                location=_location(place),
                description=html_to_text(item.get("contents") or ""),
                source="muse",
            )
        )
    return jobs


HIMALAYAS_PAGE = 20
HIMALAYAS_PAGES = 2


def fetch_himalayas(search: dict, _env: dict[str, str]) -> list[Job]:
    """The feed adds thousands of roles a day, so each keyword is searched, newest first, in each country the person can work from."""
    keywords = _search_keywords(search)
    if not keywords:
        return [_himalayas_job(item) for item in get_json("https://himalayas.app/jobs/api?limit=20", label="himalayas").get("jobs") or []]
    countries = _target_countries(search) or [""]
    found: dict[str, Job] = {}
    for keyword in keywords:
        for country in countries:
            for page in range(1, HIMALAYAS_PAGES + 1):
                query = {"q": keyword, "sort": "recent", "page": page, **({"country": country} if country else {})}
                payload = get_json(f"https://himalayas.app/jobs/api/search?{urllib.parse.urlencode(query)}", label="himalayas")
                items = payload.get("jobs") or []
                for item in items:
                    job = _himalayas_job(item)
                    found.setdefault(job.job_id or job.url, job)
                if len(items) < HIMALAYAS_PAGE:
                    break
    return list(found.values())


def _himalayas_job(item: dict) -> Job:
    restrictions = item.get("locationRestrictions") or []
    place = "; ".join(restrictions) if isinstance(restrictions, list) else str(restrictions)
    return _mark(
        Job(
            company=item.get("companyName") or "",
            title=item.get("title") or "",
            url=item.get("applicationLink") or "",
            platform="himalayas",
            job_id=str(item.get("guid") or ""),
            location=_location(place, remote=True),
            description=html_to_text(item.get("description") or item.get("excerpt") or ""),
            source="himalayas",
        ),
        posted=item.get("pubDate"),
    )


def fetch_adzuna(search: dict, env: dict[str, str]) -> list[Job]:
    options = (search.get("source_options") or {}).get("adzuna") or {}
    country = options.get("country")
    if not country:
        raise SourceError("set source_options.adzuna.country in profile/search.json")
    jobs: list[Job] = []
    # Adzuna requires every word in `what`, so the roles are searched one at a time.
    for keyword in _search_keywords(search):
        query = urllib.parse.urlencode(
            {
                "app_id": env["ADZUNA_APP_ID"],
                "app_key": env["ADZUNA_APP_KEY"],
                "results_per_page": 20,
                "what": keyword,
                "sort_by": "date",
            }
        )
        payload = get_json(
            f"https://api.adzuna.com/v1/api/jobs/{urllib.parse.quote(str(country))}/search/1?{query}",
            label="adzuna",
        )
        for item in payload.get("results") or []:
            company = item.get("company") or {}
            location = item.get("location") or {}
            jobs.append(
                _mark(
                    Job(
                        company=company.get("display_name") or "",
                        title=item.get("title") or "",
                        url=item.get("redirect_url") or "",
                        platform="adzuna",
                        job_id=str(item.get("id") or ""),
                        location=_location(location.get("display_name") or ""),
                        description=html_to_text(item.get("description") or ""),
                        source="adzuna",
                    ),
                    posted=item.get("created"),
                )
            )
    return jobs


def fetch_jooble(search: dict, env: dict[str, str]) -> list[Job]:
    jobs: list[Job] = []
    for keyword in _search_keywords(search):
        for country in _target_countries(search) or [""]:
            payload = get_json(
                f"https://jooble.org/api/{urllib.parse.quote(env['JOOBLE_API_KEY'])}",
                label="jooble",
                method="POST",
                body={"keywords": keyword, "location": country, "page": 1},
            )
            for item in payload.get("jobs") or []:
                jobs.append(
                    _mark(
                        Job(
                            company=item.get("company") or "",
                            title=item.get("title") or "",
                            url=item.get("link") or "",
                            platform="jooble",
                            job_id=str(item.get("id") or ""),
                            location=_location(item.get("location") or ""),
                            description=html_to_text(item.get("snippet") or ""),
                            source="jooble",
                        ),
                        posted=item.get("updated"),
                    )
                )
    return jobs


def _search_keywords(search: dict) -> list[str]:
    return [str(word).strip() for word in search.get("keywords") or [] if str(word).strip()]


def fetch_findwork(search: dict, env: dict[str, str]) -> list[Job]:
    keywords = " ".join((search.get("keywords") or [])[:6])
    query = urllib.parse.urlencode({"search": keywords})
    payload = get_json(
        f"https://findwork.dev/api/jobs/?{query}",
        label="findwork",
        headers={"Authorization": f"Token {env['FINDWORK_API_KEY']}"},
    )
    jobs: list[Job] = []
    for item in payload.get("results") or []:
        jobs.append(
            Job(
                company=item.get("company_name") or "",
                title=item.get("role") or "",
                url=item.get("url") or "",
                platform="findwork",
                job_id=str(item.get("id") or ""),
                location=_location(item.get("location") or "", remote=bool(item.get("remote"))),
                description=html_to_text(item.get("text") or ""),
                source="findwork",
            )
        )
    return jobs


def fetch_reed(search: dict, env: dict[str, str]) -> list[Job]:
    import base64

    keywords = " ".join((search.get("keywords") or [])[:6])
    token = base64.b64encode(f"{env['REED_API_KEY']}:".encode()).decode()
    query = urllib.parse.urlencode({"keywords": keywords, "resultsToTake": 20})
    payload = get_json(
        f"https://www.reed.co.uk/api/1.0/search?{query}",
        label="reed",
        headers={"Authorization": f"Basic {token}"},
    )
    jobs: list[Job] = []
    for item in payload.get("results") or []:
        jobs.append(
            Job(
                company=item.get("employerName") or "",
                title=item.get("jobTitle") or "",
                url=item.get("jobUrl") or "",
                platform="reed",
                job_id=str(item.get("jobId") or ""),
                location=_location(item.get("locationName") or ""),
                description=html_to_text(item.get("jobDescription") or ""),
                source="reed",
            )
        )
    return jobs


def fetch_usajobs(search: dict, env: dict[str, str]) -> list[Job]:
    keywords = " ".join((search.get("keywords") or [])[:6])
    query = urllib.parse.urlencode({"Keyword": keywords, "ResultsPerPage": 20})
    payload = get_json(
        f"https://data.usajobs.gov/api/search?{query}",
        label="usajobs",
        headers={
            "Authorization-Key": env["USAJOBS_API_KEY"],
            "User-Agent": env["USAJOBS_EMAIL"],
        },
    )
    items = ((payload.get("SearchResult") or {}).get("SearchResultItems")) or []
    jobs: list[Job] = []
    for item in items:
        descriptor = item.get("MatchedObjectDescriptor") or {}
        user_area = descriptor.get("UserArea") if isinstance(descriptor.get("UserArea"), dict) else {}
        details = user_area.get("Details") if isinstance(user_area.get("Details"), dict) else {}
        jobs.append(
            Job(
                company=descriptor.get("OrganizationName") or "",
                title=descriptor.get("PositionTitle") or "",
                url=descriptor.get("PositionURI") or "",
                platform="usajobs",
                job_id=str(descriptor.get("PositionID") or ""),
                location=_location(descriptor.get("PositionLocationDisplay") or ""),
                description=html_to_text(details.get("JobSummary") or ""),
                source="usajobs",
            )
        )
    return jobs


FREEHIRE_API = "https://freehire.me/api/v1/jobs/search"
FREEHIRE_PAGE = 100
FREEHIRE_PAGES = 5
FREEHIRE_DAYS = 3
# freehire's query language takes at most five OR alternatives.
FREEHIRE_ALTERNATIVES = 5
_FREEHIRE_JOB_TYPES = {"full_time": "fulltime", "part_time": "parttime", "contract": "contract", "internship": "internship"}
COUNTRY_NAMES = {
    "AE": "United Arab Emirates", "AR": "Argentina", "AT": "Austria", "AU": "Australia", "BD": "Bangladesh",
    "BE": "Belgium", "BR": "Brazil", "CA": "Canada", "CH": "Switzerland", "CL": "Chile", "CN": "China",
    "CO": "Colombia", "CZ": "Czech Republic", "DE": "Germany", "DK": "Denmark", "EE": "Estonia", "EG": "Egypt",
    "ES": "Spain", "FI": "Finland", "FR": "France", "GB": "United Kingdom", "GR": "Greece", "HK": "Hong Kong",
    "HU": "Hungary", "ID": "Indonesia", "IE": "Ireland", "IL": "Israel", "IN": "India", "IT": "Italy",
    "JP": "Japan", "KE": "Kenya", "KR": "South Korea", "LK": "Sri Lanka", "LT": "Lithuania", "LV": "Latvia",
    "MX": "Mexico", "MY": "Malaysia", "NG": "Nigeria", "NL": "Netherlands", "NO": "Norway", "NP": "Nepal",
    "NZ": "New Zealand", "PE": "Peru", "PH": "Philippines", "PK": "Pakistan", "PL": "Poland", "PT": "Portugal",
    "QA": "Qatar", "RO": "Romania", "SA": "Saudi Arabia", "SE": "Sweden", "SG": "Singapore", "TH": "Thailand",
    "TR": "Turkey", "TW": "Taiwan", "UA": "Ukraine", "US": "United States", "VN": "Vietnam", "ZA": "South Africa",
}
_COUNTRY_CODE = {name.lower(): code for code, name in COUNTRY_NAMES.items()} | {
    "usa": "US", "u.s.": "US", "u.s.a.": "US", "united states of america": "US", "uk": "GB", "u.k.": "GB",
    "great britain": "GB", "england": "GB", "uae": "AE", "czechia": "CZ", "türkiye": "TR", "korea": "KR",
}


def freehire_queries(keywords: list[str]) -> list[str]:
    """One title search per role. hunt still checks every title against the roles itself."""
    from hunt.titles import search_phrases

    queries: list[str] = []
    for role in keywords:
        phrases = search_phrases(str(role))[:FREEHIRE_ALTERNATIVES]
        query = " OR ".join(f'title:"{phrase}"' for phrase in phrases)
        if query and query not in queries:
            queries.append(query)
    return queries


def freehire_places(search: dict) -> list[dict[str, str]]:
    """freehire ORs every place in a search, so home countries and remote-only countries are searched apart."""
    from hunt.preferences import WORK_MODES

    rule = search.get("location") or {}
    mode = rule.get("mode") or "any"
    if mode == "any":
        return [{}]
    if mode == "remote_only":
        return [{"work_mode": "remote"}]
    home = _freehire_codes(rule.get("countries"), search)
    if mode == "countries":
        return [{"countries": home}] if home else []
    modes = [str(name) for name in rule.get("work_modes") or WORK_MODES]
    cities = [str(city).strip() for city in rule.get("cities") or [] if str(city).strip()]
    places: list[dict[str, str]] = []
    place = {"countries": home} if home else {"cities": ",".join(cities)} if cities else None
    if place is not None:
        # A work-mode filter drops postings with no stated mode, so send one only when it narrows.
        if set(modes) != set(WORK_MODES):
            place["work_mode"] = ",".join(modes)
        places.append(place)
    remote = _freehire_codes(rule.get("remote_only_countries"), search)
    if remote and "remote" in modes:
        places.append({"countries": remote, "work_mode": "remote"})
    return places


def _freehire_codes(names, search: dict) -> str:
    options = (search.get("source_options") or {}).get("freehire") or {}
    overrides = {str(name).strip().lower(): str(code).strip().upper() for name, code in (options.get("country_codes") or {}).items()}
    codes: list[str] = []
    unknown: list[str] = []
    for name in names or []:
        key = str(name).strip().lower()
        if not key:
            continue
        code = overrides.get(key) or _COUNTRY_CODE.get(key) or (key.upper() if key.upper() in COUNTRY_NAMES else "")
        if code:
            codes.append(code)
        else:
            unknown.append(str(name).strip())
    if unknown:
        raise SourceError(f"no country code for {', '.join(unknown)}: add it to source_options.freehire.country_codes in profile/search.json")
    return ",".join(dict.fromkeys(codes))


def fetch_freehire(search: dict, _env: dict[str, str]) -> list[Job]:
    """Postings freehire.me crawled from company hiring boards, first seen in the last few days. No key."""
    options = (search.get("source_options") or {}).get("freehire") or {}
    days = int(options.get("open_within_days") or FREEHIRE_DAYS)
    queries = freehire_queries(search.get("keywords") or [])
    if not queries:
        raise SourceError("freehire needs keywords in profile/search.json")
    found: dict[str, Job] = {}
    for place in freehire_places(search):
        for query in queries:
            for page in range(FREEHIRE_PAGES):
                params = {
                    "q": query,
                    **place,
                    "open_within_days": days,
                    "reality_exclude": "likely-evergreen",
                    "sort": "created_at",
                    "limit": FREEHIRE_PAGE,
                    "offset": page * FREEHIRE_PAGE,
                }
                payload = get_json(f"{FREEHIRE_API}?{urllib.parse.urlencode(params)}", label="freehire")
                rows = (payload.get("data") if isinstance(payload, dict) else None) or []
                for item in rows:
                    job = _freehire_job(item)
                    if job.url:
                        found.setdefault(job.url, job)
                if len(rows) < FREEHIRE_PAGE:
                    break
    return list(found.values())


def _freehire_job(item: dict) -> Job:
    """The posting keeps its hiring board's platform, board, and id, so it dedupes with the same posting fetched directly."""
    enrichment = item.get("enrichment") or {}
    platform = str(item.get("source") or "").lower() or "freehire"
    board, _, ident = str(item.get("external_id") or "").partition(":")
    if platform == "workday":
        board, ident = board.split(".", 1)[0], ident.rsplit("_", 1)[-1]
    url = _untracked(item.get("url") or "")
    place = item.get("location") or ""
    countries = [COUNTRY_NAMES.get(str(code).upper(), str(code).upper()) for code in item.get("countries") or []]
    size = str(enrichment.get("company_size") or "")
    dates = [stamp for stamp in (item.get("posted_at"), item.get("created_at")) if stamp]
    return _mark(
        Job(
            company=item.get("company") or "",
            title=item.get("title") or "",
            url=url,
            platform=platform,
            job_id=ident or board,
            location=_location(place, *[name for name in countries if name.lower() not in place.lower()], remote=item.get("work_mode") == "remote"),
            description=_freehire_text(item.get("description") or "", enrichment),
            source="freehire",
            board=board if ident else "",
            pay_min=_amount(enrichment.get("salary_min")),
            pay_max=_amount(enrichment.get("salary_max")),
            pay_currency=str(enrichment.get("salary_currency") or ""),
            pay_interval=str(enrichment.get("salary_period") or ""),
            job_type=_FREEHIRE_JOB_TYPES.get(str(enrichment.get("employment_type") or ""), ""),
            work_mode=str(item.get("work_mode") or ""),
        ),
        apply=url,
        # A board that restates its date on every crawl looks newer than the day freehire first saw the posting.
        posted=min(dates) if dates else "",
        big=size == "1000+",
        size_known=bool(size),
    )


def _freehire_text(description: str, enrichment: dict) -> str:
    """Search results cut the posting short, so the years and requirements freehire read from it are added."""
    text = html_to_text(description)
    years = enrichment.get("experience_years_min")
    if isinstance(years, (int, float)) and not isinstance(years, bool):
        text = f"Experience: {int(years)}+ years. {text}"
    needs = [str(need.get("text") or "").strip() for need in enrichment.get("requirements") or [] if isinstance(need, dict)]
    needs = [need for need in needs if need]
    if needs:
        text = f"{text} Requirements: {'; '.join(needs)}."
    return text.strip()


def _untracked(url: str) -> str:
    parts = urllib.parse.urlsplit(url)
    query = [(key, value) for key, value in urllib.parse.parse_qsl(parts.query, keep_blank_values=True) if not key.lower().startswith("utm_")]
    return urllib.parse.urlunsplit(parts._replace(query=urllib.parse.urlencode(query)))


def fetch_jobspy(search: dict, env: dict[str, str], on_progress=None) -> list[Job]:
    """Job boards through python-jobspy. One request per keyword and city."""
    options = (search.get("source_options") or {}).get("jobspy") or {}
    sites = jobspy_sites(search)
    if len(sites) == 1:
        options = {**options, **((options.get("site_options") or {}).get(sites[0]) or {})}
    term = str(options.get("search_term") or "").strip()
    keywords = [term] if term else [str(word).strip() for word in (search.get("keywords") or []) if str(word).strip()]
    keywords = keywords[:4]
    if not keywords:
        raise SourceError("jobspy needs keywords in profile/search.json or source_options.jobspy.search_term")
    rule = search.get("location") or {}
    mode = rule.get("mode") or "any"
    remote_only = mode == "remote_only" or (mode == "places" and set(rule.get("work_modes") or []) == {"remote"})
    places = _jobspy_places(options)
    country = str(options.get("country_indeed") or "").strip()
    wanted = int(options.get("results_wanted") or 15)
    hours = int(options.get("hours_old") or 48)
    describe = options.get("fetch_description", True) is not False
    proxies = [part.strip() for part in (env.get("JOBSPY_PROXIES") or "").split(",") if part.strip()]
    ca_cert = _ca_bundle()
    report = on_progress or (lambda _line: None)
    found: list[Job] = []
    seen: set[str] = set()
    errors: list[str] = []
    plans = [(f"@ {place}" if place else "", place, country, remote_only, list(sites)) for place in places]
    for name in _jobspy_list(options.get("remote_countries")):
        # Naukri lists India only.
        remote_sites = [site for site in sites if site != "naukri" or name.lower() == "india"]
        if remote_sites:
            plans.append((f"@ {name} remote", name, name, True, remote_sites))
    report(f"jobspy: {len(keywords) * len(plans)} searches on {', '.join(sites)}")
    for keyword in keywords:
        for where, place, place_country, remote, plan_sites in plans:
            label = f"{keyword} {where}".strip()
            kwargs: dict = {
                "site_name": plan_sites,
                "search_term": keyword,
                "results_wanted": wanted,
                "hours_old": hours,
                "description_format": "plain",
                "fetch_description": describe,
                "verbose": 0,
            }
            if remote:
                kwargs["is_remote"] = True
            if place:
                kwargs["location"] = place
            if place_country:
                kwargs["country_indeed"] = place_country
            if proxies:
                kwargs["proxies"] = proxies
            if ca_cert:
                kwargs["ca_cert"] = ca_cert
            try:
                frame, logged = _scrape_jobspy(**kwargs)
            except Exception as exc:
                errors.append(f"{label}: {exc}")
                report(f"error jobspy {label}: {exc}")
                continue
            for message in logged:
                errors.append(f"{label}: {message}")
                report(f"error jobspy {label}: {message}")
            rows = list(frame.to_dict("records")) if hasattr(frame, "to_dict") else []
            report(f"jobspy {label}: {len(rows)} listings")
            for item in rows:
                url = _cell(item.get("job_url"))
                if not url or url in seen:
                    continue
                seen.add(url)
                posted = item.get("date_posted")
                if hasattr(posted, "isoformat"):
                    posted = posted.isoformat()
                employees = _cell(item.get("company_num_employees"))
                found.append(
                    _mark(
                        Job(
                            company=_cell(item.get("company")),
                            title=_cell(item.get("title")),
                            url=url,
                            platform=_cell(item.get("site")) or "jobspy",
                            job_id=_cell(item.get("id")) or url,
                            location=_location(_cell(item.get("location")), remote=_flag(item.get("is_remote"))),
                            description=_jobspy_text(item),
                            source="jobspy",
                            pay_min=_amount(item.get("min_amount")),
                            pay_max=_amount(item.get("max_amount")),
                            pay_currency=_cell(item.get("currency")),
                            pay_interval=_cell(item.get("interval")),
                            job_type=_cell(item.get("job_type")),
                            work_mode=_cell(item.get("work_from_home_type")),
                        ),
                        apply=_cell(item.get("job_url_direct")) or url,
                        posted=posted,
                        big=_big_employer(employees),
                        size_known=bool(re.search(r"\d", employees)),
                    )
                )
    if not found and errors:
        raise SourceError("jobspy " + "; ".join(errors)[:300])
    return found


def _ca_bundle() -> str:
    """curl_cffi (Naukri, Glassdoor) otherwise trusts the macOS bundle, which lacks newer roots such as GlobalSign R46."""
    try:
        import certifi
    except ImportError:
        return ""
    return certifi.where()


def jobspy_sites(search: dict) -> list[str]:
    options = (search.get("source_options") or {}).get("jobspy") or {}
    sites = options.get("sites") or ["indeed", "linkedin"]
    if isinstance(sites, str):
        sites = sites.split(",")
    return [str(site).strip().lower() for site in sites if str(site).strip()]


def _jobspy_list(raw) -> list[str]:
    if isinstance(raw, str):
        return [part.strip() for part in raw.split(",") if part.strip()]
    if isinstance(raw, list):
        return [str(part).strip() for part in raw if str(part).strip()]
    return []


def _jobspy_places(options: dict) -> list[str]:
    places = _jobspy_list(options.get("locations"))
    single = str(options.get("location") or "").strip()
    if single and single not in places:
        places.append(single)
    return places or [""]


def _scrape_jobspy(**kwargs) -> tuple[object, list[str]]:
    try:
        from jobspy import scrape_jobs
    except ImportError as exc:
        raise SourceError("JobSpy is not installed. From this directory, run: uv sync") from exc
    with _jobspy_log(kwargs.get("site_name") or []) as logged:
        frame = scrape_jobs(**kwargs)
    return frame, logged


_SINKS: dict[str, list[list[str]]] = {}
_SINK_LOCK = threading.Lock()


class _Route(logging.Handler):
    """JobSpy logs from its own threads. Each site has its own logger, so route by name."""

    def __init__(self) -> None:
        super().__init__(logging.ERROR)

    def emit(self, record: logging.LogRecord) -> None:
        site = record.name.split(":", 1)[-1].lower()
        with _SINK_LOCK:
            sinks = list(_SINKS.get(site, []))
        for sink in sinks:
            sink.append(record.getMessage())


_ROUTE = _Route()


@contextmanager
def _jobspy_log(sites: list[str]):
    """Collect each site's errors for the search that asked. Nothing JobSpy logs reaches the terminal."""
    for name in list(logging.root.manager.loggerDict):
        if name.startswith("JobSpy:"):
            logging.getLogger(name).handlers = [_ROUTE]
    keys = [str(site).replace("_", "").lower() for site in sites]
    messages: list[str] = []
    with _SINK_LOCK:
        for key in keys:
            _SINKS.setdefault(key, []).append(messages)
    try:
        yield messages
    finally:
        with _SINK_LOCK:
            for key in keys:
                _SINKS[key] = [sink for sink in _SINKS.get(key, []) if sink is not messages]


def _jobspy_text(item: dict) -> str:
    text = html_to_text(_cell(item.get("description")))
    experience = _cell(item.get("experience_range"))
    if not experience:
        return text
    experience = re.sub(r"(?i)\byrs?\b\.?", "years", experience)
    return f"Experience: {experience}. {text}".strip()


def _cell(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        try:
            if value != value:
                return ""
        except (TypeError, ValueError):
            return ""
    text = str(value).strip()
    return "" if text.lower() == "nan" else text


def _flag(value) -> bool:
    return value is not None and value == value and bool(value)


def _amount(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if number != number or number <= 0 else number


def _big_employer(value) -> bool:
    numbers = [int(part) for part in re.findall(r"\d+", _cell(value).replace(",", ""))]
    return bool(numbers) and max(numbers) >= 1000


SPECS: dict[str, SourceSpec] = {
    "greenhouse": SourceSpec("greenhouse", kind="ats"),
    "lever": SourceSpec("lever", kind="ats"),
    "ashby": SourceSpec("ashby", kind="ats"),
    "workday": SourceSpec("workday", kind="ats"),
    "arbeitnow": SourceSpec("arbeitnow"),
    "jobicy": SourceSpec("jobicy"),
    "muse": SourceSpec("muse"),
    "himalayas": SourceSpec("himalayas"),
    "adzuna": SourceSpec("adzuna", env=("ADZUNA_APP_ID", "ADZUNA_APP_KEY")),
    "jooble": SourceSpec("jooble", env=("JOOBLE_API_KEY",)),
    "findwork": SourceSpec("findwork", env=("FINDWORK_API_KEY",)),
    "reed": SourceSpec("reed", env=("REED_API_KEY",)),
    "usajobs": SourceSpec("usajobs", env=("USAJOBS_API_KEY", "USAJOBS_EMAIL")),
    "freehire": SourceSpec("freehire"),
    "jobspy": SourceSpec("jobspy"),
}

ATS_FETCH = {
    "greenhouse": fetch_greenhouse,
    "lever": fetch_lever,
    "ashby": fetch_ashby,
    "workday": fetch_workday,
}

FEED_FETCH = {
    "arbeitnow": fetch_arbeitnow,
    "jobicy": fetch_jobicy,
    "muse": fetch_muse,
    "himalayas": fetch_himalayas,
    "adzuna": fetch_adzuna,
    "jooble": fetch_jooble,
    "findwork": fetch_findwork,
    "reed": fetch_reed,
    "usajobs": fetch_usajobs,
    "freehire": fetch_freehire,
    "jobspy": fetch_jobspy,
}


def source_status(enabled: list[str], env: dict[str, str]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    enabled_set = set(enabled)
    for spec in SPECS.values():
        if spec.name not in enabled_set:
            rows.append({"name": spec.name, "state": "off", "detail": "not listed in profile/search.json sources"})
            continue
        missing = [key for key in spec.env if not (env.get(key) or "").strip()]
        if missing:
            rows.append({"name": spec.name, "state": "skip", "detail": "set " + ", ".join(missing)})
            continue
        detail = "watchlist" if spec.kind == "ats" else "feed"
        rows.append({"name": spec.name, "state": "on", "detail": detail})
    return rows


def format_status(rows: list[dict[str, str]]) -> str:
    lines = []
    for row in rows:
        detail = f": {row['detail']}" if row["state"] != "on" else ""
        if row["state"] == "on":
            detail = f" ({row['detail']})"
        lines.append(f"{row['state']:4} {row['name']}{detail}")
    return "\n".join(lines)
