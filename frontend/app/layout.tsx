import type { Metadata } from 'next';
import { GeistSans } from 'geist/font/sans';
import { GeistMono } from 'geist/font/mono';
import './globals.css';
import Header from '@/components/Header';
import AuthProvider from '@/components/AuthProvider';
import { AuthModalProvider } from '@/components/AuthModalProvider';

export const metadata: Metadata = {
  title: 'TalentRadar - Job Search for Engineers',
  description: 'Live ATS job search, AI resume studio, and voice interview practice for software engineers.',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${GeistSans.variable} ${GeistMono.variable}`}>
      <body className={GeistSans.className}>
        <AuthProvider>
          <AuthModalProvider>
            <div style={{ minHeight: '100dvh', display: 'flex', flexDirection: 'column' }}>
              <Header />
              <main style={{ flex: 1, paddingBottom: '5rem' }}>
                <div style={{ maxWidth: '1280px', margin: '0 auto', padding: '0 1.5rem' }}>
                  {children}
                </div>
              </main>
            </div>
          </AuthModalProvider>
        </AuthProvider>
      </body>
    </html>
  );
}
