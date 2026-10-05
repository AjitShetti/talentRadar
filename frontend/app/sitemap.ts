import type { MetadataRoute } from 'next'
import { REVALIDATE_SECONDS, SITE_URL, publicRoleList } from '@/lib/public-api'

export const revalidate = REVALIDATE_SECONDS

/** The landing page, the check, and every open public role. Never fails: an unreachable API yields the static entries alone. */
export default async function sitemap(): Promise<MetadataRoute.Sitemap> {
  const roles = await publicRoleList(2000)
  return [
    { url: `${SITE_URL}/`, changeFrequency: 'weekly', priority: 1 },
    { url: `${SITE_URL}/check`, changeFrequency: 'monthly', priority: 0.8 },
    ...roles.map(role => ({
      url: `${SITE_URL}/r/${role.slug}`,
      lastModified: role.updated_at ? new Date(role.updated_at) : undefined,
      changeFrequency: 'daily' as const,
      priority: 0.6,
    })),
  ]
}
