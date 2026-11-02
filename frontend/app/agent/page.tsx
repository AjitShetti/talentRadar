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
    <header className="day-title">
      <div className="day-head">
        <h1 className="day-h1">Ask anything about your search.</h1>
      </div>
      <p className="ov2-lead">It reads your profile, tracker and interview history. It also lives on your <Link href="/dashboard">Overview</Link>.</p>
    </header>

    <CopilotWorkspace acceptUrlQuestion/>
  </AppShell></RequireAuth>
}
