import type { MetadataRoute } from 'next'
import { SITE_URL } from '@/lib/public-api'

/** Public pages are indexable; everything behind sign-in is a private workspace and is kept out. */
export default function robots(): MetadataRoute.Robots {
  return {
    rules: [{
      userAgent: '*',
      allow: ['/', '/check', '/r/', '/hiring/'],
      disallow: ['/dashboard', '/search', '/applications', '/interview', '/resume-studio', '/company-intel', '/agent', '/settings', '/onboarding', '/roles/'],
    }],
    sitemap: `${SITE_URL}/sitemap.xml`,
  }
}
