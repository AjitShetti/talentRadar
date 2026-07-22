# Master Plan: TalentRadar Enhancement

## Project Overview
TalentRadar is an application for job search and fetching.
This project aims to fulfill two major requirements:
1. **R1: Improve Job Search Relevance** — Update search retrieval logic so queries for broad technology roles (e.g., "java dev", "software engineer") return relevant jobs from the database (at least 3 relevant job postings per query).
2. **R2: Expand Indian Job Sources** — Update job fetching logic to include Indian job postings from LinkedIn, Naukri, and Indeed using the existing Tavily client, fetching and parsing at least 1 job posting from an Indian location.

## Milestone Plan

| Milestone | Name | Objective | Target Criteria | Status |
|-----------|------|-----------|-----------------|--------|
| M0 | Codebase & Test Infra Exploration | Understand project structure, backend framework, DB schema, existing search & fetch logic, and test harness | Exploration report generated in `.agents/explorer_0/analysis.md` | Completed |
| M1 | R1: Job Search Relevance Improvement | Refactor search retrieval algorithm for broad queries (e.g., query expansion, keyword matching, semantic search) | Broad queries return >= 3 relevant jobs from DB; tests pass | Completed |
| M2 | R2: Expand Indian Job Sources | Update Tavily search/fetch queries & parser for Indian postings across LinkedIn, Naukri, Indeed | Fetch & parse >= 1 job posting from an Indian location; tests pass | Completed |
| M3 | Integration, Testing & Verification | Comprehensive end-to-end testing, review, and forensic integrity audit | All unit & E2E tests pass, zero integrity violations | Completed |

## Workflow & Verification Strategy
- For each milestone:
  1. **Explorer**: Analyze codebase, identify exact files/methods needing changes, design implementation strategy.
  2. **Worker**: Implement changes, add unit/integration tests, execute build and test commands, report test output.
  3. **Reviewers / Challengers**: Review code quality, check boundary conditions, verify requirement fulfillment.
  4. **Forensic Auditor**: Verify code integrity, check for hardcoded test data or fake implementations.
  5. **Gate Check**: Verify all pass criteria before marking milestone complete.
