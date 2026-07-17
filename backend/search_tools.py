"""In-process SDK tools that let the orchestrator fetch job listings via **JobSpy**
(structured scraping), and the **Exa** and **Tavily** search APIs, instead of relying
solely on Claude's built-in `WebSearch` (which returned too few results).

`jobspy_search` is the primary bulk tool: one call scrapes Indeed, LinkedIn, Glassdoor,
ZipRecruiter, and Google Jobs via the open-source `python-jobspy` library and returns
fully structured, pre-verified records (remote/full-time/date/US already checked here in
Python) — the agent never has to read those pages, which is the main token saving.

`exa_search` / `tavily_search` cover the remaining sources: the orchestrator calls them
with a query and its assigned `source` (Workday, Greenhouse, Lever, Ashby, Dice,
Wellfound, Built In, or "Company" for open-web company career pages). The tool scopes
the search to that source's domain(s) — or, for "Company", searches the open web while
excluding the known job-board domains — and returns a JSON list of candidate postings.

A per-run context (set by the backend before each agent run) carries the user's already
stored job URLs and an incremental-search cutoff: every tool silently drops results the
user already has (`skipped_known` in the payload reports how many), so successive runs
never spend tokens re-verifying known jobs.

Recency/remote/full-time verification is done HERE, in Python, not left to the agent's
`WebFetch` — Workday's careers portal is a JS-rendered SPA that `WebFetch` cannot render
(it returns an empty/unusable page), so relying on the agent to WebFetch-verify Workday
postings silently drops nearly all of them. Tavily's own crawler DOES render these pages
(and LinkedIn's public job-preview pages) and its `raw_content` embeds structured labels —
Workday: "remote type ...", "time type ...", "posted on Posted N Days Ago"; LinkedIn: a
relative-time string like "5 days ago" right after the company/location line. We parse
these deterministically into `remote`, `full_time`, `posted_days_ago`, and
`posted_within_7d` fields on every result, for both tools:

- `tavily_search` gets `raw_content` directly from its own search call.
- `exa_search`'s own extracted `text` is unusable for Workday (garbled binary-looking
  output) and login-walled for much of LinkedIn, so its results are backfilled by calling
  Tavily's `/extract` endpoint on the returned URLs (only when `TAVILY_API_KEY` is set;
  otherwise those fields are left `null` and the agent should verify manually).

API keys are read from the environment — `EXA_API_KEY` and `TAVILY_API_KEY` — and are
never hardcoded. If a key is missing the tool returns a clear message so the agent can
fall back to the built-in `WebSearch`/`WebFetch` tools.
"""

import os
import re
import json
import asyncio
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse

import aiohttp

from claude_agent_sdk import tool, create_sdk_mcp_server

EXA_API_URL = "https://api.exa.ai/search"
TAVILY_SEARCH_API_URL = "https://api.tavily.com/search"
TAVILY_EXTRACT_API_URL = "https://api.tavily.com/extract"

# How many results to request per call. Exa supports up to 100; 30 balances recall
# against orchestrator context (jobspy now carries the bulk of discovery, so exa/tavily
# only need to cover the long-tail sources). Tavily's documented max is 20.
EXA_NUM_RESULTS = 30
TAVILY_NUM_RESULTS = 20
JOBSPY_NUM_RESULTS = 50
# Snippet caps (token budget): exa/tavily raw search snippets vs jobspy's clean
# markdown descriptions.
SNIPPET_MAX_CHARS = 300
JOBSPY_SNIPPET_MAX_CHARS = 400
DEFAULT_RECENCY_DAYS = 7
HTTP_TIMEOUT = 30
# Tavily's /extract endpoint is called in batches to backfill Exa results with verified
# content; cap batch size defensively even though Tavily accepts more per call.
EXTRACT_BATCH_SIZE = 20


