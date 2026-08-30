import type { Metadata } from 'next'
import Link from 'next/link'
import {
  ArrowUpRight, Building2, Check, Compass, Database, FileText, MapPin,
  MessageSquareText, Mic, Plus, Search, ShieldCheck,
} from 'lucide-react'
import LandingNav from '@/components/landing/LandingNav'
import Reveal from '@/components/landing/Reveal'
import SignedInRedirect from '@/components/landing/SignedInRedirect'
import {
  CopilotMock, HeroBoard, IntelMock, InterviewMock, PipelineMock, ResumeMock, SearchMock,
} from '@/components/landing/BoardMock'
import './landing.css'

/*
  THESIS: the public page is the same instrument as the app, seen from outside —
  a visitor should recognise the board they are about to sign into, not meet a
  brochure for a different-looking product.
  OWN-WORLD: graphite ground with one amber lamp; hairline rules instead of
  boxes; machined double-bezel enclosures so nothing sits flat; Big Shoulders
  Text at display scale, Fragment Mono on every figure.
  STORY: a stranger arrives cold, sees what today's board would say, learns what
  each of the six surfaces actually does, is told plainly how the product stays
  honest, and signs up — or does not — on real information.
  HONESTY: no testimonials, no user counts, no pricing, no logo wall. Every
  figure on this page sits inside a board explicitly labelled as an example.
*/

export const metadata: Metadata = {
  title: 'TalentRadar — One board for your entire job search',
  description:
    'TalentRadar pulls Indian tech roles from every source, tracks every application, runs mock interviews, tailors your resume, and tells you what to do today — grounded in your own records, not generic advice.',
  keywords: ['job search India', 'tech jobs India', 'application tracker', 'mock interview practice', 'ATS resume', 'career copilot'],
  openGraph: {
    title: 'TalentRadar — One board for your entire job search',
    description: 'Search, track, practise, write and research from a single board. India-only tech roles, grounded in your own data.',
    type: 'website',
    siteName: 'TalentRadar',
  },
  twitter: {
    card: 'summary_large_image',
    title: 'TalentRadar — One board for your entire job search',
    description: 'Search, track, practise, write and research from a single board. India-only tech roles, grounded in your own data.',
  },
}

const STEPS = [
  {
    n: '01',
    title: 'Tell it what you are aiming for',
    body: 'Onboarding takes a target role, a location, your years of experience, your skills, and — optionally — your resume. It takes about two minutes, and you can skip any part of it and come back.',
  },
  {
    n: '02',
    title: 'It watches the Indian market for you',
    body: 'TalentRadar ingests postings from company ATS boards and job sources across the country, drops anything that does not resolve to India, and uses an LLM to pull structured signals — seniority, stack, compensation, remote policy — out of each description.',
  },
  {
    n: '03',
    title: 'You open one board each morning',
    body: 'Your briefing is computed from your own records: which applications have gone quiet, where your interview scores moved, which skills keep appearing in roles you save. It names one thing to do first.',
  },
]

const FAQS = [
  {
    q: 'Is TalentRadar only for jobs in India?',
    a: 'Yes, and that is a deliberate product decision rather than a launch limitation. The ingestion pipeline drops postings that do not resolve to an Indian location, and search is scoped to India at query time. Being narrow is what lets the matching, the compensation ranges and the company research actually be useful instead of approximately right everywhere.',
  },
  {
    q: 'Where do the job postings come from?',
    a: 'Directly from company applicant-tracking boards — Greenhouse, Lever and Ashby — plus job-board and search-based sources. Postings are deduplicated by URL before they reach you, so the same role from three sources shows up once.',
  },
  {
    q: 'Does the copilot make things up?',
    a: 'The daily briefing is not an LLM guess — it is computed from your stored records, so a claim like "this application has been quiet for nine days" is arithmetic on a date you entered. The conversational copilot answers over your own applications, interview history, saved roles and profile, and points at the records it used. It can still phrase something poorly, as any language model can, but it is not inventing the underlying facts.',
  },
  {
    q: 'Do I need a microphone for the mock interviews?',
    a: 'No. Voice mode uses your browser microphone and speech APIs, but a typed-answer mode runs the same interview graph and produces the same scoring across the same dimensions. Neither is a degraded version of the other.',
  },
  {
    q: 'Will it find me email addresses for recruiters?',
    a: 'Only ones that genuinely exist. Company Intel shows real published contacts and lets you record people you have found yourself, and it labels each one by where it came from and whether it is verified. It will not guess an address from a naming pattern, which is the one thing that makes contact data actively harmful.',
  },
  {
    q: 'What does it cost?',
    a: 'Nothing is being charged today, and there is no pricing to announce yet. If that changes you will be told before it affects your account.',
  },
]

