import { RefObject, useCallback, useEffect, useRef } from 'react'

export const FOCUSABLE = 'a[href],button:not([disabled]),select:not([disabled]),input:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex="-1"])'

/**
 * Focus moves into the dialog when it opens and back to what opened it when it
 * closes. Escape closes it. A modal dialog also keeps Tab inside itself.
 */
export function useDialog(ref: RefObject<HTMLElement>, onClose: () => void, options: { modal: boolean; closeOnEscape: boolean }) {
  const { modal, closeOnEscape } = options
  const closeRef = useRef(onClose)
  useEffect(() => { closeRef.current = onClose })

  useEffect(() => {
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null
    const root = ref.current
    const first = root?.querySelector<HTMLElement>('[data-autofocus]') ?? root
    first?.focus({ preventScroll: true })
    return () => { if (opener?.isConnected) opener.focus({ preventScroll: true }) }
  }, [ref])

  useEffect(() => {
    if (!closeOnEscape) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape' && !e.defaultPrevented) closeRef.current() }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [closeOnEscape])

  return useCallback((e: React.KeyboardEvent) => {
    if (!modal || e.key !== 'Tab' || !ref.current) return
    const items = Array.from(ref.current.querySelectorAll<HTMLElement>(FOCUSABLE))
    if (!items.length) return
    const firstItem = items[0]
    const lastItem = items[items.length - 1]
    if (e.shiftKey && (document.activeElement === firstItem || document.activeElement === ref.current)) { e.preventDefault(); lastItem.focus() }
    else if (!e.shiftKey && document.activeElement === lastItem) { e.preventDefault(); firstItem.focus() }
  }, [modal, ref])
}
