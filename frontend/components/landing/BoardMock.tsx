import Reveal from './Reveal'

/**
 * Composed illustrations of the real surfaces, on their own paper — not
 * screenshots.
 *
 * Deliberately composed rather than captured: a screenshot of the builder's
 * own workspace would leak real applications and contacts, and would rot the
 * moment a surface changed. Every card below is labelled as an example so a
 * visitor never reads sample figures as a claim about real usage.
 */

function HeroCard({ position, tone = '', label, note, children }: {
  position: 'left' | 'center' | 'right'
  tone?: '' | 'is-warm' | 'is-night'
  label: string
  note: string
  children: React.ReactNode
}) {
  // The card is focusable so a keyboard or a tap reaches what a hover reveals.
  // The outer box stays put and only the inner one rises, so the pointer never
  // falls off a card that has moved out from under it.
  return (
    <article className={`lp-fcard is-${position}`} tabIndex={0} aria-label={`${label}, example`}>
      <div className="lp-fcard-lift">
        <p className="lp-fcard-tag">
          <i aria-hidden="true" />
          {/* Too narrow for the side cards' own tags: the one in front speaks for all three. */}
          {position === 'center' && <span className="lp-fcard-tag-all">All three are examples</span>}
          <span className={position === 'center' ? 'lp-fcard-tag-one' : undefined}>{label} · example</span>
        </p>
        <div className="lp-fcard-frame">
          <div className={`lp-fcard-core ${tone}`.trim()}>
            {children}
            <p className="lp-fcard-note">{note}</p>
          </div>
        </div>
      </div>
    </article>
  )
}

function HeroCardHead({ title, chip }: { title: string; chip: string }) {
  return (
    <div className="lp-fc-head">
      <span className="lp-fc-title">{title}</span>
      <span className="lp-fc-chip">{chip}</span>
    </div>
  )
}

function HeroCardRow({ mark, title, meta, chip, azure = false }: {
  mark?: string
  title: string
  meta: string
  chip?: string
  azure?: boolean
}) {
  return (
    <div className="lp-fc-row">
      {mark && <span className="lp-fc-mark" aria-hidden="true">{mark}</span>}
      <span className="lp-fc-main">
        <strong>{title}</strong>
        <span>{meta}</span>
      </span>
      {chip && <span className={`lp-fc-chip ${azure ? 'is-azure' : ''}`.trim()}>{chip}</span>}
    </div>
  )
}

/**
 * The hero's three cards, one surface each, running on past the first screen
 * until a hover or focus pops one up whole inside it.
 */
export function HeroCards() {
  return (
    <div className="lp-fcards">
      <HeroCard position="left" label="Apply" note="See whether a role is still listed before you apply.">
        <HeroCardHead title="Roles worth applying to" chip="Re-checked" />
        <HeroCardRow mark="RP" title="Senior Backend Engineer" meta="Razorpay · still listed · Bengaluru" chip="94% fit" azure />
        <HeroCardRow mark="ZR" title="Platform Engineer, Payments" meta="Zerodha · still listed · Remote" chip="88% fit" azure />
        <HeroCardRow mark="PM" title="Staff Engineer, Infrastructure" meta="Postman · still listed · Bengaluru" chip="81% fit" azure />
        <HeroCardRow mark="NL" title="Backend Engineer II" meta="Northstar Labs · re-listed twice · Chennai" chip="Re-listed" />
      </HeroCard>

      <HeroCard position="center" tone="is-warm" label="Connect" note="Up to five companies a day, with each message drafted for you.">
        <HeroCardHead title="Today’s outreach" chip="2 of 5 done" />
        <HeroCardRow mark="RP" title="Razorpay" meta="Engineering Manager · Founder" chip="Ready" azure />
        <div className="lp-fc-actions" aria-hidden="true">
          <span className="lp-fc-act">Copy</span>
          <span className="lp-fc-act is-line">I sent it</span>
        </div>
        <HeroCardRow mark="PM" title="Postman" meta="Staff Engineer · Careers inbox" chip="Ready" azure />
        <HeroCardRow mark="ZR" title="Zerodha" meta="First follow-up is due today" chip="Follow up" azure />
        <HeroCardRow mark="GR" title="Groww" meta="Met at a Bengaluru meetup" chip="Start a thread" />
      </HeroCard>

      <HeroCard position="right" tone="is-night" label="Practice" note="A question a day, mock interviews, and a ranked take-home every week.">
        <HeroCardHead title="Mock interview score" chip="Last 7 sessions" />
        <div className="lp-fc-score"><b>74</b><span className="lp-fc-chip">Up 22 points</span></div>
        <svg className="lp-fc-chart" viewBox="0 0 320 104" role="img" aria-label="Example chart: interview score rising from 52 to 74 over seven sessions">
          <defs>
            <linearGradient id="lp-fc-fade" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0" stopOpacity=".42" />
              <stop offset="1" stopOpacity="0" />
            </linearGradient>
          </defs>
          <line className="lp-fc-grid" x1="22" x2="320" y1="34" y2="34" />
          <line className="lp-fc-grid" x1="22" x2="320" y1="74" y2="74" />
          <text x="0" y="37">75</text>
          <text x="0" y="77">50</text>
          <path d="M28 70.8 75 61.2 123 66 170 53.2 217 48.4 265 40.4 312 35.6V100H28Z" fill="url(#lp-fc-fade)" />
          <path className="lp-fc-line" d="M28 70.8 75 61.2 123 66 170 53.2 217 48.4 265 40.4 312 35.6" />
          <circle className="lp-fc-dot" cx="312" cy="35.6" r="4.5" />
        </svg>
        <HeroCardRow title="Longest Substring Without Repeating Characters" meta="Today’s question · Medium · Sliding window" />
        <HeroCardRow title="Last week’s take-home" meta="Rank 12 on the leaderboard" chip="72.0" azure />
      </HeroCard>
    </div>
  )
}