# Every known job-source domain: LinkedIn, the big aggregator boards, the ATS-hosted
# company careers portals, and the tech-focused boards. Also used as the EXCLUDE list
# for "Company" career-page searches so those don't re-return board results.
ALL_SOURCE_DOMAINS = [
    "linkedin.com",
    "indeed.com",
    "glassdoor.com",
    "ziprecruiter.com",
    "myworkdayjobs.com",
    "boards.greenhouse.io",
    "job-boards.greenhouse.io",
    "jobs.lever.co",
    "jobs.ashbyhq.com",
    "dice.com",
    "wellfound.com",
    "builtin.com",
]


def _domains_for(source: str) -> list[str]:
    """Map an assigned source label to the domain(s) to scope the search to.

    Returns [] ONLY for "Company" (career pages): the caller searches the open web and
    excludes ALL_SOURCE_DOMAINS instead of scoping to an include-list.
    """
    s = (source or "").strip().lower()
    if "workday" in s:
        return ["myworkdayjobs.com"]
    if "linkedin" in s:
        return ["linkedin.com"]
    if "indeed" in s:
        return ["indeed.com"]
    if "glassdoor" in s:
        return ["glassdoor.com"]
    if "zip" in s:
        return ["ziprecruiter.com"]
    if "greenhouse" in s:
        return ["boards.greenhouse.io", "job-boards.greenhouse.io"]
    if "lever" in s:
        return ["jobs.lever.co"]
    if "ashby" in s:
        return ["jobs.ashbyhq.com"]
    if "dice" in s:
        return ["dice.com"]
    if "wellfound" in s or "angellist" in s or "angel.co" in s:
        return ["wellfound.com"]
    if "built" in s:
        return ["builtin.com"]
    if "company" in s or "career" in s:
        return []
    # Unknown / unspecified → search all known sources.
    return list(ALL_SOURCE_DOMAINS)


# --- Per-run context (cross-run dedup + incremental search) --------------------------
#
# Set by the backend before each agent run (safe as module state: /api/pull enforces a
# single run at a time). `known_urls` holds every job URL already stored for the user —
# the tools drop those results before the agent ever sees them, so no tokens are spent
# re-verifying jobs from earlier runs. The backend also adds each batch-saved URL during
# the run, so a job found via one source isn't re-surfaced by a later tool call.

_RUN_CONTEXT: dict = {"known_urls": set()}


def set_run_context(known_urls: set[str] | None = None):
    """Install the per-run dedup context. Call before an agent run starts."""
    _RUN_CONTEXT["known_urls"] = set(known_urls or set())


def clear_run_context():
    """Reset the per-run context. Call when the run finishes (success or failure)."""
    _RUN_CONTEXT["known_urls"] = set()


def add_known_urls(urls):
    """Add freshly saved job URLs to the run context (called from the batch save)."""
    _RUN_CONTEXT["known_urls"].update(u for u in urls if u)


def _normalize_url(url: str) -> str:
    return (url or "").strip().rstrip("/").lower()


# Obvious search/listing-index pages, not individual postings. Conservative on purpose:
# real posting URLs (Workday /job/, Greenhouse /jobs/<id>, Lever/Ashby /<uuid>) must pass.
_BAD_URL_PATH_RE = re.compile(
    r"/(search|jobs/search|job-search|browse|category|categories|explore)([/?#]|$)"
    r"|[?&]q=|[?&]keywords=",
    re.IGNORECASE,
)


def _is_valid_job_url(url) -> bool:
    """Cheap static check that a URL can plausibly open a specific job posting:
    http(s) with a host, not a bare domain root, not an obvious search/index page."""
    u = str(url or "").strip()
    if not u.lower().startswith(("http://", "https://")):
        return False
    parsed = urlparse(u)
    if not parsed.netloc:
        return False
    if parsed.path in ("", "/") and not parsed.query:
        return False
    return not _BAD_URL_PATH_RE.search(u)


def _filter_known(results: list[dict]) -> tuple[list[dict], int]:
    """Drop results whose URL is already stored for this user. Returns (kept, skipped)."""
    known = _RUN_CONTEXT.get("known_urls") or set()
    if not known:
        return results, 0
    known_norm = {_normalize_url(u) for u in known}
    kept = [r for r in results if _normalize_url(r.get("url")) not in known_norm]
    return kept, len(results) - len(kept)


