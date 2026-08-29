/**
 * Deterministic, rule-based ATS-friendliness score for a structured resume
 * document. Runs entirely client-side (no LLM, no network) so it can
 * recompute on every keystroke via useMemo in the editor — genuinely live,
 * not debounced. Each check contributes a weighted fraction to the total;
 * anything short of full marks surfaces as a suggestion, worst offenders first.
 */

import { ResumeDocument, ResumeItem, ResumeSection } from './api'

export type AtsScoreResult = {
  score: number
  suggestions: string[]
}

const ACTION_VERBS = new Set([
  'accelerated', 'achieved', 'analyzed', 'architected', 'automated', 'built',
  'championed', 'coordinated', 'created', 'decreased', 'delivered', 'deployed',
  'designed', 'developed', 'directed', 'drove', 'engineered', 'established',
  'executed', 'expanded', 'facilitated', 'founded', 'grew', 'implemented',
  'improved', 'increased', 'initiated', 'integrated', 'introduced', 'launched',
  'led', 'leveraged', 'maintained', 'managed', 'mentored', 'migrated',
  'negotiated', 'optimized', 'orchestrated', 'organized', 'overhauled',
  'owned', 'partnered', 'pioneered', 'planned', 'presented', 'prioritized',
  'produced', 'programmed', 'published', 'reduced', 'refactored', 'released',
  'researched', 'resolved', 'restructured', 'revamped', 'scaled', 'shipped',
  'simplified', 'solved', 'spearheaded', 'standardized', 'streamlined',
  'strengthened', 'supervised', 'supported', 'tested', 'trained',
  'transformed', 'upgraded',
])

const QUANTIFIER_RE = /\d|%|\$|₹|€|£/

function words(text: string | undefined | null): string[] {
  return (text || '').trim().split(/\s+/).filter(Boolean)
}

function firstWord(text: string): string {
  return words(text)[0]?.toLowerCase().replace(/[^a-z]/g, '') || ''
}

function allBullets(sections: ResumeSection[]): string[] {
  const bullets: string[] = []
  for (const section of sections) {
    if (section.type === 'summary' || section.type === 'skills') continue
    for (const item of section.items) {
      for (const b of item.bullets || []) if (b && b.trim()) bullets.push(b.trim())
    }
  }
  return bullets
}

function itemHasContent(item: ResumeItem, type: ResumeSection['type']): boolean {
  const textFields = Object.entries(item).filter(([k]) => k !== 'bullets' && k !== 'items')
  const hasText = textFields.some(([, v]) => typeof v === 'string' && v.trim().length > 0)
  const hasList = (item.bullets && item.bullets.some(b => b.trim())) || (item.items && item.items.some(i => i.trim()))
  return hasText || Boolean(hasList)
}

type Check = { weight: number; fraction: number; suggestion: string | null }

