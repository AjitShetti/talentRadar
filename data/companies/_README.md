# Curated company catalogue

Each `<city>.json` file is a hand-maintained directory of tech employers with an
engineering presence in that city. It powers the **Company Intel** section
(`/api/v1/company-intel/`, `frontend/app/company-intel`), and is loaded into
Postgres by:

```bash
python scripts/seed_companies.py            # all cities
python scripts/seed_companies.py --city bengaluru --dry-run
```

The seeder upserts on `domain`, so re-running it is safe and edits to the JSON
propagate on the next run.

## Record shape

| field | notes |
| --- | --- |
| `name`, `domain`, `website_url` | `domain` is the upsert key — keep it unique |
| `careers_url` | linked from the detail panel and seeded as a `careers_page` contact |
| `github_org` | GitHub org slug only, not a URL. Verified live at read time — a wrong slug 404s and the panel simply hides the open-source block |
| `linkedin_url` | company page |
| `tier` | `big_tech` \| `gcc` \| `unicorn` \| `scaleup` \| `startup` \| `services` |
| `industry` | free text, drives the industry facet |
| `office_cities` | Indian cities with an engineering office — the directory filters on this, *not* on `hq_city` (Google's HQ is not in India but it hires here) |
| `description` | one or two plain sentences on what the company actually does |
| `tech_stack` | technologies the company has publicly discussed using |
| `contacts` | see below |

## About `contacts` and accuracy

`contacts` entries here are **curated public channels only** — a careers page, or
a careers inbox a company prints on its own site. Every entry carries a
`source_url`. Named individuals are never seeded: they arrive either from a user
saving one, or from the sourced web lookup
(`POST /company-intel/{id}/contacts/discover`), which returns candidates with the
page they were found on and marks them unverified.

The rest of the catalogue is curated from public knowledge and **has not been
field-verified**. Treat `founded_year`, `employee_count_range` and `tech_stack`
as indicative. Fix anything you find wrong directly in the JSON and re-seed.