def _since_iso(days: int = DEFAULT_RECENCY_DAYS) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).strftime(
        "%Y-%m-%dT%H:%M:%S.000Z"
    )


def _ok(payload) -> dict:
    return {"content": [{"type": "text", "text": json.dumps(payload)}]}


def _msg(text: str) -> dict:
    return {"content": [{"type": "text", "text": text}]}


# --- Relative-date / listing-field parsing -----------------------------------------

_RELATIVE_RE = re.compile(
    r"\b(\d+)\s*\+?\s*(hour|hours|day|days|week|weeks|month|months|year|years)\s*ago\b",
    re.IGNORECASE,
)
_TODAY_RE = re.compile(r"\b(just now|today)\b", re.IGNORECASE)
_YESTERDAY_RE = re.compile(r"\byesterday\b", re.IGNORECASE)

_UNIT_TO_DAYS = {
    "hour": 0, "hours": 0,
    "day": 1, "days": 1,
    "week": 7, "weeks": 7,
    "month": 30, "months": 30,
    "year": 365, "years": 365,
}


def _parse_relative_days(text: str) -> float | None:
    """Best-effort parse of a relative-time string (e.g. '5 days ago', 'Posted Today',
    '29 Days Ago', '1 month ago') into an approximate number of days ago. Returns None if
    no relative-time phrase is found."""
    if not text:
        return None
    if _TODAY_RE.search(text):
        return 0
    if _YESTERDAY_RE.search(text):
        return 1
    m = _RELATIVE_RE.search(text)
    if not m:
        return None
    n = int(m.group(1))
    unit = m.group(2).lower()
    per_unit_days = _UNIT_TO_DAYS.get(unit, 1)
    return n * per_unit_days if per_unit_days else 0


_WORKDAY_REMOTE_RE = re.compile(r"remote type\s*([^\n]+)", re.IGNORECASE)
_WORKDAY_TIME_RE = re.compile(r"time type\s*([^\n]+)", re.IGNORECASE)
_WORKDAY_POSTED_RE = re.compile(r"posted on\s*Posted\s*([^\n]+)", re.IGNORECASE)
_LINKEDIN_POSTED_RE = re.compile(
    r"\b(?:just now|today|yesterday|\d+\+?\s*(?:hour|hours|day|days|week|weeks|month|months|year|years)\s*ago)\b",
    re.IGNORECASE,
)


def _classify_remote(value: str | None) -> bool | None:
    if not value:
        return None
    v = value.lower()
    if "hybrid" in v and "remote" not in v:
        return False
    if "remote" in v:
        return True
    if "onsite" in v or "on-site" in v or "office" in v:
        return False
    return None


def _classify_full_time(value: str | None) -> bool | None:
    if not value:
        return None
    v = value.lower()
    if "full" in v:
        return True
    if "part" in v or "contract" in v or "intern" in v or "temp" in v:
        return False
    return None


# US-eligibility cues. Negative: the posting restricts the role to a non-US country or
# region. Positive: the posting explicitly scopes to the US. Anything else → None
# (unknown, keep-by-default — the scout judges borderline cases).
_US_NEGATIVE_RE = re.compile(
    r"\b(?:remote\s*[-–—:(]?\s*(?:uk|united kingdom|eu|europe|emea|apac|canada|india|australia|germany|france|latam)"
    r"|(?:uk|united kingdom|eu|europe|emea|apac|canada|india|australia|germany|france|latam)\s+only"
    r"|only\s+(?:in|within|from)\s+(?:the\s+)?(?:uk|united kingdom|eu|europe|emea|apac|canada|india|australia|germany|france|latam)"
    r"|must\s+be\s+(?:located|based|resident)\s+in\s+(?:the\s+)?(?:uk|united kingdom|eu|europe|emea|apac|canada|india|australia|germany|france|latam)"
    r"|within\s+(?:european?|uk|emea|apac)\s+time\s*zones?)\b",
    re.IGNORECASE,
)
_US_POSITIVE_RE = re.compile(
    r"\b(?:remote\s*[-–—:(]?\s*(?:usa?|u\.s\.|united states)"
    r"|(?:usa?|u\.s\.|united states)\s*[-–—:(]?\s*remote"
    r"|us[- ]based|based\s+in\s+the\s+(?:us|united states)"
    r"|anywhere\s+in\s+the\s+(?:us|united states)"
    r"|united states|work\s+authorization\s+in\s+the\s+us)\b",
    re.IGNORECASE,
)


