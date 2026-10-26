'use client'

import Link from 'next/link'
import { useLayoutEffect, useRef } from 'react'

/**
 * A segmented control: a recessed track with one raised pill that slides to the
 * chosen segment. Adapted from Opensource UI's Segmented Control (MIT, see
 * THIRD_PARTY_NOTICES.md). Pass `options` + `onChange` for a value picker, or
 * `links` for navigation between sibling pages.
 */
type Props<T extends string> =
  | { label: string; options: { value: T; label: string }[]; value: T; onChange: (value: T) => void; links?: undefined }
  | { label: string; links: { href: string; label: string }[]; value: string; options?: undefined; onChange?: undefined }

export default function Segmented<T extends string>(props: Props<T>) {
  const trackRef = useRef<HTMLDivElement>(null)
  const pillRef = useRef<HTMLSpanElement>(null)
  const items = props.links ? props.links.map(link => ({ key: link.href, label: link.label })) : props.options.map(option => ({ key: option.value, label: option.label }))
  const index = Math.max(0, items.findIndex(item => item.key === props.value))

  useLayoutEffect(() => {
    const place = () => {
      const track = trackRef.current, pill = pillRef.current
      const el = track?.children[index + 1] as HTMLElement | undefined
      if (!track || !pill || !el) return
      pill.style.width = `${el.offsetWidth}px`
      pill.style.transform = `translateX(${el.offsetLeft}px)`
    }
    place()
    window.addEventListener('resize', place)
    document.fonts?.ready.then(place).catch(() => {})
    return () => window.removeEventListener('resize', place)
  }, [index, items.length])

  const move = (e: React.KeyboardEvent<HTMLDivElement>) => {
    if (props.links || (e.key !== 'ArrowRight' && e.key !== 'ArrowLeft')) return
    e.preventDefault()
    const next = (index + (e.key === 'ArrowRight' ? 1 : -1) + items.length) % items.length
    props.onChange(props.options[next].value)
    requestAnimationFrame(() => (trackRef.current?.children[next + 1] as HTMLElement | undefined)?.focus())
  }

  return <div className="seg" ref={trackRef} role={props.links ? undefined : 'tablist'} aria-label={props.label} onKeyDown={move}>
    <span className="seg-pill" ref={pillRef} aria-hidden="true" />
    {props.links
      ? props.links.map((link, i) => <Link key={link.href} href={link.href} className="seg-item" data-on={i === index ? 'true' : undefined} aria-current={i === index ? 'page' : undefined}>{link.label}</Link>)
      : props.options.map((option, i) => <button
        key={option.value}
        type="button"
        role="tab"
        className="seg-item"
        aria-selected={i === index}
        tabIndex={i === index ? 0 : -1}
        data-on={i === index ? 'true' : undefined}
        onClick={() => props.onChange(option.value)}
      >{option.label}</button>)}
  </div>
}
