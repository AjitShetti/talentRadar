/**
 * Server-side reads for the public pages (/r, /hiring, sitemap).
 *
 * These run during static generation and background revalidation on the
 * frontend host — never in a visitor's browser — which is the whole point: the
 * API sleeps on its free tier, and a stranger's first page must not wait for
 * it to wake. A page is rebuilt at most every REVALIDATE_SECONDS; when the API
 * cannot be reached during a rebuild, Next keeps serving the last good copy.
 */
import type { Liveness } from '@/lib/api'

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'
export const SITE_URL = (process.env.NEXT_PUBLIC_SITE_URL || 'https://talent-radar-ten.vercel.app').replace(/\/$/, '')

// Six hours. A verification stays valid for 72 (domain/liveness.py), so a page
// this old still shows a verdict that is true.
export const REVALIDATE_SECONDS = 21600

export type PublicRole = {
  id: string; slug: string; title: string; company: string | null; company_slug: string | null
  location: string | null; is_remote: boolean; skills: string[]
  employment_type: string | null; seniority: string | null; source_url: string | null
  posted_at: string | null; closed_at: string | null; liveness: Liveness
}
export type PublicRoleListing = { slug: string; title: string; company: string | null; location: string | null; updated_at: string | null }
export type PublicCompany = { company: string; slug: string | null; open_count: number; roles: PublicRole[] }

class NotFound extends Error {}

async function read<T>(path: string): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, { next: { revalidate: REVALIDATE_SECONDS } })
  if (response.status === 404) throw new NotFound(path)
  if (!response.ok) throw new Error(`${path} returned ${response.status}`)
  return response.json() as Promise<T>
}

/** `null` means the role does not exist; any other failure is thrown so a rebuild keeps the stale page. */
export async function publicRole(slug: string): Promise<PublicRole | null> {
  try { return await read<PublicRole>(`/api/v1/public/roles/${encodeURIComponent(slug)}`) }
  catch (error) { if (error instanceof NotFound) return null; throw error }
}

export async function publicCompany(slug: string): Promise<PublicCompany | null> {
  try { return await read<PublicCompany>(`/api/v1/public/companies/${encodeURIComponent(slug)}`) }
  catch (error) { if (error instanceof NotFound) return null; throw error }
}

/** Never throws: a build or a sitemap must not fail because the API is asleep. */
export async function publicRoleList(limit = 500): Promise<PublicRoleListing[]> {
  try { return (await read<{ roles: PublicRoleListing[] }>(`/api/v1/public/roles?limit=${limit}`)).roles || [] }
  catch { return [] }
}
