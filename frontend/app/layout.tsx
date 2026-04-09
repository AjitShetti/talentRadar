import type { Metadata } from 'next';
import './globals.css';

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
    <html lang="en">
      <body className="min-h-screen bg-gradient-to-br from-slate-50 to-slate-100">
        {children}
      </body>
    </html>
  );
}
