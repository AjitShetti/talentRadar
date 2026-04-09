"""
agents/prompts/intent_prompt.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Prompt templates for intent extraction and query parsing.
"""

INTENT_EXTRACTION_PROMPT = """\
Extract the user's search intent from their query.

Return a JSON object with:
- intent: "search_jobs" | "find_candidates" | "market_trends" | "company_info" | "general"
- keywords: list of important search terms
- skills: list of technical/professional skills mentioned
- location: location string or null
- is_remote: boolean or null
- seniority: seniority level or null
- employment_type: employment type or null
- company: company name or null

Query: {query}
"""

QUERY_REWRITE_PROMPT = """\
Rewrite the following user query into an optimized search query for job matching.

Focus on:
1. Job titles and roles
2. Required skills and technologies
3. Location preferences
4. Seniority level
5. Employment type

Original query: {query}

Return a clean search query string optimized for matching.
"""
