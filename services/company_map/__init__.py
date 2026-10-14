"""
services/company_map
~~~~~~~~~~~~~~~~~~~~
The I/O behind the company map: importers (Wikidata, OSM, company sites,
Nominatim, news feeds, GitHub, Tavily), the store that merges what they find
by source precedence, and the refresh steps the cron endpoints run.

Every importer is a thin fetch around a pure parser tested on recorded
fixtures. The rules themselves live in ``domain/``.
"""