def _classify_us_eligible(text: str) -> bool | None:
    """Best-effort US-eligibility signal from listing text. None = unknown."""
    if not text:
        return None
    if _US_NEGATIVE_RE.search(text):
        return False
    if _US_POSITIVE_RE.search(text):
        return True
    return None


def _parse_listing_signals(raw_content: str, url: str) -> dict:
    """Extract {remote, full_time, posted_days_ago, posted_text} from a rendered job page's
    text. Handles Workday's labeled fields and LinkedIn's relative-time string; falls back
    to a generic scan for other pages."""
    if not raw_content:
        return {"remote": None, "full_time": None, "posted_days_ago": None, "posted_text": None}

    is_workday = "myworkdayjobs.com" in (url or "")
    if is_workday:
        remote_m = _WORKDAY_REMOTE_RE.search(raw_content)
        time_m = _WORKDAY_TIME_RE.search(raw_content)
        posted_m = _WORKDAY_POSTED_RE.search(raw_content)
        posted_text = posted_m.group(1).strip() if posted_m else None
        return {
            "remote": _classify_remote(remote_m.group(1).strip() if remote_m else None),
            "full_time": _classify_full_time(time_m.group(1).strip() if time_m else None),
            "posted_days_ago": _parse_relative_days(posted_text) if posted_text else None,
            "posted_text": posted_text,
        }

    # LinkedIn (and generic fallback): look for a relative-time phrase near the top of the
    # page, and scan the whole text for employment-type / workplace-type cues.
    posted_m = _LINKEDIN_POSTED_RE.search(raw_content[:2000])
    posted_text = posted_m.group(0).strip() if posted_m else None
    lower = raw_content.lower()
    remote = None
    if "workplace type" in lower or "remote" in lower[:3000]:
        if re.search(r"workplace type\s*:?\s*remote", lower):
            remote = True
        elif re.search(r"workplace type\s*:?\s*(on-site|onsite)", lower):
            remote = False
        elif re.search(r"workplace type\s*:?\s*hybrid", lower):
            remote = False
        elif "remote" in lower[:1500]:
            remote = True
    full_time = None
    if re.search(r"employment type\s*:?\s*full[\s-]?time", lower):
        full_time = True
    elif re.search(r"employment type\s*:?\s*(part[\s-]?time|contract|internship|temporary)", lower):
        full_time = False
    return {
        "remote": remote,
        "full_time": full_time,
        "posted_days_ago": _parse_relative_days(posted_text) if posted_text else None,
        "posted_text": posted_text,
    }


def _enrich_result(result: dict, recency_days: int = DEFAULT_RECENCY_DAYS, trust_search_recency: bool = False) -> dict:
    """Annotate a result with remote/full_time/posted_within_recency_days, parsed from its rendered
    page text where possible.

    `trust_search_recency`: when True (Tavily-sourced results only), Tavily's own search parameter
    already time-filtered this result server-side before we ever see it. So if our page-text parse
    can't find an explicit date (the crawler is blocked on some Workday tenants, or the phrasing
    doesn't match), default `posted_within_recency_days` to True rather than unknown.
    """
    raw = result.get("raw_content") or ""
    signals = _parse_listing_signals(raw, result.get("url") or "")
    days = signals["posted_days_ago"]
    result["remote"] = signals["remote"]
    result["full_time"] = signals["full_time"]
    result["posted_days_ago"] = days
    result["posted_text"] = signals["posted_text"]
    # US-eligibility: scan the rendered page text plus title/snippet. None = unknown.
    result["us_eligible"] = _classify_us_eligible(
        " ".join(p for p in (result.get("title"), result.get("snippet"), raw[:4000]) if p)
    )
    if days is not None:
        result["posted_within_7d"] = days <= recency_days
        result["date_confidence"] = "parsed"
    elif trust_search_recency:
        result["posted_within_7d"] = True
        result["date_confidence"] = "search_filtered"
    else:
        result["posted_within_7d"] = None
        result["date_confidence"] = "unknown"
    result.pop("raw_content", None)
    return result


