# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Job seekers in India applying to tech roles — the app is India-only by design (ingestion and search actively filter to Indian postings). This is a multi-user product: people will sign up cold, not just the builder, so the design must work for a stranger meeting it for the first time, not just for someone who already knows the app's internals.

They are mid-search: actively tracking multiple applications, practicing for interviews, tailoring a resume per role, and researching companies before reaching out — a process that is logistically heavy and often stressful.

## Product Purpose

TalentRadar is an AI-powered career operating system for the Indian tech job market. It ingests job postings from many sources, extracts structured signals via LLMs, and gives a job seeker one workspace to: search and save roles, track applications through a pipeline, practice mock interviews (LangGraph-driven, voice-first with a typed fallback), build and tailor a resume (structured section editor with live LaTeX/PDF compilation and ATS scoring), research employers (tech stack, GitHub activity, talent contacts), and ask a personal "Career Copilot" natural-language questions about their own search.

Success means the user spends less time context-switching between job boards, trackers, and prep tools, and gets daily, concrete direction on what to do next.

## Positioning

Unlike a generic job board (Naukri, LinkedIn, Instahyre) or a standalone resume/interview tool, TalentRadar's copilot and daily briefing reason over the user's *own* data — their applications, interview scores, skill gaps, saved roles — via RAG and a LangGraph agent, rather than surfacing generic listings or advice. The daily "briefing" is deterministic (computed from the user's real records: stale applications, interview momentum, unfinished saves), not just an LLM guess, and the UI needs to keep that trustworthiness legible.

## Operating Context

Frequent, often daily use during an active job search. A typical session: check the dashboard for what needs attention today, search/save new roles, move applications through saved → applied → screening → interview → offer, run a mock interview (by voice or typed), iterate a resume against a specific target role, or look up a company before applying or reaching out to a contact. Onboarding (right after signup) collects target role, location, years of experience, skills, and an optional resume upload before first real use.

## Capabilities and Constraints

- India-only scope is enforced by the backend (non-Indian postings are dropped/filtered); this is a permanent product constraint, not a launch limitation.
- Frontend is Next.js 14 (App Router), plain React, hand-authored CSS (no Tailwind, no component library) — `lucide-react` is the only UI dependency beyond React/Next itself.
- Auth is email/password (JWT); onboarding runs immediately after signup and can be safely skipped/partial.
- Voice interview mode depends on browser mic + speech APIs; a typed-answer fallback must always exist and never feel like a downgrade path that was bolted on.
- Resume Studio compiles real LaTeX to a real PDF server-side, and computes ATS-fit score-and-suggestions live, client-side, on every keystroke.
- Company Intel pulls real GitHub org/repo data and lets the user log or discover talent contacts — the app explicitly never fabricates a contact or invents an email from a naming pattern, and the UI must keep signaling provenance (published vs. self-added vs. verified/unverified).
- The Career Copilot and daily briefing are grounded in the signed-in user's own stored data (profile, tracker, interview history, saved memories) — copy and visual treatment should never suggest it is guessing or generic.

## Brand Commitments

Product name: **TalentRadar**. Established feature names to keep: "Career Copilot," "Interview Lab," "Resume Studio," "Company Intel," "Overview," "Find roles." These names are part of the product's vocabulary and should carry forward even where surfaces are visually rebuilt.

Visual identity (palette, typography, motion, brand mark) is explicitly **not** locked — the user has confirmed it is open to full replacement as part of this redesign, not just refinement of the current near-black/chartreuse/Fraunces look.

## Evidence on Hand

No real user testimonials, pricing, or case studies exist and none should be fabricated or implied. Company Intel content (tech stack, GitHub stats, contacts) is real ingested/user-entered data — never placeholder or invented — and the UI's job is to represent it honestly (including "no data yet" states), not to dress up empty states as if data exists.

## Product Principles

1. Ground everything in the user's real data — never let the interface imply the copilot or briefing is guessing or generic.
2. Today's single most important action should be legible before anything else competes for attention.
3. The product must read as considered and trustworthy to a stranger meeting it cold, not only usable to someone who already knows it.
4. Reduce cognitive load during an inherently stressful process — calm, clear, and momentum-oriented rather than anxiety-inducing or gamified.
5. Each surface (search, track, practice, write, research, ask) does a genuinely different job — the design should let each one look and feel like what it is, instead of forcing all of them into one interchangeable bordered-card-in-a-grid template.

## Accessibility & Inclusion

No formally established standard beyond general web accessibility practice. The current build already respects `prefers-reduced-motion` and visible focus states; the redesign should preserve and extend that baseline (contrast, keyboard access, motion sensitivity) rather than regress it in pursuit of visual ambition.
