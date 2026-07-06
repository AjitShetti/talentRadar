import type { Metadata } from 'next';
import { Space_Grotesk, Manrope } from 'next/font/google';
import './globals.css';
import Header from '@/components/Header';
import AuthProvider from '@/components/AuthProvider';

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
  description: 'Search jobs, analyze market trends, and find perfect matches with AI',
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
          <div className="grid-lines" />
          <div className="container" style={{ position: 'relative', zIndex: 1 }}>
            <Header />
            {children}
          </div>
        </AuthProvider>
      </body>
    </html>
  );
}