async def _tavily_extract(session: aiohttp.ClientSession, api_key: str, urls: list[str]) -> dict[str, str]:
    """Call Tavily's /extract on a batch of URLs; returns {url: raw_content} for whatever
    succeeded. Failures for individual URLs are silently skipped (best-effort backfill)."""
    raw_by_url: dict[str, str] = {}
    for i in range(0, len(urls), EXTRACT_BATCH_SIZE):
        batch = urls[i : i + EXTRACT_BATCH_SIZE]
        if not batch:
            continue
        try:
            async with session.post(
                TAVILY_EXTRACT_API_URL,
                json={"api_key": api_key, "urls": batch},
                timeout=HTTP_TIMEOUT,
            ) as resp:
                if resp.status != 200:
                    continue
                data = await resp.json()
        except Exception:
            continue
        for r in data.get("results", []) or []:
            u = r.get("url")
            if u:
                raw_by_url[u] = r.get("raw_content") or ""
    return raw_by_url


@tool(
    "exa_search",
    "Search the Exa API for recent job postings on the assigned source (Workday/Greenhouse/"
    "Lever/Ashby careers portals, Dice, Wellfound, Built In, or source='Company' to search "
    "employer career pages on the open web; LinkedIn/Indeed/Glassdoor/ZipRecruiter are "
    "normally covered by jobspy_search — only use them here as a jobspy fallback). Returns "
    "a JSON array of candidate postings, each pre-annotated with `remote` (bool|null), "
    "`full_time` (bool|null), `posted_days_ago` (number|null), `posted_within_7d` "
    "(bool|null), and `us_eligible` (bool|null) — computed by rendering the page "
    "server-side, since WebFetch cannot render Workday's JS pages. Jobs the user already "
    "has are filtered out automatically (`skipped_known`). KEEP a candidate only if "
    "posted_within_7d is true, remote is true (or null with the title/snippet clearly "
    "indicating remote), full_time is not explicitly false, and us_eligible is not false. "
    "If a field is null, the page couldn't be verified — use judgment from the title/"
    "snippet, or drop it if unsure.",
    {"query": str, "source": str, "time_period_days": int},
)
async def exa_search(args: dict) -> dict:
    # Throttle to prevent Claude API rate limits (reduced from 4s to 1.5s)
    await asyncio.sleep(1.5)
    api_key = os.environ.get("EXA_API_KEY")
    if not api_key:
        return _msg(
            "EXA_API_KEY is not set in the environment — Exa search is unavailable. "
            "Fall back to the built-in WebSearch/WebFetch tools."
        )
    query = (args.get("query") or "").strip()
    if not query:
        return _msg("exa_search requires a non-empty 'query'.")
    domains = _domains_for(args.get("source"))
    time_period_days = args.get("time_period_days", DEFAULT_RECENCY_DAYS)

    payload = {
        "query": query,
        "type": "auto",
        "numResults": EXA_NUM_RESULTS,
        "contents": {"text": {"maxCharacters": SNIPPET_MAX_CHARS}},
    }
    if domains:
        payload["includeDomains"] = domains
    else:
        # source='Company': search employer career pages on the open web, excluding
        # the known job-board domains so we don't re-return board results.
        payload["excludeDomains"] = list(ALL_SOURCE_DOMAINS)
    # Exa's published-date filter works for LinkedIn but zeroes out the ATS portals
    # (Workday/Greenhouse/Lever/Ashby listing pages lack a publish date Exa recognizes).
    if domains == ["linkedin.com"]:
        payload["startPublishedDate"] = _since_iso(time_period_days)
    headers = {"x-api-key": api_key, "Content-Type": "application/json"}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                EXA_API_URL, json=payload, headers=headers, timeout=HTTP_TIMEOUT
            ) as resp:
                if resp.status == 429:
                    return _msg(
                        "Exa API rate limit exceeded. Fall back to WebSearch/WebFetch for this query."
                    )
                if resp.status != 200:
                    body = await resp.text()
                    return _msg(f"Exa search failed (HTTP {resp.status}): {body[:300]}")
                data = await resp.json()

            results = []
            for r in data.get("results", []) or []:
                if not _is_valid_job_url(r.get("url")):
                    continue
                results.append(
                    {
                        "title": r.get("title"),
                        "url": r.get("url"),
                        "snippet": (r.get("text") or "")[:SNIPPET_MAX_CHARS],
                    }
                )
            # Cross-run dedup BEFORE the Tavily extract backfill — no point paying to
            # re-verify jobs the user already has stored.
            results, skipped_known = _filter_known(results)

            # Backfill verified remote/full_time/posted_within_recency via Tavily's extractor
            # (Exa's own extracted text is unusable for Workday and login-walled on LinkedIn).
            tavily_key = os.environ.get("TAVILY_API_KEY")
            if tavily_key and results:
                raw_by_url = await _tavily_extract(
                    session, tavily_key, [r["url"] for r in results if r.get("url")]
                )
                for r in results:
                    r["raw_content"] = raw_by_url.get(r.get("url"))
    except Exception as e:
        return _msg(f"Exa search error: {e}")

    results = [_enrich_result(r, recency_days=time_period_days) for r in results]
    return _ok(
        {
            "provider": "exa",
            "domains": domains,
            "count": len(results),
            "skipped_known": skipped_known,
            "results": results,
        }
    )


