"use client";

import { useState } from "react";
import { signIn } from "next-auth/react";
import { useRouter } from "next/navigation";
import { Mail, Lock, ArrowRight, Loader2, AlertCircle, X } from "lucide-react";

interface AuthModalProps {
  isOpen: boolean;
  onClose: () => void;
  initialMode?: "login" | "signup";
}

export default function AuthModal({ isOpen, onClose, initialMode = "login" }: AuthModalProps) {
  const [mode, setMode] = useState<"login" | "signup">(initialMode);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [error, setError] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const router = useRouter();

  if (!isOpen) return null;

  const validatePassword = (pass: string) => {
    if (pass.length < 8) return "Password must be at least 8 characters long.";
    return null;
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setIsLoading(true);
    setError("");

    if (mode === "signup") {
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

        const signInRes = await signIn("credentials", {
          redirect: false,
          email,
          password,
        });

        if (signInRes?.error) {
          setError("Account created, but automatic sign in failed. Please log in manually.");
          setMode("login");
        } else {
          onClose();
          router.push("/search");
        }
      } catch (err) {
        setError("An unexpected error occurred. Could not reach server.");
      } finally {
        setIsLoading(false);
      }
    } else {
      // Login mode
      try {
        const res = await signIn("credentials", {
          redirect: false,
          email,
          password,
        });

        if (res?.error) {
          setError("Invalid email or password");
        } else {
          onClose();
          router.push("/search");
        }
      } catch (err) {
        setError("An unexpected error occurred.");
      } finally {
        setIsLoading(false);
      }
    }
  };

  return (
    <div 
      style={{ 
        position: 'fixed',
        top: 0,
        left: 0,
        right: 0,
        bottom: 0,
        zIndex: 50,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        padding: '1rem',
        background: 'rgba(0, 0, 0, 0.6)', 
        backdropFilter: 'blur(4px)' 
      }}
    >
      <div 
        className="panel relative" 
        style={{ width: '100%', maxWidth: '400px', animation: 'fadeIn 0.3s ease-out' }}
      >
        <button 
          onClick={onClose}
          style={{ position: 'absolute', top: '1rem', right: '1rem', background: 'transparent', border: 'none', cursor: 'pointer', color: 'var(--color-fg-muted)' }}
        >
          <X size={20} />
        </button>

        <div style={{ textAlign: 'center', marginBottom: '2rem' }}>
          <h1 className="text-accent" style={{ marginBottom: '0.5rem', fontSize: '1.5rem' }}>
            {mode === "login" ? "WELCOME BACK" : "CREATE ACCOUNT"}
          </h1>
          <p style={{ color: 'var(--color-fg-muted)', fontSize: '0.875rem' }}>
            {mode === "login" 
              ? "Sign in to TalentRadar to continue your journey." 
              : "Join TalentRadar and discover your next role."}
          </p>
        </div>

        {error && (
          <div style={{ padding: '0.75rem', border: '1px solid #ef4444', backgroundColor: 'rgba(239, 68, 68, 0.1)', color: '#fca5a5', marginBottom: '1.5rem', display: 'flex', alignItems: 'flex-start', gap: '0.5rem', fontSize: '0.875rem' }}>
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
                style={{ width: '100%', padding: '0.75rem 0.75rem 0.75rem 2.5rem', background: 'rgba(255,255,255,0.05)', border: '1px solid var(--color-border)', color: 'var(--color-fg)', fontFamily: 'var(--font-body)', borderRadius: '0.5rem' }}
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
                placeholder={mode === "signup" ? "At least 8 characters" : "••••••••"}
                style={{ width: '100%', padding: '0.75rem 0.75rem 0.75rem 2.5rem', background: 'rgba(255,255,255,0.05)', border: '1px solid var(--color-border)', color: 'var(--color-fg)', fontFamily: 'var(--font-body)', borderRadius: '0.5rem' }}
              />
            </div>
          </div>

          {mode === "signup" && (
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
                  style={{ width: '100%', padding: '0.75rem 0.75rem 0.75rem 2.5rem', background: 'rgba(255,255,255,0.05)', border: '1px solid var(--color-border)', color: 'var(--color-fg)', fontFamily: 'var(--font-body)', borderRadius: '0.5rem' }}
                />
              </div>
            </div>
          )}

          <button type="submit" disabled={isLoading} className="btn btn-primary" style={{ width: '100%', justifyContent: 'center', marginTop: '0.5rem' }}>
            {isLoading ? <Loader2 className="animate-spin" size={18} /> : (
              <>{mode === "login" ? "SIGN IN" : "SIGN UP"} <ArrowRight size={18} /></>
            )}
          </button>
        </form>

        <div style={{ marginTop: '1.5rem', textAlign: 'center', fontSize: '0.875rem', color: 'var(--color-fg-muted)' }}>
          {mode === "login" ? (
            <>
              Don't have an account?{' '}
              <button 
                type="button" 
                onClick={() => { setMode("signup"); setError(""); }} 
                className="text-accent" 
                style={{ textDecoration: 'underline', background: 'transparent', border: 'none', cursor: 'pointer', fontFamily: 'inherit', padding: 0 }}
              >
                Sign up
              </button>
            </>
          ) : (
            <>
              Already have an account?{' '}
              <button 
                type="button" 
                onClick={() => { setMode("login"); setError(""); }} 
                className="text-accent" 
                style={{ textDecoration: 'underline', background: 'transparent', border: 'none', cursor: 'pointer', fontFamily: 'inherit', padding: 0 }}
              >
                Log in
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
