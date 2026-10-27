import type { MetadataRoute } from 'next'

/** Install metadata; the favicon, icon.svg and apple-icon.png beside this file cover the browser tab. */
export default function manifest(): MetadataRoute.Manifest {
  return {
    name: 'TalentRadar',
    short_name: 'TalentRadar',
    description: 'AI-powered career intelligence for modern job seekers.',
    start_url: '/dashboard',
    display: 'standalone',
    background_color: '#000000',
    theme_color: '#000000',
    icons: [
      { src: '/icon-192.png', sizes: '192x192', type: 'image/png' },
      { src: '/icon-512.png', sizes: '512x512', type: 'image/png' },
      { src: '/maskable-512.png', sizes: '512x512', type: 'image/png', purpose: 'maskable' },
    ],
  }
}
