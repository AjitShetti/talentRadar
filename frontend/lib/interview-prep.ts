/**
 * The Interview Lab link for a posting. The lab reads `job`, `role` and `company` from its URL
 * and anchors the session to that posting; with no job it is a plain mock interview.
 * The briefing's own card builds the same link (services/copilot.py::interview_prep_href).
 */
export function interviewPrepHref(jobId: string | null | undefined, role: string, company: string | null | undefined) {
  if (!jobId) return '/interview'
  return `/interview?${new URLSearchParams({ job: jobId, role, company: company || '' })}`
}
