import type { Metadata } from 'next'
import './globals.css'

export const metadata: Metadata = { title: 'TalentRadar — Your next move, clearer', description: 'AI-powered career intelligence for modern job seekers.' }

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return <html lang="en"><body>
    {/*
      THESIS: the overview is a live departures board, not a card dashboard —
      every status (applications, interviews, briefing) reads as a ranked,
      ruled row, refusing the bordered-card-grid default.
      OWN-WORLD: graphite board face, brushed-steel neutrals, warm off-white
      ink, amber reserved for what needs you; Big Shoulders Text for condensed
      row labels, Fragment Mono for tabular data, Public Sans for reading
      copy; hairline rules replace boxes.
      STORY: a job seeker opens Overview and reads today's status at a
      glance — what's stale, what's moving, what to do next — the way you'd
      read a station board, not scan a grid of stat cards.
      FIRST VIEWPORT: a ruled header ledger of live counters, then a ranked
      "today" board of briefing rows, each with a status lamp, kind,
      headline and action.
      FORM: signals-instruments-split-flap-concourse, challenger, beat the
      assigned field-notebook direction; seed 735ca199.
      FINISH: unreviewed and undocumented is unfinished; this build ends
      with the finish review, the verdict, DESIGN.md, and every shipping
      raster carrying its provenance.
    */}
    {children}
  </body></html>
}
