'use client'

import Link from 'next/link'
import AppShell from '@/components/AppShell'
import CopilotWorkspace from '@/components/CopilotWorkspace'
import RequireAuth from '@/components/RequireAuth'

/**
 * The Career Copilot's old home.
 *
 * The copilot now lives on the overview page, alongside the briefing and the
 * feedback panels it answers about, and it is no longer in the sidebar. This
 * route stays so bookmarks and older links keep working — it renders the same
 * workspace, plus the pointer back to where it belongs.
 */
export default function AgentPage() {
  return <RequireAuth><AppShell>
    <section className="page-heading">
      <div>
        <span className="board-kicker">Career copilot</span>
        <h1>Ask anything about your search<span>.</span></h1>
        <p>
        It reads your profile, tracker and interview history — not guesswork.
        This now lives on your <Link className="text-button" href="/dashboard">overview</Link>,
          next to today&apos;s briefing.
        </p>
      </div>
    </section>

    <CopilotWorkspace acceptUrlQuestion/>
  </AppShell></RequireAuth>
}
