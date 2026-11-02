import { redirect } from 'next/navigation'

/** The ranking now sits under the weekly contest; old links land there. */
export default function LeaderboardPage() {
  redirect('/practice#contest')
}
