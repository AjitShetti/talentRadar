import type { Metadata } from 'next';
import { Plus_Jakarta_Sans, JetBrains_Mono } from 'next/font/google';
import './globals.css';
import Header from '@/components/Header';
import AuthProvider from '@/components/AuthProvider';
import { AuthModalProvider } from '@/components/AuthModalProvider';

const jakarta = Plus_Jakarta_Sans({
  subsets: ['latin'],
  variable: '--font-jakarta',
  display: 'swap',
});

const jetbrainsMono = JetBrains_Mono({
  subsets: ['latin'],
  variable: '--font-mono',
  display: 'swap',
});

export const metadata: Metadata = {
  title: 'TalentRadar - Intelligent Job Telemetry & Career Studio',
  description: 'Real-time multi-source job aggregation, ATS resume studio, and AI interview simulations.',
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className={`${jakarta.variable} ${jetbrainsMono.variable}`}>
      <body>
        <AuthProvider>
          <AuthModalProvider>
            <div className="grid-lines" aria-hidden="true" />
            <div style={{ position: 'relative', zIndex: 1, minHeight: '100dvh', display: 'flex', flexDirection: 'column' }}>
              <Header />
              <main style={{ flex: 1, paddingBottom: '4rem' }}>
                <div className="container">
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
