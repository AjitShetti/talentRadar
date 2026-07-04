"""
ingestion/scrapers/ats_crawler.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Generic Applicant Tracking System (ATS) job crawler.

Uses DuckDuckGo to discover job postings on known ATS platforms 
(Greenhouse, Lever, Ashby, etc.) and extracts the raw HTML text using BeautifulSoup.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from bs4 import BeautifulSoup
from duckduckgo_search import DDGS

from ingestion.parsers.schemas import RawJobResult

logger = logging.getLogger(__name__)

_DEFAULT_RAW_DIR = Path(os.getenv("RAW_DATA_DIR", "data/raw"))

_TARGET_DOMAINS = [
    "boards.greenhouse.io",
    "jobs.lever.co",
    "jobs.ashbyhq.com"
]

class ATSCrawler:
    def __init__(
        self,
        raw_data_dir: Path | str | None = None,
        timeout: float = 15.0,
    ) -> None:
        self._raw_dir = Path(raw_data_dir or _DEFAULT_RAW_DIR)
        self._timeout = timeout
        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        })

    def search_jobs(
        self,
        role: str,
        location: str = "Remote",
        *,
        count: int = 10,
    ) -> list[RawJobResult]:
        """
        Use DuckDuckGo to find ATS job links for a specific role/location.
        """
        # Build query for DDG: e.g. "Software Engineer" Remote site:boards.greenhouse.io OR site:jobs.lever.co
        sites = " OR ".join([f"site:{domain}" for domain in _TARGET_DOMAINS])
        query = f'"{role}" {location} ({sites})'
        
        logger.info(f"Searching DDG: {query}")
        
        urls = []
        try:
            with DDGS() as ddgs:
                results = ddgs.text(query, max_results=count)
                for r in results:
                    urls.append(r.get('href'))
        except Exception as e:
            logger.error(f"DDG search failed: {e}")
            return []

        # Scrape each URL
        raw_results = []
        for url in urls:
            if not url:
                continue
            
            # Basic validation that it's actually an ATS link
            if not any(domain in url for domain in _TARGET_DOMAINS):
                continue
                
            logger.info(f"Scraping ATS URL: {url}")
            try:
                resp = self._session.get(url, timeout=self._timeout)
                resp.raise_for_status()
                
                # Parse text
                soup = BeautifulSoup(resp.text, "html.parser")
                
                # Try to extract title, fallback to HTML title
                title = soup.title.string if soup.title else "Unknown Title"
                
                # Clean up text by removing script/style tags
                for script in soup(["script", "style"]):
                    script.extract()
                    
                text_content = soup.get_text(separator="\n", strip=True)
                
                raw_results.append(
                    RawJobResult(
                        title=title.strip(),
                        url=url,
                        content=text_content[:500], # Snippet
                        raw_content=text_content,   # Full text for LLM
                        score=1.0,
                    )
                )
                
                time.sleep(1) # Be nice to servers
            except Exception as e:
                logger.warning(f"Failed to scrape {url}: {e}")

        return raw_results

    def save_raw(
        self,
        results: list[RawJobResult],
        *,
        run_id: str,
        role: str = "unknown",
        location: str = "unknown",
    ) -> list[Path]:
        slug = _slugify(f"{role}_{location}")
        dest_dir = self._raw_dir / run_id / slug
        dest_dir.mkdir(parents=True, exist_ok=True)

        written: list[Path] = []
        for result in results:
            content_hash = hashlib.md5(result.url.encode()).hexdigest()[:12]
            file_path = dest_dir / f"{content_hash}.json"

            payload = {
                "fetched_at": datetime.now(tz=timezone.utc).isoformat(),
                "query_role": role,
                "query_location": location,
                "run_id": run_id,
                **result.model_dump(),
            }

            file_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            written.append(file_path)

        logger.info(f"Saved {len(written)} raw results to {dest_dir}")
        return written

    def __enter__(self) -> "ATSCrawler":
        return self

    def __exit__(self, *_: Any) -> None:
        self._session.close()

def _slugify(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_-]+", "_", text)
    return text[:64]
