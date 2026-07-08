'use client';

import { useEffect } from 'react';
import { useRouter } from 'next/navigation';
import { useAuthModal } from '@/components/AuthModalProvider';

/**
 * The login page now opens as an inline modal popup.
 * Anyone who navigates directly to /login is redirected to home
 * with the sign-in modal opening immediately.
 */
export default function LoginRedirect() {
  const router = useRouter();
  const { openLogin } = useAuthModal();

  useEffect(() => {
    openLogin();
    router.replace('/');
  }, [openLogin, router]);

  return null;
}