function ValueTile({ title, line, tone = '', delay = 0, children }: {
  title: string
  line: string
  tone?: '' | 'is-warm' | 'is-night'
  delay?: number
  children: React.ReactNode
}) {
  return (
    <Reveal className="lp-value-cell" delay={delay}>
      <article className="lp-value">
        <h3>{title}</h3>
        <p className="lp-value-line">{line}</p>
        <p className="lp-fcard-tag"><i aria-hidden="true" />{title} · example</p>
        <div className="lp-fcard-frame">
          <div className={`lp-fcard-core ${tone}`.trim()}>{children}</div>
        </div>
      </article>
    </Reveal>
  )
}

/** The headline's four verbs, one tile each, with sample cards the hero does not repeat. */
export function ValueTiles() {
  return (
    <div className="lp-values">
      <ValueTile title="Apply" line="See which roles fit you, and whether they are still open.">
        <HeroCardHead title="Senior Backend Engineer" chip="94% fit" />
        <HeroCardRow mark="RP" title="Razorpay" meta="Bengaluru · Hybrid" chip="Still listed" azure />
        <HeroCardRow title="Checked with the employer’s job board" meta="2 days ago" />
        <HeroCardRow title="Skills to brush up" meta="Kafka, Kubernetes" />
      </ValueTile>

      <ValueTile title="Connect" line="Up to five companies a day, with each message drafted for you." tone="is-warm" delay={80}>
        <HeroCardHead title="Razorpay" chip="Ready" />
        <HeroCardRow mark="EM" title="Engineering Manager" meta="Found on the company’s site" />
        <p className="lp-fc-msg">Hi, I have built payment services in Go for four years and saw your team is hiring. Could I ask two questions about the role?</p>
        <div className="lp-fc-actions" aria-hidden="true">
          <span className="lp-fc-act">Copy</span>
          <span className="lp-fc-act is-line">I sent it</span>
        </div>
      </ValueTile>

      <ValueTile title="Practice" line="One question a day, and mock interviews that track your score." tone="is-night">
        <HeroCardHead title="Today’s question" chip="Medium" />
        <HeroCardRow title="Longest Substring Without Repeating Characters" meta="Sliding window · picked for your gaps" />
        <p className="lp-fc-score-label">Mock interview score</p>
        <div className="lp-fc-score"><b>74</b><span className="lp-fc-chip">Up 22 points</span></div>
      </ValueTile>

      <ValueTile title="Compete" line="A take-home every week, judged and ranked on a leaderboard." delay={80}>
        <HeroCardHead title="This week’s take-home" chip="48h" />
        <HeroCardRow mark="11" title="builder-7f3a" meta="Judged" chip="74.5" />
        <HeroCardRow mark="12" title="You" meta="Judged" chip="72.0" azure />
        <HeroCardRow mark="13" title="builder-c21e" meta="Judged" chip="70.0" />
      </ValueTile>
    </div>
  )
}
