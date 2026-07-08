'use client';

import { createContext, useContext, useState, useCallback, ReactNode } from 'react';
import AuthModal from './AuthModal';

interface AuthModalContextValue {
  openLogin: () => void;
  openSignup: () => void;
  close: () => void;
}

const AuthModalContext = createContext<AuthModalContextValue | null>(null);

export function AuthModalProvider({ children }: { children: ReactNode }) {
  const [isOpen, setIsOpen] = useState(false);
  const [tab, setTab] = useState<'login' | 'signup'>('login');

  const openLogin = useCallback(() => { setTab('login');  setIsOpen(true); }, []);
  const openSignup = useCallback(() => { setTab('signup'); setIsOpen(true); }, []);
  const close = useCallback(() => setIsOpen(false), []);

  return (
    <AuthModalContext.Provider value={{ openLogin, openSignup, close }}>
      {children}
      <AuthModal isOpen={isOpen} onClose={close} defaultTab={tab} />
    </AuthModalContext.Provider>
  );
}

export function useAuthModal() {
  const ctx = useContext(AuthModalContext);
  if (!ctx) throw new Error('useAuthModal must be used inside <AuthModalProvider>');
  return ctx;
}