@tool(
    "tavily_search",
    "Search the Tavily API for recent job postings on the assigned source (Workday/"
    "Greenhouse/Lever/Ashby careers portals, Dice, Wellfound, Built In, or source='Company' "
    "to search employer career pages on the open web; LinkedIn/Indeed/Glassdoor/ZipRecruiter "
    "are normally covered by jobspy_search — only use them here as a jobspy fallback). "
    "Returns a JSON array of candidate postings, each pre-annotated with `remote` "
    "(bool|null), `full_time` (bool|null), `posted_days_ago` (number|null), "
    "`posted_within_7d` (bool|null), and `us_eligible` (bool|null) — parsed from the "
    "rendered page (Workday's 'remote type'/'time type'/'posted on' labels, or a relative "
    "post-date). Jobs the user already has are filtered out automatically "
    "(`skipped_known`). KEEP a candidate only if posted_within_7d is true, remote is true "
    "(or null with the title/snippet clearly indicating remote), full_time is not "
    "explicitly false, and us_eligible is not false. If a field is null, the page couldn't "
    "be verified — use judgment from the title/snippet, or drop it if unsure.",
    {"query": str, "source": str, "time_period_days": int},
)
async def tavily_search(args: dict) -> dict:
    # Throttle to prevent Claude API rate limits (reduced from 4s to 1.5s)
    await asyncio.sleep(1.5)
    api_key = os.environ.get("TAVILY_API_KEY")
    if not api_key:
        return _msg(
            "TAVILY_API_KEY is not set in the environment — Tavily search is unavailable. "
            "Fall back to the built-in WebSearch/WebFetch tools."
        )
    query = (args.get("query") or "").strip()
    if not query:
        return _msg("tavily_search requires a non-empty 'query'.")
    domains = _domains_for(args.get("source"))
    time_period_days = args.get("time_period_days", DEFAULT_RECENCY_DAYS)

    payload = {
        "api_key": api_key,
        "query": query,
        "search_depth": "advanced",
        "max_results": TAVILY_NUM_RESULTS,
        "days": time_period_days,
        "topic": "general",
        "include_raw_content": True,
    }
    if domains:
        payload["include_domains"] = domains
    else:
        # source='Company': open-web career pages, excluding the known job boards.
        payload["exclude_domains"] = list(ALL_SOURCE_DOMAINS)
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                TAVILY_SEARCH_API_URL, json=payload, timeout=HTTP_TIMEOUT
            ) as resp:
                if resp.status == 429:
                    return _msg(
                        "Tavily API rate limit exceeded. Fall back to WebSearch/WebFetch for this query."
                    )
                if resp.status != 200:
                    body = await resp.text()
                    return _msg(f"Tavily search failed (HTTP {resp.status}): {body[:300]}")
                data = await resp.json()
    except Exception as e:
        return _msg(f"Tavily search error: {e}")

    results = []
    for r in data.get("results", []) or []:
        if not _is_valid_job_url(r.get("url")):
            continue
        results.append(
            {
                "title": r.get("title"),
                "url": r.get("url"),
                "snippet": (r.get("content") or "")[:SNIPPET_MAX_CHARS],
                "raw_content": r.get("raw_content"),
            }
        )
    results, skipped_known = _filter_known(results)
    results = [_enrich_result(r, recency_days=time_period_days, trust_search_recency=True) for r in results]
    return _ok(
        {
            "provider": "tavily",
            "domains": domains,
            "count": len(results),
            "skipped_known": skipped_known,
            "results": results,
        }
    )


