import { PracticeHistory } from '@/lib/api'

const MAX_ROWS = 7
const words = (text: string) => text.replace(/[-_]/g, ' ')
const capitalise = (text: string) => text ? text[0].toUpperCase() + text.slice(1) : text
const topicName = (topic: string) => capitalise(words(topic))

/** Done days by topic, most practised first, with system design as one row. */
export default function TopicBars({ history }: { history: PracticeHistory }) {
  const top = history.topics.slice(0, MAX_ROWS)
  const design = history.solved.design
  const largest = Math.max(design, ...top.map(item => item.count), 1)
  const least = history.topics.length > 1 ? history.topics[history.topics.length - 1] : null

  return <section className="pr-card" aria-label="Solved by topic">
    <p className="pr-lbl">By topic</p>
    {top.length === 0 && design === 0
      ? <p className="muted-copy">Your topics appear here once you have solved a problem.</p>
      : <div className="pr-topics">
        {top.map(item => <div className="pr-topic" key={item.topic}>
          <span>{topicName(item.topic)}</span>
          <i><b style={{ width: `${(item.count / largest) * 100}%` }} /></i>
          <em>{item.count}</em>
        </div>)}
        {design > 0 && <div className="pr-topic" data-design="">
          <span>System design</span>
          <i><b style={{ width: `${(design / largest) * 100}%` }} /></i>
          <em>{design}</em>
        </div>}
      </div>}
    {least && <p className="pr-topic-note">Least practised: <b>{topicName(least.topic)}</b>.</p>}
  </section>
}
