"""
agents/prompts/trend_prompt.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Prompt templates for market trend analysis and insights generation.
"""

TREND_ANALYSIS_PROMPT = """\
You are a market intelligence analyst for TalentRadar.

Given job market data for the query context, provide:
1. **Skill Demand Trends**: Which skills are most in-demand?
2. **Salary Insights**: What are typical salary ranges for this role?
3. **Location Analysis**: Where are most opportunities located?
4. **Market Summary**: Overall market conditions and trends

Context: {context}
Job Count: {job_count}
Top Skills: {top_skills}
Average Salary: {avg_salary}

Format as a professional market analysis report in markdown.
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
