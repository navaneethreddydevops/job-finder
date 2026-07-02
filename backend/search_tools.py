"""In-process SDK tools that let the orchestrator fetch job listings via the **Exa** and
**Tavily** search APIs instead of relying solely on Claude's built-in `WebSearch` (which
returned too few results).

Both tools are model-driven: the orchestrator calls `exa_search` / `tavily_search` with a
query and its assigned `source` ("LinkedIn" or "Workday"). The tool scopes the search to
that source's domain and returns a JSON list of candidate postings.

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

import aiohttp

from claude_agent_sdk import tool, create_sdk_mcp_server

EXA_API_URL = "https://api.exa.ai/search"
TAVILY_SEARCH_API_URL = "https://api.tavily.com/search"
TAVILY_EXTRACT_API_URL = "https://api.tavily.com/extract"

# How many results to request per call — conservative to avoid API rate limits.
# Original limits (30/25) were safe; increased slightly but requests throttled heavily.
EXA_NUM_RESULTS = 30
TAVILY_NUM_RESULTS = 25
RECENCY_DAYS = 7
HTTP_TIMEOUT = 30
# Tavily's /extract endpoint is called in batches to backfill Exa results with verified
# content; cap batch size defensively even though Tavily accepts more per call.
EXTRACT_BATCH_SIZE = 20


def _domains_for(source: str) -> list[str]:
    """Map an assigned source label to the domain(s) to scope the search to."""
    s = (source or "").strip().lower()
    if "workday" in s:
        return ["myworkdayjobs.com"]
    if "linkedin" in s:
        return ["linkedin.com"]
    # Unknown / unspecified → search both allowed sources.
    return ["linkedin.com", "myworkdayjobs.com"]


def _since_iso() -> str:
    return (datetime.now(timezone.utc) - timedelta(days=RECENCY_DAYS)).strftime(
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


def _enrich_result(result: dict, trust_search_recency: bool = False) -> dict:
    """Annotate a result with remote/full_time/posted_within_7d, parsed from its rendered
    page text where possible.

    `trust_search_recency`: when True (Tavily-sourced results only), Tavily's own `days=7`
    search parameter already time-filtered this result server-side before we ever see it.
    So if our page-text parse can't find an explicit date (the crawler is blocked on some
    Workday tenants, or the phrasing doesn't match), default `posted_within_7d` to True
    rather than unknown — Tavily's own filter is still a real (if less precise) guarantee.
    An explicit *stale* parse (page text says >7 days) always overrides this default.
    """
    signals = _parse_listing_signals(result.get("raw_content") or "", result.get("url") or "")
    days = signals["posted_days_ago"]
    result["remote"] = signals["remote"]
    result["full_time"] = signals["full_time"]
    result["posted_days_ago"] = days
    result["posted_text"] = signals["posted_text"]
    if days is not None:
        result["posted_within_7d"] = days <= RECENCY_DAYS
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
    "Search the Exa API for recent job postings on the assigned source (LinkedIn or Workday "
    "careers). Returns a JSON array of candidate postings, each pre-annotated with `remote` "
    "(bool|null), `full_time` (bool|null), `posted_days_ago` (number|null), and "
    "`posted_within_7d` (bool|null) — computed by rendering the page server-side, since "
    "WebFetch cannot render Workday's JS pages. KEEP a candidate only if posted_within_7d is "
    "true, remote is true (or null with the title/snippet clearly indicating remote), and "
    "full_time is not explicitly false. If a field is null, the page couldn't be verified — "
    "use judgment from the title/snippet, or drop it if unsure.",
    {"query": str, "source": str},
)
async def exa_search(args: dict) -> dict:
    # Aggressive throttle — 4s between calls to prevent Claude API rate limits.
    await asyncio.sleep(4.0)
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

    payload = {
        "query": query,
        "type": "auto",
        "numResults": EXA_NUM_RESULTS,
        "includeDomains": domains,
        "contents": {"text": {"maxCharacters": 500}},
    }
    # Exa's published-date filter works for LinkedIn but zeroes out Workday portals
    # (their listing pages lack a publish date Exa recognizes).
    if "myworkdayjobs.com" not in domains:
        payload["startPublishedDate"] = _since_iso()
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
                results.append(
                    {
                        "title": r.get("title"),
                        "url": r.get("url"),
                        "snippet": (r.get("text") or "")[:500],
                    }
                )

            # Backfill verified remote/full_time/posted_within_7d via Tavily's extractor
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

    results = [_enrich_result(r) for r in results]
    return _ok({"provider": "exa", "domains": domains, "count": len(results), "results": results})


@tool(
    "tavily_search",
    "Search the Tavily API for recent job postings on the assigned source (LinkedIn or "
    "Workday careers). Returns a JSON array of candidate postings, each pre-annotated with "
    "`remote` (bool|null), `full_time` (bool|null), `posted_days_ago` (number|null), and "
    "`posted_within_7d` (bool|null) — parsed from the rendered page (Workday's 'remote "
    "type'/'time type'/'posted on' labels, or LinkedIn's relative post-date). KEEP a "
    "candidate only if posted_within_7d is true, remote is true (or null with the title/"
    "snippet clearly indicating remote), and full_time is not explicitly false. If a field "
    "is null, the page couldn't be verified — use judgment from the title/snippet, or drop "
    "it if unsure.",
    {"query": str, "source": str},
)
async def tavily_search(args: dict) -> dict:
    # Aggressive throttle — 4s between calls to prevent Claude API rate limits.
    await asyncio.sleep(4.0)
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

    payload = {
        "api_key": api_key,
        "query": query,
        "search_depth": "advanced",
        "max_results": TAVILY_NUM_RESULTS,
        "include_domains": domains,
        "days": RECENCY_DAYS,
        "topic": "general",
        "include_raw_content": True,
    }
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
        results.append(
            {
                "title": r.get("title"),
                "url": r.get("url"),
                "snippet": (r.get("content") or "")[:500],
                "raw_content": r.get("raw_content"),
            }
        )
    results = [_enrich_result(r, trust_search_recency=True) for r in results]
    return _ok({"provider": "tavily", "domains": domains, "count": len(results), "results": results})


# In-process MCP server exposing the two search tools. The SDK names the tools
# `mcp__jobsearch__exa_search` and `mcp__jobsearch__tavily_search`.
JOB_SEARCH_SERVER_NAME = "jobsearch"
job_search_server = create_sdk_mcp_server(
    name=JOB_SEARCH_SERVER_NAME,
    version="1.0.0",
    tools=[exa_search, tavily_search],
)

# Fully-qualified tool names to grant via allowed_tools / AgentDefinition.tools.
EXA_TOOL = f"mcp__{JOB_SEARCH_SERVER_NAME}__exa_search"
TAVILY_TOOL = f"mcp__{JOB_SEARCH_SERVER_NAME}__tavily_search"
SEARCH_TOOL_NAMES = [EXA_TOOL, TAVILY_TOOL]
