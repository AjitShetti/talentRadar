import type { Metadata } from 'next'
import Link from 'next/link'
import BrandLockup from '@/components/BrandLockup'
import LandingMotion from '@/components/landing/LandingMotion'
import LandingNav from '@/components/landing/LandingNav'
import PrismGround from '@/components/landing/PrismGround'
import Reveal from '@/components/landing/Reveal'
import SignedInRedirect from '@/components/landing/SignedInRedirect'
import { HeroCards, ValueTiles } from '@/components/landing/BoardMock'
import './landing.css'

/*
  THESIS: one black ground from top to bottom, with the prism at both ends: a
  visitor starts on it, and is sent off on it. Under the hero its ribs trail off
  into the page, and a few keep falling behind the text in between.
  STORY: hero, social proof, problem, solution, how it works, FAQ, CTA. Short
  lines throughout; nobody reads a long landing page.
  HONESTY: no testimonials, no user counts, no pricing, no logos. The product has
  no quotes to show yet and its public role list is too small to count, so the
  proof section names some of the public sources it reads and says they are not partners.
  Real quotes go there once they exist. Every sample figure on this page sits
  inside something labelled as an example, and the hero's stats are the product's
  own limits, not usage claims.
*/

const TITLE = 'TalentRadar — Apply, connect, practice and compete.'
const DESCRIPTION = 'Your entire job application, from applying to practising, in one place. Roles in India only.'

export const metadata: Metadata = {
  title: TITLE,
  description: DESCRIPTION,
  keywords: ['job search India', 'tech jobs India', 'application tracker', 'mock interview practice', 'ATS resume', 'career copilot'],
  openGraph: {
    title: TITLE,
    description: DESCRIPTION,
    type: 'website',
    siteName: 'TalentRadar',
  },
  twitter: {
    card: 'summary_large_image',
    title: TITLE,
    description: DESCRIPTION,
  },
}

const SOURCES = [
  'Greenhouse', 'Lever', 'Ashby', 'GitHub', 'Wikidata', 'OpenStreetMap', 'Luma', 'Google Developer Groups',
]

const PROOFS = [
  { title: 'Checked with the employer', body: 'Roles on an employer’s own job board are re-checked, so you can see when one has closed.' },
  { title: 'Nothing goes out for you', body: 'It writes the draft. You copy it and send it yourself.' },
  { title: 'Guesses say they are guesses', body: 'A worked-out email reads “likely, unverified”. Every sample on this page says “example”.' },
]

const PAINS = [
  { label: 'Where to apply', body: 'Job boards full of roles that may already be gone.' },
  { label: 'Who to contact', body: 'No idea who to write to, or what to say.' },
  { label: 'What to practise', body: 'Practice with no record of whether you are improving.' },
  { label: 'What comes next', body: 'An application from three weeks ago quietly goes cold.' },
]

const STEPS = [
  { n: '1', title: 'Tell it your goal', body: 'The role you want and where you are now. You can skip this and come back later.' },
  { n: '2', title: 'It does the looking', body: 'It reads job boards for roles in India, and re-checks the ones on employers’ own boards.' },
  { n: '3', title: 'Open today’s plan', body: 'What to apply for, who to write to and what to practise, in one place.' },
]

const FAQS = [
  { q: 'Is it only for jobs in India?', a: 'Yes. Roles known to be outside India are dropped.' },
  { q: 'Where do the jobs come from?', a: 'Employers’ own job boards on Greenhouse, Lever and Ashby, plus other job boards and search. The same role shows up once.' },
  { q: 'Does it message people for me?', a: 'No. It writes the draft, and you copy it and send it yourself. “I sent it” only records that you did.' },
  { q: 'Will it guess email addresses?', a: 'It shows addresses that were published. If it works one out from a company’s format, it says “likely, unverified” and never stores it.' },
  { q: 'How are take-homes judged?', a: 'An AI reads your GitHub repo and write-up, and weighs your process (70%) above the finished product (30%). You appear under a pseudonym unless you choose a name.' },
  { q: 'Do I need a microphone for mock interviews?', a: 'No. You can answer by voice or by typing.' },
  { q: 'What does it cost?', a: 'Nothing today, and there is no pricing yet. You will be told before that changes.' },
]

function Kicker({ children }: { children: React.ReactNode }) {
  return <p className="lp-kicker"><i aria-hidden="true" />{children}</p>
}

