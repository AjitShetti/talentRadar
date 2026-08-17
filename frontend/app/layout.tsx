import type { Metadata } from 'next';
import { Space_Grotesk, Manrope } from 'next/font/google';
import './globals.css';
import Header from '@/components/Header';
import AuthProvider from '@/components/AuthProvider';
import { AuthModalProvider } from '@/components/AuthModalProvider';

const spaceGrotesk = Space_Grotesk({
  subsets: ['latin'],
  variable: '--font-space-grotesk',
});

const manrope = Manrope({
  subsets: ['latin'],
  variable: '--font-manrope',
});

export const metadata: Metadata = {
  title: 'TalentRadar - AI-Powered Job Intelligence',
  description: 'Real-time multi-source job search, resume studio, and AI interview prep',
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className={`${spaceGrotesk.variable} ${manrope.variable}`}>
      <body>
        <AuthProvider>
          <AuthModalProvider>
            <div className="grid-lines" />
            <div className="container" style={{ position: 'relative', zIndex: 1 }}>
              <Header />
              {children}
            </div>
          </AuthModalProvider>
        </AuthProvider>
      </body>
    </html>
  );
}