export function computeAtsScore(doc: ResumeDocument): AtsScoreResult {
  const visibleSections = doc.sections.filter(s => s.visible)
  const byType = (t: ResumeSection['type']) => visibleSections.find(s => s.type === t)

  const checks: Check[] = []

  // Contact info completeness
  const contactFields = [doc.personal.full_name, doc.personal.email, doc.personal.phone, doc.personal.location]
  const contactFraction = contactFields.filter(f => f && f.trim()).length / contactFields.length
  checks.push({
    weight: 10, fraction: contactFraction,
    suggestion: contactFraction < 1 ? 'Fill in your full name, email, phone, and location — recruiters and ATS parsers both look for complete contact info.' : null,
  })

  // Links (LinkedIn / GitHub / portfolio)
  const hasLink = doc.personal.links.some(l => l.url && l.url.trim())
  checks.push({
    weight: 5, fraction: hasLink ? 1 : 0,
    suggestion: hasLink ? null : 'Add a LinkedIn, GitHub, or portfolio link so reviewers can verify your work.',
  })

  // Professional summary
  const summarySection = byType('summary')
  const summaryText = summarySection?.items[0]?.text || ''
  const summaryWords = words(summaryText).length
  const summaryFraction = !summarySection ? 0 : summaryWords === 0 ? 0 : summaryWords < 10 ? 0.4 : summaryWords > 70 ? 0.6 : 1
  checks.push({
    weight: 8, fraction: summaryFraction,
    suggestion: summaryFraction < 1
      ? (summaryWords === 0 ? 'Add a 2-3 sentence professional summary at the top of your resume.' : 'Aim for a 10-60 word professional summary — long enough to say who you are, short enough to be read.')
      : null,
  })

  // Experience presence
  const experienceSection = byType('experience')
  const experienceItems = (experienceSection?.items || []).filter(i => itemHasContent(i, 'experience'))
  checks.push({
    weight: 12, fraction: experienceItems.length > 0 ? 1 : 0,
    suggestion: experienceItems.length > 0 ? null : 'Add at least one work experience entry.',
  })

  // Experience bullets present
  const itemsWithBullets = experienceItems.filter(i => (i.bullets || []).some(b => b.trim()))
  const bulletsFraction = experienceItems.length === 0 ? 0 : itemsWithBullets.length / experienceItems.length
  checks.push({
    weight: 10, fraction: bulletsFraction,
    suggestion: bulletsFraction < 1 ? 'Add bullet points describing your impact under each role — a title and company alone won’t parse well.' : null,
  })

  // Action verbs
  const bullets = allBullets(visibleSections)
  const actionVerbCount = bullets.filter(b => ACTION_VERBS.has(firstWord(b))).length
  const actionVerbFraction = bullets.length === 0 ? 0 : actionVerbCount / bullets.length
  checks.push({
    weight: 15, fraction: actionVerbFraction,
    suggestion: bullets.length > 0 && actionVerbFraction < 0.8
      ? `${bullets.length - actionVerbCount} of your ${bullets.length} bullet${bullets.length === 1 ? '' : 's'} don’t start with a strong action verb — try "Led", "Built", "Reduced", "Launched"…`
      : null,
  })

  // Quantified impact
  const quantifiedCount = bullets.filter(b => QUANTIFIER_RE.test(b)).length
  const quantifiedFraction = bullets.length === 0 ? 0 : quantifiedCount / bullets.length
  checks.push({
    weight: 15, fraction: quantifiedFraction,
    suggestion: bullets.length > 0 && quantifiedFraction < 0.5
      ? 'Quantify your impact with numbers where you can — e.g. "40% faster", "$2M saved", "10k users".'
      : null,
  })

  // Bullet length sanity (not a fragment, not a wall of text)
  const wellSizedCount = bullets.filter(b => { const n = words(b).length; return n >= 6 && n <= 30 }).length
  const bulletLengthFraction = bullets.length === 0 ? 0 : wellSizedCount / bullets.length
  checks.push({
    weight: 8, fraction: bulletLengthFraction,
    suggestion: bullets.length > 0 && bulletLengthFraction < 0.7
      ? 'Keep bullets to roughly one line — long enough to show impact, short enough to scan.'
      : null,
  })

  // Skills
  const skillsSection = byType('skills')
  const skillCount = (skillsSection?.items || []).reduce((n, i) => n + (i.items?.filter(s => s.trim()).length || 0), 0)
  const skillsFraction = Math.min(skillCount / 3, 1)
  checks.push({
    weight: 10, fraction: skillsFraction,
    suggestion: skillsFraction < 1 ? 'List at least 3-5 relevant technical skills so keyword-matching ATS systems can find them.' : null,
  })

  // Education
  const educationItems = (byType('education')?.items || []).filter(i => itemHasContent(i, 'education'))
  checks.push({
    weight: 7, fraction: educationItems.length > 0 ? 1 : 0,
    suggestion: educationItems.length > 0 ? null : 'Add your education details.',
  })

  // No empty visible sections left dangling
  const otherSections = visibleSections.filter(s => !['summary', 'experience', 'skills', 'education'].includes(s.type))
  const nonEmptyOther = otherSections.filter(s => s.items.some(i => itemHasContent(i, s.type)))
  const emptySections = otherSections.length - nonEmptyOther.length
  const tidyFraction = otherSections.length === 0 ? 1 : nonEmptyOther.length / otherSections.length
  checks.push({
    weight: 5, fraction: tidyFraction,
    suggestion: emptySections > 0 ? `${emptySections} visible section${emptySections === 1 ? ' is' : 's are'} empty — fill it in or hide it with the eye icon.` : null,
  })

  const totalWeight = checks.reduce((sum, c) => sum + c.weight, 0)
  const earned = checks.reduce((sum, c) => sum + c.weight * c.fraction, 0)
  const score = totalWeight === 0 ? 0 : Math.round((earned / totalWeight) * 100)

  const suggestions = checks
    .filter(c => c.suggestion)
    .sort((a, b) => b.weight * (1 - b.fraction) - a.weight * (1 - a.fraction))
    .map(c => c.suggestion as string)
    .slice(0, 5)

  return { score, suggestions }
}
