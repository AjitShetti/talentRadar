# Original User Request

## Initial Request — 2026-07-22T10:20:56Z

# Teamwork Project Prompt — Draft

Improve the job search logic to accurately return relevant results for broad technology roles like "java dev" or "software engineer". Expand the ingestion pipeline to fetch Indian job postings from LinkedIn, Naukri, and Indeed by tweaking the existing Tavily client.

Working directory: d:\projects\talentRadar
Integrity mode: development

## Requirements

### R1. Improve Job Search Relevance
Update the search retrieval logic so that queries for broad technology roles (e.g., "java dev", "software engineer") successfully return the relevant jobs existing in the database.

### R2. Expand Indian Job Sources
Update the job fetching logic to include Indian job postings from LinkedIn, Naukri, and Indeed. Use the existing Tavily client and optimize its queries to target these specific portals.

## Acceptance Criteria

### Job Search Fix
- [ ] An agent-as-judge verifies that a search for "java dev" and "software engineer" returns at least 3 relevant job postings from the database, rather than returning empty results.

### Indian Job Sources
- [ ] An agent-as-judge verifies that the ingestion pipeline successfully fetches and parses at least 1 job posting from an Indian location via LinkedIn, Naukri, or Indeed using the Tavily client.
