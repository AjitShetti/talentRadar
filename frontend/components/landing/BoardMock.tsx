import { FileCheck2, Github, Quote } from 'lucide-react'
import FlapText from '@/components/FlapText'

/**
 * Composed illustrations of the real surfaces, built from the same tokens and
 * row vocabulary as the app itself — not screenshots.
 *
 * Deliberately composed rather than captured: a screenshot of the builder's
 * own workspace would leak real applications and contacts, and would rot the
 * moment a surface changed. Every board below is labelled as an example so a
 * visitor never reads sample figures as a claim about real usage.
 */

function Chrome({ label }: { label: string }) {
  return (
    <div className="lp-mock-chrome">
      <div className="lp-mock-dots" aria-hidden="true"><i /><i /><i /></div>
      <span className="lp-mock-tag">{label}</span>
    </div>
  )
}

/** The hero board: the Overview surface, reduced to its ledger and top rows. */
export function HeroBoard() {
  return (
    <div className="lp-bezel">
      <div className="lp-bezel-core lp-mock">
        <Chrome label="Example board · sample data" />
        <div className="lp-mock-body">
          <div className="lp-mock-counters">
            <div className="lp-mock-counter">
              <span>Matched</span>
              <strong><FlapText value={42} /></strong>
            </div>
            <div className="lp-mock-counter">
              <span>In flight</span>
              <strong><FlapText value={11} /></strong>
            </div>
            <div className="lp-mock-counter is-lit">
              <span>Needs you</span>
              <strong><FlapText value={3} /></strong>
            </div>
            <div className="lp-mock-counter">
              <span>Last score</span>
              <strong><FlapText value={74} /></strong>
            </div>
          </div>

          <div className="lp-mock-row">
            <i className="lp-mock-lamp" aria-hidden="true" />
            <div className="lp-mock-row-main">
              <div>
                <span className="lp-mock-kind">Do this first</span>
                <span className="lp-mock-head">Follow up with Razorpay</span>
              </div>
              <p className="lp-mock-sub">Screening call was 9 days ago and the thread has gone quiet. A short nudge today is the highest-value thing on the board.</p>
            </div>
            <span className="lp-mock-time">09:12</span>
          </div>

          <div className="lp-mock-row">
            <i className="lp-mock-lamp tone-warn" aria-hidden="true" />
            <div className="lp-mock-row-main">
              <div>
                <span className="lp-mock-kind tone-warn">Going cold</span>
                <span className="lp-mock-head">2 applications past 14 days</span>
              </div>
              <p className="lp-mock-sub">Zerodha and Postman have had no movement since you applied.</p>
            </div>
            <span className="lp-mock-time">09:12</span>
          </div>

          <div className="lp-mock-row">
            <i className="lp-mock-lamp tone-good" aria-hidden="true" />
            <div className="lp-mock-row-main">
              <div>
                <span className="lp-mock-kind tone-good">Momentum</span>
                <span className="lp-mock-head">System design is up 8 points</span>
              </div>
              <p className="lp-mock-sub">Your weakest track is still behavioural — one session would even it out.</p>
            </div>
            <span className="lp-mock-time">08:40</span>
          </div>
        </div>
      </div>
    </div>
  )
}

