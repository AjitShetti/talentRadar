'use client';

import { useEffect } from 'react';
import { useRouter } from 'next/navigation';
import { useAuthModal } from '@/components/AuthModalProvider';

/**
 * The signup page now opens as an inline modal popup.
 * Anyone who navigates directly to /signup is redirected to home
 * with the create-account tab opening immediately.
 */
export default function SignupRedirect() {
  const router = useRouter();
  const { openSignup } = useAuthModal();

  useEffect(() => {
    openSignup();
    router.replace('/');
  }, [openSignup, router]);

  return null;
}
