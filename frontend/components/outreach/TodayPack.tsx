'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import Link from 'next/link'
import { Loader2 } from 'lucide-react'
import PackCompanyCard, { PackItemRow } from '@/components/outreach/PackCompanyCard'
import { track } from '@/lib/analytics'
import { api, OutreachPack, PackCompany, PackItem } from '@/lib/api'
import { companiesToWrite, followUpsToWrite, shownItemIds } from '@/lib/outreach-pack'

// The server takes at most this many ids in one "seen" call (MAX_SEEN_IDS).
const SEEN_BATCH = 20

const messageOf = (err: unknown, fallback: string) => (err instanceof Error && err.message ? err.message : fallback)

/** The pack with these items swapped in by id, wherever they sit. */
function withItems(pack: OutreachPack, items: PackItem[]): OutreachPack {
  const byId = new Map(items.map(item => [item.id, item]))
  const swap = (item: PackItem) => byId.get(item.id) ?? item
  return {
    ...pack,
    companies: pack.companies.map(c => ({ ...c, items: c.items.map(swap) })),
    follow_ups: pack.follow_ups.map(swap),
  }
}

type Props = {
  /** Call after something the rest of /outreach shows changed (a thread was opened or a touch recorded). */
  onChanged: () => void
}

/**
 * Today's pack at the top of /outreach: up to five companies picked from the user's
 * profile, who to write to at each, and a message ready to review. The user sends it
 * from their own mail or LinkedIn; this only records what they say they did.
 */
export default function TodayPack({ onChanged }: Props) {
  const [pack, setPack] = useState<OutreachPack | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  // Company ids and follow-up item ids whose messages are being written now.
  const [writing, setWriting] = useState<Set<string>>(new Set())
  const started = useRef(false)

  const mark = (id: string, on: boolean) => setWriting(cur => {
    const next = new Set(cur)
    if (on) next.add(id)
    else next.delete(id)
    return next
  })

  const writeCompany = useCallback(async (companyId: string) => {
    mark(companyId, true)
    try {
      const { items } = await api.outreach.pack.writeCompany(companyId)
      setPack(cur => cur && withItems(cur, items))
    } catch {
      // The rows say the message could not be written and offer to try again.
    } finally {
      mark(companyId, false)
    }
  }, [])

  const writeFollowUp = useCallback(async (itemId: string) => {
    mark(itemId, true)
    try {
      const { item } = await api.outreach.pack.writeItem(itemId)
      setPack(cur => cur && withItems(cur, [item]))
    } catch {
      // As above: the row offers to try again.
    } finally {
      mark(itemId, false)
    }
  }, [])

  // Read (the day's first read builds the pack), mark what is shown as seen, then write
  // the messages one company at a time so the first company is readable soonest.
  useEffect(() => {
    if (started.current) return
    started.current = true
    void (async () => {
      let loaded: OutreachPack
      try {
        loaded = await api.outreach.pack.get()
      } catch (err) {
        setError(messageOf(err, "Could not load today's pack."))
        setLoading(false)
        return
      }
      const companies = companiesToWrite(loaded)
      const followUps = followUpsToWrite(loaded)
      setWriting(new Set([...companies, ...followUps]))
      setPack(loaded)
      setLoading(false)
      const ids = shownItemIds(loaded)
      for (let i = 0; i < ids.length; i += SEEN_BATCH) {
        api.outreach.pack.seen(ids.slice(i, i + SEEN_BATCH)).catch(() => undefined)
      }
      for (const id of companies) await writeCompany(id)
      for (const id of followUps) await writeFollowUp(id)
    })()
  }, [writeCompany, writeFollowUp])

  async function sent(item: PackItem, message: string, subject: string | null) {
    setError('')
    try {
      const { item: saved } = await api.outreach.pack.sent(item.id, { message, subject })
      setPack(cur => cur && withItems(cur, [saved]))
      track('outreach_sent', { channel: item.channel })
      onChanged()
    } catch (err) {
      setError(messageOf(err, 'Could not record that.'))
    }
  }

  // The item goes at once; if the server refuses, it comes back.
  function skip(item: PackItem) {
    setError('')
    setPack(cur => cur && withItems(cur, [{ ...item, status: 'skipped' }]))
    api.outreach.pack.skip(item.id).catch(err => {
      setPack(cur => cur && withItems(cur, [item]))
      setError(messageOf(err, 'Could not skip that.'))
    })
  }

  function retry(item: PackItem) {
    if (item.kind === 'follow_up') void writeFollowUp(item.id)
    else {
      const company = pack?.companies.find(c => c.items.some(i => i.id === item.id))
      if (company?.company_id) void writeCompany(company.company_id)
    }
  }

  async function applied(company: PackCompany, jobId: string) {
    if (!company.company_id) return
    setError('')
    try {
      await api.outreach.pack.applied(company.company_id, jobId)
      setPack(cur => cur && { ...cur, companies: cur.companies.map(c => c.company_id === company.company_id ? { ...c, applied: true } : c) })
      onChanged()
    } catch (err) {
      setError(messageOf(err, 'Could not record the application.'))
    }
  }

  const handlers = { onSent: sent, onSkip: skip, onWrite: retry }
  const ready = pack
    ? [...pack.companies.flatMap(c => c.items), ...pack.follow_ups].filter(i => i.status === 'new' && i.message !== null).length
    : 0
  const followUps = pack?.follow_ups.filter(i => i.status !== 'skipped') ?? []

  return <section className="or-section pack" id="today">
    <div className="or-head">
      <h2>Today&rsquo;s pack</h2><span className="or-rule" />
      {pack && <span className="or-note">{pack.companies.length} {pack.companies.length === 1 ? 'company' : 'companies'} &middot; {ready} ready</span>}
    </div>
    <p className="or-hint">Review, send it yourself, and mark it sent. Nothing is ever sent for you.</p>
    {error && <p className="form-error">{error}</p>}

    {loading
      ? <div className="loading-state"><Loader2 className="spin" /> Picking today&rsquo;s companies&hellip;</div>
      : pack && (pack.companies.length === 0 && followUps.length === 0
        ? <div className="empty-state"><p>Nothing new matched today. Add skills or target roles to widen it. <Link href="/settings">Open your profile &rarr;</Link></p></div>
        : <>
            {pack.companies.map(company => <PackCompanyCard
              key={company.company_id ?? company.name}
              company={company}
              writing={company.company_id !== null && writing.has(company.company_id)}
              onApplied={applied}
              {...handlers}
            />)}
            {followUps.length > 0 && <section className="pack-company">
              <div className="pack-company-top"><h3>Follow-ups due</h3></div>
              {followUps.map(item => <PackItemRow
                key={`${item.id}:${item.message === null ? 'pending' : 'ready'}`}
                item={item}
                writing={writing.has(item.id)}
                {...handlers}
              />)}
            </section>}
          </>)}
  </section>
}
