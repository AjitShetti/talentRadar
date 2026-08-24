'use client'

export type SuggestionProfile = {
  target_roles?: unknown
  target_locations?: unknown
  skills?: unknown
  headline?: unknown
}

/** Curated prompts covering a spread of role families, seniorities, locations and work styles. */
const GENERAL_POOL = [
  'Remote product design roles with AI experience',
  'Senior UX researcher positions in Bengaluru',
  'Frontend jobs at early-stage startups',
  'Backend engineers working with Python and Kafka',
  'Data science roles that do not require a PhD',
  'Engineering manager jobs with a hybrid schedule',
  'Machine learning roles in healthcare',
  'Full-stack positions at Series A fintech companies',
  'DevOps roles using Kubernetes and AWS in Hyderabad',
  'Product manager roles for developer tools',
  'QA automation jobs open to remote candidates',
  'Mobile developers building React Native apps',
  'Staff engineer roles with a strong design culture',
  'Security engineering positions in Pune',
  'Analytics roles that use SQL and dbt',
  'Technical writer jobs at open-source companies',
  'Cloud architect roles with a senior compensation band',
  'Java backend roles in Chennai with remote flexibility',
  'Entry-level software roles that offer mentorship',
  'Platform engineering jobs at AI startups',
  'Node.js engineers at companies building payments',
  'Design system ownership for a senior designer',
  'Data engineering roles moving toward ML infrastructure',
  'Contract frontend work with TypeScript',
  'Roles where I can move from support into product',
  'Companies hiring for LLM and RAG experience',
]

function textList(value: unknown): string[] {
  if (!Array.isArray(value)) return []
  return value
    .map(item => typeof item === 'string' ? item : (item as { name?: unknown })?.name)
    .filter((item): item is string => typeof item === 'string' && item.trim().length > 0)
    .map(item => item.trim())
}

function lower(value: string) {
  // Keep acronyms (UX, SRE, QA) intact; only downcase ordinary Title Case words.
  return value === value.toUpperCase() ? value : value.toLowerCase()
}

/** Prompts written from the user's own profile, so the panel reflects their actual search. */
function personalisedPool(profile: SuggestionProfile | null): string[] {
  if (!profile) return []
  const roles = textList(profile.target_roles)
  const locations = textList(profile.target_locations)
  const skills = textList(profile.skills)
  const out: string[] = []

  for (const role of roles) {
    out.push(`Remote ${lower(role)} roles open right now`)
    for (const location of locations) out.push(`${role} roles in ${location}`)
    for (const skill of skills.slice(0, 3)) out.push(`${role} positions that use ${skill}`)
    out.push(`${role} jobs at early-stage startups`)
  }
  for (const skill of skills.slice(0, 4)) {
    if (!roles.length) out.push(`Roles that need strong ${skill} skills`)
    out.push(`Teams building with ${skill}`)
  }
  for (const location of locations) {
    if (!roles.length) out.push(`Senior engineering roles in ${location}`)
  }
  return out
}

function shuffle<T>(items: T[]): T[] {
  const copy = items.slice()
  for (let i = copy.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1))
    ;[copy[i], copy[j]] = [copy[j], copy[i]]
  }
  return copy
}

/**
 * Pick `count` suggestions, favouring profile-derived prompts and avoiding anything
 * currently on screen so a refresh always visibly changes the panel.
 *
 * Randomised, so it must be called from an effect or event handler — never during
 * render, which would desync the server and client markup.
 */
export function pickSuggestions(profile: SuggestionProfile | null, exclude: string[] = [], count = 3): string[] {
  const personal = shuffle(Array.from(new Set(personalisedPool(profile))))
  const general = shuffle(GENERAL_POOL)
  const ordered = [...personal.slice(0, Math.min(2, personal.length)), ...general, ...personal.slice(2)]

  const blocked = new Set(exclude)
  const picked: string[] = []
  for (const suggestion of ordered) {
    if (picked.length === count) break
    if (blocked.has(suggestion) || picked.includes(suggestion)) continue
    picked.push(suggestion)
  }
  // Only possible when the pool is smaller than `count` plus the excluded set.
  if (picked.length < count) for (const suggestion of ordered) {
    if (picked.length === count) break
    if (!picked.includes(suggestion)) picked.push(suggestion)
  }
  return picked
}
