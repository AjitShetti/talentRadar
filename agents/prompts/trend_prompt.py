"""
agents/prompts/trend_prompt.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Prompt templates for market trend analysis and insights generation.
"""

TREND_ANALYSIS_PROMPT = """\
You are a market intelligence analyst for TalentRadar.

Given job market data for the query context, provide a **very brief and concise** summary (max 3-4 short bullet points).
Focus only on the most critical highlights regarding:
- Skill Demand Trends
- Salary Insights
- Top Cities Analysis

Do NOT generate a long paragraph. Keep it extremely short and easy to read.

Context: {context}
Job Count: {job_count}
Top Skills: {top_skills}
Average Salary: {avg_salary}

Format as a short markdown list.
"""

SALARY_INSIGHT_PROMPT = """\
Analyze the salary data and provide insights:

Role: {role}
Location: {location}
Salary Range: {min_salary} - {max_salary} {currency}
Market Context: {market_context}

Provide:
1. Whether this is competitive for the market
2. Percentile estimate (if enough data)
3. Recommendations
"""

SKILL_GAP_ANALYSIS = """\
Compare the candidate's skills against the job requirements.

Candidate Skills: {candidate_skills}
Job Required Skills: {job_skills}

Identify:
1. **Strong Matches**: Skills the candidate has that the job requires
2. **Skill Gaps**: Required skills the candidate is missing
3. **Bonus Skills**: Additional skills that add value
4. **Match Score**: Percentage match based on skills
"""
