/**
 * What brings someone to TalentRadar. Onboarding asks it first and Settings changes it later;
 * the profile stores the sentence as `career_goals`, which the copilot reads.
 */

export type Goal = 'apply' | 'prepare' | 'both'

/** Where each goal lands the user, and the sentence saved as their career goal. */
export const GOALS: { value: Goal; title: string; detail: string; href: string; sentence: string }[] = [
  { value: 'apply', title: 'Land a job', detail: 'Openings, who to contact at each company, and a tracker for every application.', href: '/search', sentence: 'Land a job: find openings, reach the right people and track applications.' },
  { value: 'prepare', title: 'Get interview-ready', detail: 'Mock interviews, one problem a day and a weekly contest.', href: '/practice', sentence: 'Get interview-ready: mock interviews, daily practice and a weekly contest.' },
  { value: 'both', title: 'Both', detail: 'Start from a day plan that mixes applying and practice.', href: '/dashboard', sentence: 'Land a job and get interview-ready at the same time.' },
]

/** The goal a stored sentence was saved from, or null for one written some other way. */
export const goalFromSentence = (sentence: unknown): Goal | null =>
  GOALS.find(goal => goal.sentence === sentence)?.value ?? null
