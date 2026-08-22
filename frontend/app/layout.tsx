import type { Metadata } from 'next'
import './globals.css'

export const metadata: Metadata = { title: 'TalentRadar — Your next move, clearer', description: 'AI-powered career intelligence for modern job seekers.' }

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return <html lang="en"><body>{children}</body></html>
}
