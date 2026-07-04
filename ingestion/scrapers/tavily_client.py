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

    def search(self, query: str, max_results: int = 10) -> List[RawJobResult]:
        payload = {
            "api_key": self._api_key,
            "query": query,
            "search_depth": "advanced",
            "max_results": max_results,
            "include_raw_content": True
        }
        
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

    def search_jobs(self, role: str, location: str, count: int = 10) -> List[RawJobResult]:
        query = f"{role} jobs in {location}"
        return self.search(query, max_results=count)

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
