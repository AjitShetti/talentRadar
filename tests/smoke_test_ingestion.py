"""Quick smoke test for schemas and parser logic (no LLM call needed)."""
import sys
sys.path.insert(0, "/app")

from ingestion.parsers.schemas import ParsedJobDescription, RawJobResult
from ingestion.parsers.jd_parser import JDParser

# ── Test 1: RawJobResult basic construction ──────────────────────────────────
r = RawJobResult(
    title="Senior Engineer at Stripe",
    url="https://stripe.com/jobs/123",
    content="We are hiring a senior engineer...",
    score=0.95,
)
assert r.best_content == "We are hiring a senior engineer..."
print("✅ Test 1 passed: RawJobResult.best_content")

# ── Test 2: ParsedJobDescription — skill deduplication ───────────────────────
jd = ParsedJobDescription(
    title="Senior Software Engineer",
    company="Stripe",
    skills=["Python", "Go", "Kubernetes", "Python", "go"],  # two dupes
    experience="5+ years",
    location="San Francisco, CA",
    is_remote=False,
    salary="$180k–$240k/year",
    salary_min=180000,
    salary_max=240000,
    salary_currency="USD",
    employment_type="full_time",
    seniority="senior",
    source_url="https://stripe.com/jobs/123",
    raw_text="raw job text here",
)
print("✅ Test 2 passed: title =", jd.title, "| company =", jd.company)
print("   Skills (deduped):", jd.skills)
assert "Python" in jd.skills and len(jd.skills) == jd.skills.count("Python") + (len(jd.skills) - 1)

# ── Test 3: auto salary-range swap ───────────────────────────────────────────
jd2 = ParsedJobDescription(
    title="Data Scientist",
    company="Acme",
    salary_min=150000,
    salary_max=90000,   # intentionally inverted
    raw_text="test content",
)
assert jd2.salary_min == 90000 and jd2.salary_max == 150000, "Salary swap failed!"
print("✅ Test 3 passed: inverted salary auto-swapped:", jd2.salary_min, "->", jd2.salary_max)

# ── Test 4: enum normalisation ────────────────────────────────────────────────
jd3 = ParsedJobDescription(
    title="Intern",
    company="StartupX",
    employment_type="Full-Time",  # should normalise to full_time
    seniority="Senior",           # should normalise to senior
    raw_text="some content",
)
assert jd3.employment_type == "full_time"
assert jd3.seniority == "senior"
print("✅ Test 4 passed: enum normalisation works:", jd3.employment_type, jd3.seniority)

# ── Test 5: to_job_kwargs() mapping ──────────────────────────────────────────
kwargs = jd.to_job_kwargs()
required_keys = ["title", "skills", "is_remote", "salary_min", "salary_max", "salary_currency"]
for k in required_keys:
    assert k in kwargs, f"Missing key: {k}"
print("✅ Test 5 passed: to_job_kwargs() has all required keys:", required_keys)

# ── Test 6: JDParser._extract_json ───────────────────────────────────────────
extracted = JDParser._extract_json('{"title": "Engineer", "company": "Corp"}')
assert extracted["title"] == "Engineer"
print("✅ Test 6 passed: _extract_json (clean JSON)")

extracted2 = JDParser._extract_json(
    'Here is the output:\n```json\n{"title": "SWE", "company": "FAANG"}\n```'
)
assert extracted2["title"] == "SWE"
print("✅ Test 6b passed: _extract_json (markdown-wrapped JSON)")

print("\n🎉 All smoke tests passed!")
