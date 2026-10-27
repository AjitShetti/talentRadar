/**
 * The viewer's theme. The choice ('system' | 'light' | 'dark') is kept per browser in
 * localStorage; <html data-theme> always holds what is shown ('light' | 'dark'), so the
 * CSS needs one light block and no media query. THEME_SCRIPT runs in <head> before the
 * first paint, so a page never flashes the other theme.
 */
export type ThemeChoice = 'system' | 'light' | 'dark'

export const THEME_KEY = 'tr:theme'
export const THEME_EVENT = 'tr:theme-change'
/** Until a viewer picks, the app follows their device. */
export const DEFAULT_THEME: ThemeChoice = 'system'

export function isChoice(value: unknown): value is ThemeChoice {
  return value === 'system' || value === 'light' || value === 'dark'
}

export function readChoice(): ThemeChoice {
  try {
    const stored = localStorage.getItem(THEME_KEY)
    return isChoice(stored) ? stored : DEFAULT_THEME
  } catch {
    return DEFAULT_THEME
  }
}

export function resolve(choice: ThemeChoice): 'light' | 'dark' {
  if (choice !== 'system') return choice
  return window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark'
}

export function applyChoice(choice: ThemeChoice) {
  document.documentElement.dataset.theme = resolve(choice)
}

export function saveChoice(choice: ThemeChoice) {
  try { localStorage.setItem(THEME_KEY, choice) } catch { /* storage blocked: applies to this page only */ }
  applyChoice(choice)
  window.dispatchEvent(new CustomEvent<ThemeChoice>(THEME_EVENT, { detail: choice }))
}

/**
 * The same rules as readChoice/resolve, inlined as a string for <head>. It re-applies when
 * the device switches (for 'system') and when another tab saves a new choice.
 */
export const THEME_SCRIPT = `(function(){var d=document.documentElement,m=window.matchMedia('(prefers-color-scheme: light)');function a(){var c='${DEFAULT_THEME}';try{var s=localStorage.getItem('${THEME_KEY}');if(s==='system'||s==='light'||s==='dark')c=s}catch(e){}d.dataset.theme=c==='system'?(m.matches?'light':'dark'):c}a();m.addEventListener('change',a);window.addEventListener('storage',function(e){if(e.key==='${THEME_KEY}')a()})})()`
