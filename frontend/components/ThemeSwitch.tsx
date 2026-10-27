'use client'

import { useEffect, useState } from 'react'
import { Monitor, Moon, Sun, type LucideIcon } from 'lucide-react'
import { readChoice, saveChoice, THEME_EVENT, THEME_KEY, type ThemeChoice } from '@/lib/theme'

const OPTIONS: { value: ThemeChoice; label: string; icon: LucideIcon }[] = [
  { value: 'system', label: 'System', icon: Monitor },
  { value: 'light', label: 'Light', icon: Sun },
  { value: 'dark', label: 'Dark', icon: Moon },
]

/**
 * System / Light / Dark as one segmented row, in the spirit of Framer's ThemeSwitcher:
 * the choice is saved per browser and applied to <html data-theme> at once (lib/theme.ts).
 * Left and right arrows move the choice, as in any radio group.
 */
export default function ThemeSwitch({ tabbable = true }: { tabbable?: boolean }) {
  const [choice, setChoice] = useState<ThemeChoice>('dark')

  useEffect(() => {
    setChoice(readChoice())
    const onChange = (e: Event) => setChoice((e as CustomEvent<ThemeChoice>).detail)
    const onStorage = (e: StorageEvent) => { if (e.key === THEME_KEY) setChoice(readChoice()) }
    window.addEventListener(THEME_EVENT, onChange)
    window.addEventListener('storage', onStorage)
    return () => { window.removeEventListener(THEME_EVENT, onChange); window.removeEventListener('storage', onStorage) }
  }, [])

  const index = OPTIONS.findIndex(option => option.value === choice)
  const pick = (value: ThemeChoice) => { setChoice(value); saveChoice(value) }

  return <div
    className="theme-switch"
    role="radiogroup"
    aria-label="Appearance"
    style={{ '--i': index } as React.CSSProperties}
    onKeyDown={e => {
      if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return
      e.preventDefault()
      const next = OPTIONS[(index + (e.key === 'ArrowRight' ? 1 : OPTIONS.length - 1)) % OPTIONS.length]
      pick(next.value)
      e.currentTarget.querySelector<HTMLElement>(`[data-value="${next.value}"]`)?.focus()
    }}
  >
    <span className="theme-switch-glider" aria-hidden="true" />
    {OPTIONS.map(({ value, label, icon: Icon }) => <button
      key={value}
      type="button"
      role="radio"
      aria-checked={choice === value}
      data-value={value}
      tabIndex={tabbable && choice === value ? 0 : -1}
      onClick={() => pick(value)}
    ><Icon size={13} aria-hidden="true" />{label}</button>)}
  </div>
}
