"""
eleon web tools — real internet access.

- web_search: DuckDuckGo (instant-answer API + HTML results scrape), no key.
- fetch_url:  download a page and return cleaned text.
- download_file: save a URL to disk (default: Downloads).

These give the agent grounding in current, real information instead of
guessing, and the ability to pull anything off the internet and act on it.
"""
from __future__ import annotations

import re
from html import unescape
from pathlib import Path
from urllib.parse import quote_plus

import httpx

from core.tools import tool

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/125.0 Safari/537.36")
_HEADERS = {"User-Agent": _UA}


def _strip_html(html: str, limit: int) -> str:
    html = re.sub(r"<script[^>]*>.*?</script>", " ", html, flags=re.DOTALL | re.I)
    html = re.sub(r"<style[^>]*>.*?</style>", " ", html, flags=re.DOTALL | re.I)
    text = re.sub(r"<[^>]+>", " ", html)
    text = unescape(re.sub(r"\s+", " ", text)).strip()
    return text[:limit]


@tool("web_search", "Search the web and return the top results (title + "
      "snippet + link). Use this for current or unknown information.",
      {"type": "object",
       "properties": {"query": {"type": "string"},
                      "max_results": {"type": "integer", "default": 5}},
       "required": ["query"]})
async def web_search(query: str, max_results: int = 5) -> str:
    results: list[str] = []
    async with httpx.AsyncClient(timeout=12, headers=_HEADERS,
                                 follow_redirects=True) as client:
        # 1) Instant answer API (definitions, facts).
        try:
            api = (f"https://api.duckduckgo.com/?q={quote_plus(query)}"
                   "&format=json&no_redirect=1&no_html=1")
            data = (await client.get(api)).json()
            if data.get("AbstractText"):
                results.append(f"[Answer] {data['AbstractText']}")
        except Exception:
            pass

        # 2) HTML results page (organic links).
        try:
            html = (await client.get(
                f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"
            )).text
            for m in re.finditer(
                    r'result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', html):
                link, title = m.group(1), _strip_html(m.group(2), 120)
                if title:
                    results.append(f"- {title}\n  {link}")
                if len(results) >= max_results + 1:
                    break
        except Exception:
            pass

    if results:
        return "\n".join(results[:max_results + 1])
    return (f"No parsed results. Open manually: "
            f"https://duckduckgo.com/?q={quote_plus(query)}")


@tool("fetch_url", "Fetch a web page and return its cleaned text content "
      "(up to ~4000 chars). Use after web_search to read a specific page.",
      {"type": "object",
       "properties": {"url": {"type": "string"},
                      "max_chars": {"type": "integer", "default": 4000}},
       "required": ["url"]})
async def fetch_url(url: str, max_chars: int = 4000) -> str:
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    try:
        async with httpx.AsyncClient(timeout=15, headers=_HEADERS,
                                     follow_redirects=True) as client:
            r = await client.get(url)
            r.raise_for_status()
            return _strip_html(r.text, int(max_chars))
    except Exception as e:
        return f"[error] could not fetch {url}: {e}"


@tool("download_file", "Download a file from a URL to disk (default folder: "
      "Downloads). Returns the saved path.",
      {"type": "object",
       "properties": {"url": {"type": "string"},
                      "filename": {"type": "string"}},
       "required": ["url"]})
async def download_file(url: str, filename: str = "") -> str:
    if not filename:
        filename = url.split("/")[-1].split("?")[0] or "download.bin"
    dest = Path.home() / "Downloads" / filename
    try:
        async with httpx.AsyncClient(timeout=60, headers=_HEADERS,
                                     follow_redirects=True) as client:
            r = await client.get(url)
            r.raise_for_status()
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(r.content)
        return f"Downloaded {len(r.content)} bytes → {dest}"
    except Exception as e:
        return f"[error] download failed: {e}"