# --- JobSpy structured scraping ------------------------------------------------------

# jobspy `site` column value → our source label. Google Jobs results point at the
# underlying employer posting, so they're labeled "Company".
_JOBSPY_SITE_TO_SOURCE = {
    "indeed": "Indeed",
    "linkedin": "LinkedIn",
    "glassdoor": "Glassdoor",
    "zip_recruiter": "ZipRecruiter",
    "google": "Company",
}
_JOBSPY_DEFAULT_SITES = ["indeed", "linkedin", "glassdoor", "zip_recruiter", "google"]


def _none_if_nan(value):
    """pandas NaN/NaT → None so results are JSON-serializable."""
    if value is None:
        return None
    try:
        if value != value:  # NaN is the only value that != itself
            return None
    except Exception:
        pass
    return value


async def _jobspy_search_impl(args: dict) -> dict:
    """Testable implementation behind the `jobspy_search` tool."""
    try:
        from jobspy import scrape_jobs
    except ImportError:
        return _msg(
            "python-jobspy is not installed — jobspy search is unavailable. Fall back to "
            "exa_search/tavily_search with source='LinkedIn'/'Indeed'/'Glassdoor'/"
            "'ZipRecruiter' for those sources."
        )

    search_term = (args.get("search_term") or "").strip()
    if not search_term:
        return _msg("jobspy_search requires a non-empty 'search_term'.")

    sites = args.get("site_name") or list(_JOBSPY_DEFAULT_SITES)
    sites = [s for s in sites if s in _JOBSPY_SITE_TO_SOURCE] or list(_JOBSPY_DEFAULT_SITES)
    time_period_days = args.get("time_period_days", DEFAULT_RECENCY_DAYS)
    results_wanted = args.get("results_wanted", JOBSPY_NUM_RESULTS)
    now = datetime.now(timezone.utc)

    try:
        df = await asyncio.to_thread(
            scrape_jobs,
            site_name=sites,
            search_term=search_term,
            google_search_term=f"{search_term} remote jobs in United States",
            location="United States",
            is_remote=True,
            hours_old=max(1, int(time_period_days) * 24),
            results_wanted=results_wanted,
            country_indeed="USA",
            description_format="markdown",
            verbose=0,
        )
    except Exception as e:
        return _msg(
            f"jobspy scrape error: {e}. Fall back to exa_search/tavily_search with "
            "source='LinkedIn'/'Indeed'/'Glassdoor'/'ZipRecruiter' for those sources."
        )

    results = []
    for row in (df.to_dict("records") if df is not None and not df.empty else []):
        row = {k: _none_if_nan(v) for k, v in row.items()}
        # Prefer the direct employer/ATS apply link over the board's redirect page —
        # board redirect URLs expire or bounce to search results far more often.
        direct = row.get("job_url_direct")
        url = direct if _is_valid_job_url(direct) else row.get("job_url")
        if not _is_valid_job_url(url):
            continue
        # Hard filters, applied here so the agent never spends tokens on rejects:
        # remote-only and full-time-only (unknown job_type is kept — scouts judge it).
        if row.get("is_remote") is False:
            continue
        job_type = (row.get("job_type") or "").lower()
        if job_type and "fulltime" not in job_type.replace("-", "").replace("_", ""):
            continue
        date_posted = row.get("date_posted")
        posted_days_ago = None
        if date_posted is not None:
            try:
                posted = datetime.fromisoformat(str(date_posted))
                if posted.tzinfo is None:
                    posted = posted.replace(tzinfo=timezone.utc)
                posted_days_ago = max(0, (now - posted).days)
            except Exception:
                pass
        results.append(
            {
                "title": row.get("title"),
                "company": row.get("company"),
                "url": url,
                "location": row.get("location") or "Remote",
                "snippet": (row.get("description") or "")[:JOBSPY_SNIPPET_MAX_CHARS],
                "date_posted": str(date_posted) if date_posted is not None else None,
                "posted_days_ago": posted_days_ago,
                # hours_old already filtered server-side at each board.
                "posted_within_7d": (
                    posted_days_ago <= time_period_days if posted_days_ago is not None else True
                ),
                "date_confidence": "parsed" if posted_days_ago is not None else "search_filtered",
                "remote": True if row.get("is_remote") else None,
                "full_time": True if "fulltime" in job_type.replace("-", "").replace("_", "") else None,
                "salary_min": row.get("min_amount"),
                "salary_max": row.get("max_amount"),
                "salary_currency": row.get("currency"),
                "source": _JOBSPY_SITE_TO_SOURCE.get((row.get("site") or "").lower(), "Company"),
                # Structured scrape, US-scoped (country_indeed=USA, location=United
                # States, is_remote=True): scouts must format these WITHOUT WebFetch.
                "pre_verified": True,
                "us_eligible": True,
            }
        )

    results, skipped_known = _filter_known(results)
    return _ok(
        {
            "provider": "jobspy",
            "sites": sites,
            "count": len(results),
            "skipped_known": skipped_known,
            "results": results,
        }
    )


