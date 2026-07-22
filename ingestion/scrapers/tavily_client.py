import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Optional, List

import httpx

from ingestion.parsers.schemas import RawJobResult

def _slugify(text: str) -> str:
    """Convert text to a slug."""
    text = text.lower()
    text = re.sub(r'[^a-z0-9\s-]', '', text)
    text = re.sub(r'[\s-]+', '_', text).strip('_')
    return text[:64]


INDIAN_JOB_DOMAINS = [
    "linkedin.com",
    "naukri.com",
    "indeed.com",
    "in.indeed.com",
]


def detect_source_from_url(url: str) -> str:
    """
    Identify the origin job platform from a URL.
    Returns: 'linkedin' | 'naukri' | 'indeed' | 'greenhouse' | 'lever' | 'ashby' | 'tavily_search'
    """
    if not url:
        return "tavily_search"
    url_lower = url.lower()
    if "linkedin.com" in url_lower:
        return "linkedin"
    elif "naukri.com" in url_lower:
        return "naukri"
    elif "indeed.com" in url_lower:
        return "indeed"
    elif "greenhouse.io" in url_lower:
        return "greenhouse"
    elif "lever.co" in url_lower:
        return "lever"
    elif "ashbyhq.com" in url_lower:
        return "ashby"
    return "tavily_search"


class TavilyJobScraper:
    def __init__(self, api_key: Optional[str] = None, raw_data_dir: Optional[str | Path] = None):
        if not api_key:
            from config.settings import get_settings
            api_key = get_settings().tavily_api_key
            if not api_key:
                raise ValueError("TAVILY_API_KEY is required in configuration.")
        self._api_key = api_key
        self._client = httpx.Client()
        self._raw_dir = Path(raw_data_dir) if raw_data_dir else Path("data") / "raw"

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self._client.close()

    def search(
        self,
        query: str,
        max_results: int = 10,
        include_domains: Optional[List[str]] = None,
        exclude_domains: Optional[List[str]] = None,
    ) -> List[RawJobResult]:
        payload = {
            "api_key": self._api_key,
            "query": query,
            "search_depth": "advanced",
            "max_results": max_results,
            "include_raw_content": True
        }
        if include_domains:
            payload["include_domains"] = include_domains
        if exclude_domains:
            payload["exclude_domains"] = exclude_domains
        
        response = self._client.post("https://api.tavily.com/search", json=payload)
        response.raise_for_status()
        data = response.json()
        
        results = []
        for item in data.get("results", []):
            url = item.get("url", "").strip()
            if not url:
                continue
            
            try:
                result = RawJobResult(
                    title=item.get("title", ""),
                    url=url,
                    content=item.get("content", ""),
                    score=item.get("score", 0.0),
                    published_date=item.get("published_date"),
                    raw_content=item.get("raw_content")
                )
                results.append(result)
            except Exception:
                continue
                
        return results

    def search_jobs(
        self,
        role: str,
        location: str,
        count: int = 10,
        include_domains: Optional[List[str]] = None,
        use_site_operators: bool = False,
    ) -> List[RawJobResult]:
        query_str = f"{role} jobs in {location}"
        if use_site_operators and include_domains:
            site_query = " OR ".join([f"site:{d}" for d in include_domains])
            query_str = f"{query_str} ({site_query})"

        return self.search(
            query_str,
            max_results=count,
            include_domains=include_domains,
        )

    def save_raw(self, results: List[RawJobResult], run_id: str, role: str, location: str) -> List[Path]:
        role_slug = _slugify(role)
        loc_slug = _slugify(location)
        dest_dir = self._raw_dir / role_slug / loc_slug
        dest_dir.mkdir(parents=True, exist_ok=True)
        
        paths = []
        for result in results:
            url_hash = hashlib.md5(result.url.encode()).hexdigest()
            file_path = dest_dir / f"{run_id}_{url_hash}.json"
            
            # Using model_dump (pydantic v2) or dict()
            data = result.model_dump() if hasattr(result, "model_dump") else result.dict()
            data["run_id"] = run_id
            data["fetched_at"] = datetime.utcnow().isoformat()
            
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            paths.append(file_path)
            
        return paths

    def load_raw(self, path: Path) -> dict:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
