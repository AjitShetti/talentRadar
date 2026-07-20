"use client";

import { useState } from "react";
import { signIn } from "next-auth/react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { Mail, Lock, ArrowRight, Loader2 } from "lucide-react";

export default function LoginPage() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const router = useRouter();

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setIsLoading(true);
    setError("");

    try {
      const res = await signIn("credentials", {
        redirect: false,
        email,
        password,
      });

      if (res?.error) {
        setError("Invalid email or password");
      } else {
        router.push("/search"); // Redirect to job search page
      }
    } catch (err) {
      setError("An unexpected error occurred.");
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="container" style={{ minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
      <div className="panel" style={{ width: '100%', maxWidth: '400px' }}>
        <div style={{ textAlign: 'center', marginBottom: '2rem' }}>
          <h1 className="text-accent" style={{ marginBottom: '0.5rem' }}>WELCOME BACK</h1>
          <p style={{ color: 'var(--color-fg-muted)' }}>Sign in to TalentRadar to continue your journey.</p>
        </div>

        {error && (
          <div style={{ padding: '0.75rem', border: '1px solid #ef4444', backgroundColor: 'rgba(239, 68, 68, 0.1)', color: '#fca5a5', marginBottom: '1.5rem', textAlign: 'center' }}>
            {error}
          </div>
        )}

        <form onSubmit={handleSubmit} style={{ display: 'flex', flexDirection: 'column', gap: '1.25rem' }}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
            <label style={{ fontSize: '0.875rem', color: 'var(--color-fg-muted)', fontFamily: 'var(--font-display)' }}>EMAIL</label>
            <div style={{ position: 'relative' }}>
              <div style={{ position: 'absolute', inset: '0', right: 'auto', paddingLeft: '0.75rem', display: 'flex', alignItems: 'center', pointerEvents: 'none' }}>
                <Mail size={18} style={{ color: 'var(--color-fg-muted)' }} />
              </div>
              <input
                type="email"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="you@example.com"
                style={{ width: '100%', padding: '0.75rem 0.75rem 0.75rem 2.5rem', background: 'transparent', border: '1px solid var(--color-border)', color: 'var(--color-fg)', fontFamily: 'var(--font-body)' }}
              />
            </div>
          </div>

          <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
            <label style={{ fontSize: '0.875rem', color: 'var(--color-fg-muted)', fontFamily: 'var(--font-display)' }}>PASSWORD</label>
            <div style={{ position: 'relative' }}>
              <div style={{ position: 'absolute', inset: '0', right: 'auto', paddingLeft: '0.75rem', display: 'flex', alignItems: 'center', pointerEvents: 'none' }}>
                <Lock size={18} style={{ color: 'var(--color-fg-muted)' }} />
              </div>
              <input
                type="password"
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="••••••••"
                style={{ width: '100%', padding: '0.75rem 0.75rem 0.75rem 2.5rem', background: 'transparent', border: '1px solid var(--color-border)', color: 'var(--color-fg)', fontFamily: 'var(--font-body)' }}
              />
            </div>
          </div>

          <button type="submit" disabled={isLoading} className="btn btn-primary" style={{ width: '100%', justifyContent: 'center', marginTop: '1rem' }}>
            {isLoading ? <Loader2 className="animate-spin" size={18} /> : (
              <>SIGN IN <ArrowRight size={18} /></>
            )}
          </button>
        </form>

        <div style={{ marginTop: '2rem', textAlign: 'center', fontSize: '0.875rem', color: 'var(--color-fg-muted)' }}>
          Don't have an account? <Link href="/signup" className="text-accent" style={{ textDecoration: 'underline' }}>Sign up</Link>
        </div>
      </div>
    </div>
  );
}