export default function LandingPage() {
  return (
    <div className="lp">
      <SignedInRedirect />
      <noscript><style>{'.lp-reveal{opacity:1;transform:none;filter:none}.lp-motion{display:none}.lp-proof-names li::after{animation:none}'}</style></noscript>

      <LandingMotion />
      <LandingNav />

      <main>
        {/* ── Hero: one screen on the prism, whatever the window's size. The
            cards run on past that screen, over the tail where the ribs fade. ── */}
        <div className="lp-prismed lp-hero-wrap">
          <PrismGround />
          <section className="lp-hero">
            <div className="lp-hero-stage">
              <h1 className="lp-h1">Apply, connect, practice&nbsp;and <em>compete</em>.</h1>

              <div className="lp-hero-mid">
                <p className="lp-hero-sub">
                  Your entire job application, from applying to practising, in one place. Roles in India only.
                </p>

                <dl className="lp-stats lp-stats-l">
                  <div className="lp-stat"><dt>Employer job feeds checked</dt><dd>3</dd></div>
                  <div className="lp-stat"><dt>Companies a day, at most</dt><dd>5</dd></div>
                </dl>

                <div className="lp-try-slot">
                  <Link className="lp-try" href="/signup">
                    <BrandLockup size={22} />
                    Try now
                  </Link>
                </div>

                <dl className="lp-stats lp-stats-r">
                  <div className="lp-stat"><dt>Practice question a day</dt><dd>1</dd></div>
                  <div className="lp-stat"><dt>Weekly ranked take-home</dt><dd>24/48h</dd></div>
                </dl>
              </div>

              <HeroCards />
            </div>
          </section>
          <div className="lp-hero-tail" aria-hidden="true" />
        </div>

        {/* ── Social proof: sources anyone can check, until there are quotes ── */}
        <section className="lp-section">
          <div className="lp-wrap">
            <Reveal>
              <Kicker>Where it comes from</Kicker>
              <h2 className="lp-h2">Built on sources you can <em>check</em>.</h2>
            </Reveal>

            <Reveal delay={80}>
              <ul className="lp-proof-names">
                {SOURCES.map((source, i) => (
                  <li key={source} style={{ '--i': i } as React.CSSProperties}>{source}</li>
                ))}
              </ul>
              <p className="lp-proof-note">Some of the public sources it reads. None of them is a partner or endorses TalentRadar.</p>
            </Reveal>

            <div className="lp-proof-facts" data-progress>
              {PROOFS.map((proof, i) => (
                <Reveal className="lp-proof-fact" key={proof.title} delay={i * 80}>
                  <h3>{proof.title}</h3>
                  <p>{proof.body}</p>
                </Reveal>
              ))}
            </div>
          </div>
        </section>

        {/* ── Problem ───────────────────────────────────────────────────── */}
        <section className="lp-section">
          <div className="lp-wrap lp-split">
            <Reveal>
              <Kicker>The problem</Kicker>
              <h2 className="lp-h2">A job search should not need <em>this many tabs</em>.</h2>
              <p className="lp-lead">Nothing here is hard on its own. Keeping it all straight is.</p>
            </Reveal>

            <Reveal delay={100}>
              <dl className="lp-pains" data-progress>
                {PAINS.map(pain => (
                  <div className="lp-pain" key={pain.label}>
                    <dt>{pain.label}</dt>
                    <dd>{pain.body}</dd>
                  </div>
                ))}
              </dl>
            </Reveal>
          </div>
        </section>

        {/* ── Solution: the headline's four verbs, each with its surface ───── */}
        <section className="lp-section" id="value">
          <div className="lp-wrap">
            <Reveal>
              <Kicker>What you get</Kicker>
              <h2 className="lp-h2">One place that already knows <em>what is next</em>.</h2>
            </Reveal>

            <ValueTiles />

            <Reveal>
              <p className="lp-also">Also inside: Resume Studio, a map of Bengaluru tech companies, Bengaluru tech events and a career copilot.</p>
            </Reveal>
          </div>
        </section>

        {/* ── How it works ─────────────────────────────────────────────── */}
        <section className="lp-section" id="how">
          <div className="lp-wrap">
            <Reveal>
              <Kicker>How it works</Kicker>
              <h2 className="lp-h2">Three steps, then a plan <em>every morning</em>.</h2>
            </Reveal>

            <div className="lp-steps" data-progress>
              {STEPS.map((step, i) => (
                <Reveal className="lp-step" key={step.n} delay={i * 100}>
                  <span className="lp-step-num" aria-hidden="true">{step.n}</span>
                  <h3>{step.title}</h3>
                  <p>{step.body}</p>
                </Reveal>
              ))}
            </div>
          </div>
        </section>

        {/* ── FAQ ──────────────────────────────────────────────────────── */}
        <section className="lp-section" id="faq">
          <div className="lp-wrap lp-faq-grid">
            <Reveal>
              <Kicker>Questions</Kicker>
              <h2 className="lp-h2">Before you <em>sign up</em>.</h2>
            </Reveal>

            <Reveal className="lp-faq" delay={80}>
              {FAQS.map(faq => (
                <details key={faq.q}>
                  <summary>
                    {faq.q}
                    <span className="lp-faq-mark" aria-hidden="true">+</span>
                  </summary>
                  <p>{faq.a}</p>
                </details>
              ))}
            </Reveal>
          </div>
        </section>

        {/* ── CTA: back on the prism ───────────────────────────────────── */}
        <section className="lp-prismed lp-closer">
          <PrismGround />
          <h2 className="lp-h2">Stop juggling. Start <em>today</em>.</h2>
          <p className="lp-closer-sub">Free while it is early.</p>
          <div className="lp-try-slot">
            <Link className="lp-try" href="/signup">
              <BrandLockup size={22} />
              Try now
            </Link>
          </div>
          <Link className="lp-closer-signin" href="/login">I already have an account</Link>
        </section>
      </main>

      <footer className="lp-footer">
        <div className="lp-wrap lp-footer-inner">
          <Link className="lp-nav-brand" href="/">
            <BrandLockup />
          </Link>
          <div className="lp-footer-links">
            <a href="#value">What you get</a>
            <a href="#how">How it works</a>
            <a href="#faq">Questions</a>
            <Link href="/login">Sign in</Link>
          </div>
          <span className="lp-footer-meta">Career intelligence · India</span>
        </div>
      </footer>
    </div>
  )
}
