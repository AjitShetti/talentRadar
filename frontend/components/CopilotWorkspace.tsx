'use client'

import Link from 'next/link'
import { FormEvent, useEffect, useRef, useState } from 'react'
import {
  ArrowRight, Brain, ExternalLink, Lightbulb, Loader2, MapPin,
  Plus, Send, Trash2,
} from 'lucide-react'
import { AgentMemory, api, ChatJob, LearningPlan, LearningTask } from '@/lib/api'
import { usePersistentState } from '@/lib/persistent-state'

type Turn = { role: 'user' | 'assistant'; content: string; jobs?: ChatJob[]; intent?: string }

const FALLBACK_STARTERS = [
  'What should I focus on today?',
  'Which of my applications have gone cold?',
  'What skills am I missing for my target roles?',
]

function JobResult({ job }: { job: ChatJob }) {
  return <div className="cp-job">
    <strong>{job.title}</strong>
    <span>{job.company}{job.location ? ` · ${job.location}` : ''}{job.is_remote ? ' · Remote' : ''}</span>
    {(job.skills?.length ?? 0) > 0 && <div className="chips">
      {job.skills!.slice(0, 5).map(skill => <span key={skill}>{skill}</span>)}
    </div>}
    <div className="cp-job-actions">
      <Link href="/search" className="text-button">Open in search <ArrowRight size={12}/></Link>
      {job.source_url && <a href={job.source_url} target="_blank" rel="noreferrer" className="text-button">
        Posting <ExternalLink size={12}/>
      </a>}
    </div>
  </div>
}

/** Resources come back as bare URLs; the host is the only readable part of one. */
function hostOf(url: string) {
  try {
    return new URL(url).hostname.replace(/^www\./, '')
  } catch {
    return url
  }
}

/** Priority is 1..5 with 1 most urgent; an unscored task sorts to the bottom. */
function planRank(task: LearningTask) {
  return typeof task.priority === 'number' ? task.priority : 99
}

function LearningPlanList({ plan, onClear }: { plan: LearningPlan; onClear: () => void }) {
  // A plan restored from a previous session may predate this shape.
  const tasks = [...(plan.tasks ?? [])].sort((a, b) => planRank(a) - planRank(b))
  if (tasks.length === 0) {
    return <p className="muted-copy">
      No gaps to close yet. Add your skills in Settings or run a few searches so
      your copilot has something to compare you against.
    </p>
  }
  return <div className="cp-plan">
    {tasks.map((task, index) => <article key={`${task.skill_name}-${index}`}>
      <div className="cp-plan-head">
        <span className="cp-plan-rank">{index + 1}</span>
        <div>
          <strong>{task.title}</strong>
          <span className="cp-plan-skill">{task.skill_name}</span>
        </div>
      </div>
      {task.description && <p>{task.description}</p>}
      {(task.resources?.length ?? 0) > 0 && <div className="cp-plan-links">
        {task.resources!.map(resource => resource.startsWith('http')
          ? <a key={resource} href={resource} target="_blank" rel="noreferrer">
              {hostOf(resource)} <ExternalLink size={11}/>
            </a>
          : <span key={resource}>{resource}</span>)}
      </div>}
    </article>)}
    <button className="text-button" onClick={onClear}>Clear plan</button>
  </div>
}

/**
 * The Career Copilot: a running thread, the memories that steer it, and the
 * learning plan it can generate.
 *
 * It lives on the overview page rather than behind its own nav item — the
 * briefing above it and the feedback panels below it are exactly the context
 * it answers about, so splitting them across two routes only made the user
 * carry that context between them. It stays a component so the /agent route
 * still renders the same thing for anyone holding a link to it.
 *
 * `acceptUrlQuestion` picks up a ?q= handoff; only the standalone route needs it.
 */
