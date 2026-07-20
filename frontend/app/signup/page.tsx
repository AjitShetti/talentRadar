"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { Mail, Lock, ArrowRight, Loader2, AlertCircle } from "lucide-react";
import { signIn } from "next-auth/react";

export default function SignupPage() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [error, setError] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const router = useRouter();

  const validatePassword = (pass: string) => {
    if (pass.length < 8) return "Password must be at least 8 characters long.";
    return null;
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setIsLoading(true);
    setError("");

    if (password !== confirmPassword) {
      setError("Passwords do not match.");
      setIsLoading(false);
      return;
    }

    const passwordError = validatePassword(password);
    if (passwordError) {
      setError(passwordError);
      setIsLoading(false);
      return;
    }

    try {
      // Register the user via the API
      const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
      const res = await fetch(`${API_URL}/api/auth/signup`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password }),
      });

      if (!res.ok) {
        const data = await res.json();
        setError(data.detail || "Registration failed. Please try again.");
        setIsLoading(false);
        return;
      }

      // Automatically sign in the user after successful registration
      const signInRes = await signIn("credentials", {
        redirect: false,
        email,
        password,
      });

      if (signInRes?.error) {
        setError("Account created, but automatic sign in failed. Please log in manually.");
        router.push("/login");
      } else {
        router.push("/search"); // Redirect to job search
      }
    } catch (err) {
      setError("An unexpected error occurred. Could not reach server.");
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="container" style={{ minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
      <div className="panel" style={{ width: '100%', maxWidth: '400px' }}>
        <div style={{ textAlign: 'center', marginBottom: '2rem' }}>
          <h1 className="text-accent" style={{ marginBottom: '0.5rem' }}>CREATE ACCOUNT</h1>
          <p style={{ color: 'var(--color-fg-muted)' }}>Join TalentRadar and discover your next role.</p>
        </div>

        {error && (
          <div style={{ padding: '0.75rem', border: '1px solid #ef4444', backgroundColor: 'rgba(239, 68, 68, 0.1)', color: '#fca5a5', marginBottom: '1.5rem', display: 'flex', alignItems: 'flex-start', gap: '0.5rem' }}>
            <AlertCircle size={18} style={{ flexShrink: 0, marginTop: '0.125rem' }} />
            <span>{error}</span>
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
                placeholder="At least 8 characters"
                style={{ width: '100%', padding: '0.75rem 0.75rem 0.75rem 2.5rem', background: 'transparent', border: '1px solid var(--color-border)', color: 'var(--color-fg)', fontFamily: 'var(--font-body)' }}
              />
            </div>
          </div>

          <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
            <label style={{ fontSize: '0.875rem', color: 'var(--color-fg-muted)', fontFamily: 'var(--font-display)' }}>CONFIRM PASSWORD</label>
            <div style={{ position: 'relative' }}>
              <div style={{ position: 'absolute', inset: '0', right: 'auto', paddingLeft: '0.75rem', display: 'flex', alignItems: 'center', pointerEvents: 'none' }}>
                <Lock size={18} style={{ color: 'var(--color-fg-muted)' }} />
              </div>
              <input
                type="password"
                required
                value={confirmPassword}
                onChange={(e) => setConfirmPassword(e.target.value)}
                placeholder="••••••••"
                style={{ width: '100%', padding: '0.75rem 0.75rem 0.75rem 2.5rem', background: 'transparent', border: '1px solid var(--color-border)', color: 'var(--color-fg)', fontFamily: 'var(--font-body)' }}
              />
            </div>
          </div>

          <button type="submit" disabled={isLoading} className="btn btn-primary" style={{ width: '100%', justifyContent: 'center', marginTop: '1rem' }}>
            {isLoading ? <Loader2 className="animate-spin" size={18} /> : (
              <>SIGN UP <ArrowRight size={18} /></>
            )}
          </button>
        </form>

        <div style={{ marginTop: '2rem', textAlign: 'center', fontSize: '0.875rem', color: 'var(--color-fg-muted)' }}>
          Already have an account? <Link href="/login" className="text-accent" style={{ textDecoration: 'underline' }}>Log in</Link>
        </div>
      </div>
    </div>
  );
}