@tool(
    "jobspy_search",
    "Bulk-scrape structured job postings from Indeed, LinkedIn, Glassdoor, ZipRecruiter, "
    "and Google Jobs in ONE call (remote, full-time, US-scoped, within the time window — "
    "all enforced server-side). Every result has pre_verified=true: its remote/full_time/"
    "date/US fields are already verified, so NEVER WebFetch these — pass them straight to "
    "a job_scout to format. Jobs the user already has are filtered out automatically "
    "(`skipped_known`). Call this FIRST, once per role; use exa_search/tavily_search only "
    "for the sources this does not cover (Workday, Greenhouse, Lever, Ashby, Dice, "
    "Wellfound, Built In, Company), or as a fallback if this tool returns an error.",
    {"search_term": str, "time_period_days": int, "results_wanted": int},
)
async def jobspy_search(args: dict) -> dict:
    return await _jobspy_search_impl(args)


# In-process MCP server exposing the search tools. The SDK names the tools
# `mcp__jobsearch__jobspy_search`, `mcp__jobsearch__exa_search`, and
# `mcp__jobsearch__tavily_search`.
JOB_SEARCH_SERVER_NAME = "jobsearch"
job_search_server = create_sdk_mcp_server(
    name=JOB_SEARCH_SERVER_NAME,
    version="1.0.0",
    tools=[jobspy_search, exa_search, tavily_search],
)

# Fully-qualified tool names to grant via allowed_tools / AgentDefinition.tools.
EXA_TOOL = f"mcp__{JOB_SEARCH_SERVER_NAME}__exa_search"
TAVILY_TOOL = f"mcp__{JOB_SEARCH_SERVER_NAME}__tavily_search"
JOBSPY_TOOL = f"mcp__{JOB_SEARCH_SERVER_NAME}__jobspy_search"
SEARCH_TOOL_NAMES = [JOBSPY_TOOL, EXA_TOOL, TAVILY_TOOL]