export default function LandingPage() {
  return (
    <div className="lp">
      <SignedInRedirect />

      <div className="lp-orbs" aria-hidden="true">
        <div className="lp-orb lp-orb-a" />
        <div className="lp-orb lp-orb-b" />
      </div>

      <LandingNav />

      <main>
        {/* ── Hero: editorial split — display type left, live board right ─── */}
        <section className="lp-hero">
          <div className="lp-wrap">
            <div className="lp-hero-grid">
              <Reveal>
                <span className="lp-eyebrow">
                  <i className="lp-eyebrow-dot" aria-hidden="true" />
                  India · Tech roles only
                </span>
                <h1 className="lp-h1">Your whole job search, on <em>one board</em>.</h1>
                <p className="lp-lead">
                  Six tabs, three trackers and a spreadsheet is not a system. TalentRadar pulls Indian tech
                  roles from every source, tracks every application, runs your interview practice, tailors
                  your resume, and each morning tells you the one thing to do first — computed from your
                  own records, not generic advice.
                </p>
                <div className="lp-cta-row">
                  <Link className="lp-cta" href="/signup">
                    Create your account
                    <span className="lp-cta-icon" aria-hidden="true"><ArrowUpRight size={16} strokeWidth={1.5} /></span>
                  </Link>
                  <Link className="lp-cta-ghost" href="/login">
                    Sign in
                    <span className="lp-cta-icon" aria-hidden="true"><ArrowUpRight size={15} strokeWidth={1.5} /></span>
                  </Link>
                </div>
                <p className="lp-hero-note">
                  <MapPin size={14} strokeWidth={1.25} />
                  Built for the Indian market — postings outside India never reach your board.
                </p>
              </Reveal>

              <Reveal delay={140}><HeroBoard /></Reveal>
            </div>
          </div>
        </section>

        {/* ── The before-state ──────────────────────────────────────────── */}
        <section className="lp-section-tight">
          <div className="lp-wrap">
            <div className="lp-split">
              <Reveal>
                <span className="lp-eyebrow lp-eyebrow-plain">The problem</span>
                <h2 className="lp-h2">The search is not hard. Keeping track of it is.</h2>
                <p className="lp-body">
                  Nothing in a job search is individually difficult. What exhausts people is that it lives
                  in nine places at once, and no single one of them can tell you what today should look like.
                </p>
              </Reveal>

              <Reveal delay={120}>
                <div className="lp-ledger">
                  <div className="lp-ledger-row"><span>01</span><p>Four job boards, each with its own saved list you forget to check.</p></div>
                  <div className="lp-ledger-row"><span>02</span><p>A spreadsheet that is accurate until the week you are busiest.</p></div>
                  <div className="lp-ledger-row"><span>03</span><p>A resume you rewrite per role and lose track of which version went where.</p></div>
                  <div className="lp-ledger-row"><span>04</span><p>Interview practice with no record of whether you are actually improving.</p></div>
                  <div className="lp-ledger-row"><span>05</span><p>An application from three weeks ago that quietly went cold.</p></div>
                </div>
              </Reveal>
            </div>
          </div>
        </section>

        {/* ── How it works ─────────────────────────────────────────────── */}
        <section className="lp-section" id="how">
          <div className="lp-wrap">
            <Reveal>
              <span className="lp-eyebrow lp-eyebrow-plain">How it works</span>
              <h2 className="lp-h2">Three steps, then a board every morning.</h2>
            </Reveal>

            <div className="lp-steps">
              {STEPS.map((step, i) => (
                <Reveal className="lp-step" key={step.n} delay={i * 110}>
                  <span className="lp-step-num">{step.n}</span>
                  <h3>{step.title}</h3>
                  <p>{step.body}</p>
                </Reveal>
              ))}
            </div>
          </div>
        </section>

        {/* ── Features: asymmetric bento, each cell shaped to its surface ── */}
        <section className="lp-section" id="features">
          <div className="lp-wrap">
            <Reveal>
              <span className="lp-eyebrow lp-eyebrow-plain">What is inside</span>
              <h2 className="lp-h2">Six surfaces. Each one does a genuinely different job.</h2>
              <p className="lp-lead">
                They are not six views of the same list. Searching, tracking, practising, writing,
                researching and asking are different kinds of work, and each surface is built to look
                like the thing it actually is.
              </p>
            </Reveal>

            <div className="lp-bento">
              {/* Find roles — wide, with its result rows alongside */}
              <Reveal className="lp-cell-wide">
                <div className="lp-bezel">
                  <div className="lp-bezel-core lp-feature-split">
                    <div className="lp-feature">
                      <span className="lp-feature-icon" aria-hidden="true"><Search size={18} strokeWidth={1.25} /></span>
                      <h3>Find roles</h3>
                      <p>
                        Ask in plain language — &ldquo;senior backend roles in Bengaluru that use Go&rdquo; — and
                        get matches by meaning, not keyword overlap. Every posting has already been parsed into
                        structured signals, so the filters actually mean something.
                      </p>
                      <ul className="lp-feature-facts">
                        <li><Check size={13} strokeWidth={1.5} />Semantic search across every ingested posting</li>
                        <li><Check size={13} strokeWidth={1.5} />Scored against your profile, skills and experience band</li>
                        <li><Check size={13} strokeWidth={1.5} />Deduplicated, so one role appears once</li>
                      </ul>
                    </div>
                    <SearchMock />
                  </div>
                </div>
              </Reveal>

              {/* Applications — narrow pipeline column */}
              <Reveal className="lp-cell" delay={90}>
                <div className="lp-bezel">
                  <div className="lp-bezel-core lp-feature">
                    <span className="lp-feature-icon" aria-hidden="true"><Compass size={18} strokeWidth={1.25} /></span>
                    <h3>Applications</h3>
                    <p>
                      Move roles from saved through applied, screening, interview and offer. The board
                      notices when something stops moving and puts it back in front of you before it
                      is too late to follow up.
                    </p>
                    <div className="lp-feature-mock"><PipelineMock /></div>
                  </div>
                </div>
              </Reveal>

              {/* Interview Lab — half */}
              <Reveal className="lp-cell-half">
                <div className="lp-bezel">
                  <div className="lp-bezel-core lp-feature">
                    <span className="lp-feature-icon" aria-hidden="true"><Mic size={18} strokeWidth={1.25} /></span>
                    <h3>Interview Lab</h3>
                    <p>
                      Full mock interviews that ask real follow-ups instead of reading from a list. Answer
                      out loud or type — both run the same interview and are scored the same way, across
                      structure, depth, communication and trade-offs, per track.
                    </p>
                    <ul className="lp-feature-facts">
                      <li><Check size={13} strokeWidth={1.5} />Voice-first, with typed answers as a full equal</li>
                      <li><Check size={13} strokeWidth={1.5} />Scored per dimension so you know what to fix</li>
                      <li><Check size={13} strokeWidth={1.5} />Tracked over sessions, so improvement is visible</li>
                    </ul>
                    <div className="lp-feature-mock"><InterviewMock /></div>
                  </div>
                </div>
              </Reveal>

              {/* Resume Studio — half */}
              <Reveal className="lp-cell-half" delay={90}>
                <div className="lp-bezel">
                  <div className="lp-bezel-core lp-feature">
                    <span className="lp-feature-icon" aria-hidden="true"><FileText size={18} strokeWidth={1.25} /></span>
                    <h3>Resume Studio</h3>
                    <p>
                      Edit your resume section by section and watch a real typeset PDF compile as you go —
                      genuine LaTeX, not an HTML approximation. Point it at a specific posting and it scores
                      the fit and names what is missing while you type.
                    </p>
                    <ul className="lp-feature-facts">
                      <li><Check size={13} strokeWidth={1.5} />Structured editor, real LaTeX, real PDF output</li>
                      <li><Check size={13} strokeWidth={1.5} />ATS fit recomputed on every keystroke</li>
                      <li><Check size={13} strokeWidth={1.5} />Tailored per role, without starting over each time</li>
                    </ul>
                    <div className="lp-feature-mock"><ResumeMock /></div>
                  </div>
                </div>
              </Reveal>

              {/* Company Intel — narrow */}
              <Reveal className="lp-cell">
                <div className="lp-bezel">
                  <div className="lp-bezel-core lp-feature">
                    <span className="lp-feature-icon" aria-hidden="true"><Building2 size={18} strokeWidth={1.25} /></span>
                    <h3>Company Intel</h3>
                    <p>
                      What a company actually builds with, from its real public GitHub activity — plus the
                      contacts you have found, each labelled with where it came from and whether it is
                      verified. Nothing here is invented.
                    </p>
                    <div className="lp-feature-mock"><IntelMock /></div>
                  </div>
                </div>
              </Reveal>

              {/* Career Copilot — wide, chat alongside */}
              <Reveal className="lp-cell-wide" delay={90}>
                <div className="lp-bezel">
                  <div className="lp-bezel-core lp-feature-split">
                    <div className="lp-feature">
                      <span className="lp-feature-icon" aria-hidden="true"><MessageSquareText size={18} strokeWidth={1.25} /></span>
                      <h3>Career Copilot</h3>
                      <p>
                        Ask questions about your own search and get answers drawn from your records — your
                        tracker, your interview history, your saved roles, your profile. Not advice about
                        job searching in general; answers about yours.
                      </p>
                      <ul className="lp-feature-facts">
                        <li><Check size={13} strokeWidth={1.5} />Grounded in your data, with the records it used</li>
                        <li><Check size={13} strokeWidth={1.5} />Remembers context across the whole search</li>
                        <li><Check size={13} strokeWidth={1.5} />Writes the daily briefing that opens your board</li>
                      </ul>
                    </div>
                    <CopilotMock />
                  </div>
                </div>
              </Reveal>
            </div>
          </div>
        </section>

        {/* ── Trust: the honest substitute for a testimonial wall ────────── */}
        <section className="lp-section" id="trust">
          <div className="lp-wrap">
            <Reveal>
              <span className="lp-eyebrow lp-eyebrow-plain">How it stays honest</span>
              <h2 className="lp-h2">The parts most tools quietly fake.</h2>
              <p className="lp-lead">
                TalentRadar is new and has no case studies to show you, so here is the thing worth
                judging it on instead: what it refuses to make up.
              </p>
            </Reveal>

            <div className="lp-trust-grid">
              <Reveal className="lp-trust-item">
                <h3><Database size={17} strokeWidth={1.25} />The briefing is computed, not guessed</h3>
                <p>
                  &ldquo;This application has been quiet for nine days&rdquo; is arithmetic on a date you
                  entered, not a language model&rsquo;s impression. The deterministic parts of your board —
                  stale applications, score movement, recurring skill gaps — are calculated from your records
                  before any model is involved.
                </p>
              </Reveal>

              <Reveal className="lp-trust-item" delay={80}>
                <h3><ShieldCheck size={17} strokeWidth={1.25} />Contacts are never invented</h3>
                <p>
                  Plenty of tools will guess that someone&rsquo;s address follows first.last@company.com.
                  TalentRadar does not. It shows genuinely published contacts and ones you added yourself,
                  and labels every one by origin and verification state, so you always know what you are
                  looking at.
                </p>
              </Reveal>

              <Reveal className="lp-trust-item">
                <h3><MapPin size={17} strokeWidth={1.25} />India-only, and honest about it</h3>
                <p>
                  Postings that do not resolve to an Indian location are dropped during ingestion rather
                  than shown with a disclaimer. Narrow scope is why the compensation ranges, locations and
                  seniority bands can be specific instead of vaguely global.
                </p>
              </Reveal>

              <Reveal className="lp-trust-item" delay={80}>
                <h3><Plus size={17} strokeWidth={1.25} />Empty states stay empty</h3>
                <p>
                  When there is no data for a company, or nothing needs your attention today, the board says
                  so. It does not dress a blank surface in placeholder cards to look busier than it is —
                  including every example board on this page, which is labelled as one.
                </p>
              </Reveal>
            </div>
          </div>
        </section>

        {/* ── FAQ ──────────────────────────────────────────────────────── */}
        <section className="lp-section-tight" id="faq">
          <div className="lp-wrap">
            <Reveal>
              <span className="lp-eyebrow lp-eyebrow-plain">Questions</span>
              <h2 className="lp-h2">Before you sign up.</h2>
            </Reveal>

            <Reveal className="lp-faq" delay={80}>
              {FAQS.map(faq => (
                <details key={faq.q}>
                  <summary>
                    {faq.q}
                    <span className="lp-faq-mark" aria-hidden="true"><Plus size={14} strokeWidth={1.5} /></span>
                  </summary>
                  <p>{faq.a}</p>
                </details>
              ))}
            </Reveal>
          </div>
        </section>

        {/* ── Closer ───────────────────────────────────────────────────── */}
        <section className="lp-section-tight">
          <div className="lp-wrap">
            <Reveal>
              <div className="lp-bezel">
                <div className="lp-bezel-core lp-closer">
                  <span className="lp-eyebrow">
                    <i className="lp-eyebrow-dot" aria-hidden="true" />
                    Free while it is early
                  </span>
                  <h2 className="lp-h2">Open tomorrow morning to a board that already knows what matters.</h2>
                  <p className="lp-lead">
                    Onboarding takes about two minutes, and you can skip any part of it. Bring a resume if
                    you have one — the board works without it either way.
                  </p>
                  <div className="lp-cta-row">
                    <Link className="lp-cta" href="/signup">
                      Create your account
                      <span className="lp-cta-icon" aria-hidden="true"><ArrowUpRight size={16} strokeWidth={1.5} /></span>
                    </Link>
                    <Link className="lp-cta-ghost" href="/login">
                      I already have one
                      <span className="lp-cta-icon" aria-hidden="true"><ArrowUpRight size={15} strokeWidth={1.5} /></span>
                    </Link>
                  </div>
                </div>
              </div>
            </Reveal>
          </div>
        </section>
      </main>

      <footer className="lp-footer">
        <div className="lp-wrap lp-footer-inner">
          <Link className="lp-nav-brand" href="/">
            <span className="brand-mark" aria-hidden="true">R</span>
            <span>Talent<span>Radar</span></span>
          </Link>
          <div className="lp-footer-links">
            <a href="#how">How it works</a>
            <a href="#features">Features</a>
            <a href="#trust">How it stays honest</a>
            <a href="#faq">Questions</a>
            <Link href="/login">Sign in</Link>
          </div>
          <span className="lp-mono-meta">Career intelligence · India</span>
        </div>
      </footer>
    </div>
  )
}