export function SearchMock() {
  const rows = [
    { mark: 'RP', title: 'Senior Backend Engineer', meta: 'Bengaluru · Hybrid · ₹38–52L', score: '94' },
    { mark: 'ZR', title: 'Platform Engineer, Payments', meta: 'Remote (India) · ₹32–46L', score: '88' },
    { mark: 'PM', title: 'Staff Engineer, Infrastructure', meta: 'Pune · On-site · ₹45–60L', score: '81' },
  ]
  return (
    <div className="lp-bezel lp-bezel-sm">
      <div className="lp-bezel-core">
        <Chrome label="Find roles · example" />
        <div className="lp-mini">
          {rows.map(row => (
            <div className="lp-mini-row" key={row.title}>
              <span className="lp-mini-mark" aria-hidden="true">{row.mark}</span>
              <span className="lp-mini-main">
                <strong>{row.title}</strong>
                <span>{row.meta}</span>
              </span>
              <span className="lp-mini-pill">{row.score}% fit</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

export function PipelineMock() {
  const stages = [
    { label: 'Saved', count: '14', on: false },
    { label: 'Applied', count: '9', on: false },
    { label: 'Screening', count: '4', on: true },
    { label: 'Interview', count: '2', on: false },
    { label: 'Offer', count: '0', on: false },
  ]
  return (
    <div className="lp-bezel lp-bezel-sm">
      <div className="lp-bezel-core">
        <Chrome label="Applications · example" />
        <div className="lp-mini">
          <div className="lp-mini-stack">
            {stages.map(stage => (
              <div className={`lp-mini-stage ${stage.on ? 'is-on' : ''}`} key={stage.label}>
                <i aria-hidden="true" />
                <strong>{stage.label}</strong>
                <em>{stage.count}</em>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}

export function InterviewMock() {
  const dims = [
    { label: 'Structure', score: 82, tone: '' },
    { label: 'Depth', score: 76, tone: '' },
    { label: 'Communication', score: 58, tone: 'tone-weak' },
    { label: 'Trade-offs', score: 88, tone: 'tone-good' },
  ]
  return (
    <div className="lp-bezel lp-bezel-sm">
      <div className="lp-bezel-core">
        <Chrome label="Interview Lab · example" />
        <div className="lp-mini">
          <div className="lp-mini-bars">
            {dims.map(dim => (
              <div key={dim.label}>
                <div className="lp-mini-bar-head"><span>{dim.label}</span><b>{dim.score}</b></div>
                <div className="lp-mini-track">
                  <div className={`lp-mini-fill ${dim.tone}`} style={{ width: `${dim.score}%` }} />
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}

export function ResumeMock() {
  return (
    <div className="lp-bezel lp-bezel-sm">
      <div className="lp-bezel-core">
        <Chrome label="Resume Studio · example" />
        <div className="lp-mini">
          <div className="lp-mini-row">
            <span className="lp-mini-mark" aria-hidden="true"><FileCheck2 size={13} strokeWidth={1.25} /></span>
            <span className="lp-mini-main">
              <strong>ATS fit against this posting</strong>
              <span>Recompiled on every keystroke</span>
            </span>
            <span className="lp-mini-pill tone-good">86</span>
          </div>
          <div className="lp-mini-row">
            <span className="lp-mini-mark" aria-hidden="true">1</span>
            <span className="lp-mini-main">
              <strong>Add &ldquo;Kafka&rdquo; to your platform bullet</strong>
              <span>Named 4 times in the job description</span>
            </span>
            <span className="lp-mini-pill tone-flat">+6</span>
          </div>
          <div className="lp-mini-row">
            <span className="lp-mini-mark" aria-hidden="true">2</span>
            <span className="lp-mini-main">
              <strong>Quantify the migration bullet</strong>
              <span>No measurable outcome detected</span>
            </span>
            <span className="lp-mini-pill tone-flat">+4</span>
          </div>
        </div>
      </div>
    </div>
  )
}

export function IntelMock() {
  return (
    <div className="lp-bezel lp-bezel-sm">
      <div className="lp-bezel-core">
        <Chrome label="Company Intel · example" />
        <div className="lp-mini">
          <div className="lp-mini-row">
            <span className="lp-mini-mark" aria-hidden="true"><Github size={13} strokeWidth={1.25} /></span>
            <span className="lp-mini-main">
              <strong>Public repositories</strong>
              <span>Go, TypeScript, Kubernetes</span>
            </span>
            <span className="lp-mini-pill tone-flat">142</span>
          </div>
          <div className="lp-mini-row">
            <span className="lp-mini-mark" aria-hidden="true">JD</span>
            <span className="lp-mini-main">
              <strong>Engineering Manager</strong>
              <span>Published on the company blog</span>
            </span>
            <span className="lp-mini-pill tone-good">Verified</span>
          </div>
          <div className="lp-mini-row">
            <span className="lp-mini-mark" aria-hidden="true">AK</span>
            <span className="lp-mini-main">
              <strong>Staff Engineer</strong>
              <span>Added by you · no email on file</span>
            </span>
            <span className="lp-mini-pill tone-flat">Unverified</span>
          </div>
        </div>
      </div>
    </div>
  )
}

export function CopilotMock() {
  return (
    <div className="lp-bezel lp-bezel-sm">
      <div className="lp-bezel-core">
        <Chrome label="Career Copilot · example" />
        <div className="lp-mini">
          <div className="lp-mini-chat">
            <div className="lp-mini-ask">Which of my applications should I chase this week?</div>
            <div className="lp-mini-say">
              Three are worth a nudge. Razorpay is 9 days past its screening call, and Zerodha and Postman
              have had no movement since you applied on the 14th. Start with Razorpay — it is the only one
              where someone has already spoken to you.
              <span className="lp-mini-cite"><Quote size={10} strokeWidth={1.25} /> From your tracker · 3 records</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