export default function CopilotWorkspace({ acceptUrlQuestion = false }: { acceptUrlQuestion?: boolean }) {
  const [memories, setMemories] = useState<AgentMemory[]>([])
  const [starters, setStarters] = useState<string[]>(FALLBACK_STARTERS)
  const [turns, setTurns, turnsHydrated] = usePersistentState<Turn[]>('agent.thread', [])
  const [draft, setDraft] = useState('')
  const [thinking, setThinking] = useState(false)
  const [newMemory, setNewMemory] = useState('')
  const [learning, setLearning] = usePersistentState<LearningPlan | null>('agent.learning', null)
  const [planning, setPlanning] = useState(false)
  const [error, setError] = useState('')
  const threadEnd = useRef<HTMLDivElement>(null)
  const askedFromUrl = useRef(false)

  async function loadMemories() {
    try {
      const stored = await api.agent.memories()
      // Dismissals live in the same table; they're page mechanics, not memories.
      setMemories(stored.memories.filter(m => m.memory_type !== 'card_dismissal'))
    } catch { /* the rail is optional — never block the thread on it */ }
  }

  useEffect(() => {
    loadMemories()
    api.agent.starters().then(r => setStarters(r.starters)).catch(() => undefined)
  }, [])

  // Wait for the stored thread to hydrate first, or the handed-over question
  // would replace the restored one.
  useEffect(() => {
    if (!acceptUrlQuestion || !turnsHydrated || askedFromUrl.current) return
    const question = new URLSearchParams(window.location.search).get('q')
    if (!question) return
    askedFromUrl.current = true
    // Drop it from the URL so a refresh doesn't ask the same thing again.
    window.history.replaceState(null, '', window.location.pathname)
    ask(question)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [acceptUrlQuestion, turnsHydrated])

  // Keep the newest turn in view as the conversation grows.
  useEffect(() => { threadEnd.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' }) }, [turns, thinking])

  async function ask(message: string) {
    const question = message.trim()
    if (!question || thinking) return
    const history = turns.map(t => ({ role: t.role, content: t.content }))
    setTurns([...turns, { role: 'user', content: question }])
    setDraft('')
    setThinking(true)
    setError('')
    try {
      const reply = await api.agent.chat(question, history)
      setTurns(current => [...current, {
        role: 'assistant',
        content: reply.reply,
        jobs: reply.jobs,
        intent: reply.intent,
      }])
      // A turn can teach the copilot something new — refresh the rail.
      loadMemories()
    } catch (err) {
      setTurns(current => [...current, {
        role: 'assistant',
        content: err instanceof Error ? err.message : 'Something went wrong on that one.',
      }])
    } finally {
      setThinking(false)
    }
  }

  async function addMemory(event: FormEvent) {
    event.preventDefault()
    const content = newMemory.trim()
    if (!content) return
    setNewMemory('')
    try {
      const saved = await api.agent.remember(content)
      setMemories(current => [saved, ...current])
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not save that.')
    }
  }

  async function forget(id: string) {
    setMemories(current => current.filter(m => m.id !== id))
    try {
      await api.agent.forget(id)
    } catch {
      loadMemories()
    }
  }

  async function generatePlan() {
    setPlanning(true)
    try {
      setLearning(await api.career.recommend())
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not create learning recommendations.')
    } finally {
      setPlanning(false)
    }
  }

  return <>
    {error && <p className="form-error">{error}</p>}

    <div className="cp-layout">
      <div className="cp-main">
        <section className="cp-chat">
          <div className="cp-section-head">
            <p className="eyebrow">ASK YOUR COPILOT</p>
            {turns.length > 0 && <button className="text-button" onClick={() => setTurns([])}>Clear thread</button>}
          </div>

          {turns.length === 0 && <div className="cp-starters">
            <p className="muted-copy">It can search roles, read your tracker, find skill gaps, and look up companies.</p>
            {starters.map(starter => <button key={starter} onClick={() => ask(starter)}>{starter}</button>)}
          </div>}

          <div className="cp-thread">
            {turns.map((turn, index) => <div key={index} className="cp-turn" data-role={turn.role}>
              <div className="cp-bubble">{turn.content}</div>
              {(turn.jobs?.length ?? 0) > 0 && <div className="cp-jobs">
                {turn.jobs!.map(job => <JobResult key={job.id} job={job}/>)}
              </div>}
            </div>)}
            {thinking && <div className="cp-turn" data-role="assistant">
              <div className="cp-bubble cp-thinking"><Loader2 size={13} className="spin"/> Thinking…</div>
            </div>}
            <div ref={threadEnd}/>
          </div>

          <form className="cp-composer" onSubmit={event => { event.preventDefault(); ask(draft) }}>
            <input
              value={draft}
              onChange={event => setDraft(event.target.value)}
              placeholder="Ask about your search, your applications, or a company…"
              aria-label="Message your career copilot"
            />
            <button className="primary-button" type="submit" disabled={thinking || !draft.trim()}>
              <Send size={14}/> Send
            </button>
          </form>
        </section>
      </div>

      <aside className="cp-rail">
        <section className="panel">
          <div className="panel-heading">
            <div>
              <p className="eyebrow">SAVED CONTEXT</p>
              <h2>What your copilot knows</h2>
            </div>
            <Brain size={17}/>
          </div>
          {memories.length > 0
            ? <div className="cp-memories">{memories.map(memory => <div key={memory.id}>
                <div>
                  <strong>{memory.memory_type.replace(/_/g, ' ')}</strong>
                  <p>{memory.content}</p>
                </div>
                <button className="icon-refresh" title="Forget this" onClick={() => forget(memory.id)}>
                  <Trash2 size={13}/>
                </button>
              </div>)}</div>
            : <p className="muted-copy">Nothing saved yet. Ask about a role or location and your copilot will start remembering what you&apos;re after.</p>}
          <form className="cp-teach" onSubmit={addMemory}>
            <input
              value={newMemory}
              onChange={event => setNewMemory(event.target.value)}
              placeholder="Teach it something — “I want to stay in Pune”"
              aria-label="Teach your copilot"
            />
            <button className="icon-refresh" type="submit" title="Save"><Plus size={15}/></button>
          </form>
        </section>

        <section className="panel">
          <div className="panel-heading">
            <div>
              <p className="eyebrow">LEARNING PLAN</p>
              <h2>Close your gaps</h2>
            </div>
            <MapPin size={17}/>
          </div>
          <p className="muted-copy">Turn the skills employers keep asking for into a concrete study plan.</p>
          <button className="outline-button full-width" onClick={generatePlan} disabled={planning}>
            {planning ? <><Loader2 size={14} className="spin"/> Building…</> : <><Lightbulb size={14}/> Generate learning plan</>}
          </button>
          {learning && <LearningPlanList plan={learning} onClear={() => setLearning(null)}/>}
        </section>
      </aside>
    </div>
  </>
}
