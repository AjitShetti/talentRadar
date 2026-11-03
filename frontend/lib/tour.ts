import type { Routine } from '@/lib/routine'

/**
 * The first-run tour: where to apply, connect and prepare, then the day line. Pure rules, so
 * e2e/routine.rules.spec.ts can run them with no browser. components/Tour.tsx draws it.
 * Nothing here is sent anywhere: the tour only points at things that are already on the page.
 */

/** Dispatched on `window` to open the tour from the top, by the account menu's "Take the tour". */
export const TOUR_EVENT = 'tr:tour'

/** What a step points at: `data-tour` on the Job hunt and Prep nav buttons, and on the day line. */
export type TourTarget = 'jobs' | 'prep' | 'day'

export const TOUR_STEPS: { target: TourTarget; title: string; body: string }[] = [
  { target: 'jobs', title: 'Find roles', body: 'Search live openings across the boards, then apply from the role.' },
  { target: 'jobs', title: 'Applications', body: 'Every role you go for sits here, from saved to offer. Under Job hunt.' },
  { target: 'jobs', title: 'Outreach and Companies', body: 'Who to reach at each company, and when to follow up. Also under Job hunt.' },
  { target: 'prep', title: 'Practice and mock interviews', body: 'One problem a day, a weekly contest, and interviews scored answer by answer.' },
  { target: 'day', title: 'Your day', body: 'This line is your routine: what each hour is for. Tick blocks off under Your day on the Overview, and change the times whenever you like.' },
]

/** Pages that are not the product: the tour waits for someone who is using it. */
const NOT_THE_PRODUCT = ['/onboarding', '/login', '/signup']

/**
 * Whether to open the tour without being asked: the routine has loaded, the person finished
 * onboarding, they have not seen the tour, and they are on a page of the product.
 */
export function shouldAutoStart(routine: Routine | null, path: string): boolean {
  if (!routine || !routine.onboarding_completed || routine.tour_seen) return false
  return !NOT_THE_PRODUCT.some(page => path === page || path.startsWith(page + '/'))
}

/** A rectangle in viewport pixels. */
export type Box = { left: number; top: number; width: number; height: number }

/** The cut-out leaves this much of the page undimmed around the target. */
export const SPOT_PAD = 6
/** The card keeps this far from every edge of the viewport. */
export const GUTTER = 16
/** The card sits this far below the cut-out. */
export const CARD_GAP = 12
/** Under this width the card is a sheet at the bottom, as the filters' sheet is. */
export const PINNED_BELOW = 560

/** The undimmed part of the page: the target and `SPOT_PAD` around it. None without a target. */
export function spotlight(target: Box | null): Box | null {
  if (!target) return null
  return {
    left: target.left - SPOT_PAD,
    top: target.top - SPOT_PAD,
    width: target.width + 2 * SPOT_PAD,
    height: target.height + 2 * SPOT_PAD,
  }
}

/** `value` held between `min` and `max`; where they cross (a card wider than the room) `min` wins. */
const clamp = (value: number, min: number, max: number) => Math.max(min, Math.min(value, max))

/**
 * Where the card goes. Pinned to the bottom of a narrow viewport. Otherwise below the
 * cut-out and centred on the target, held inside the viewport with a gutter; with no target,
 * centred on the viewport.
 */
export function placeCard(
  target: Box | null,
  card: { width: number; height: number },
  view: { width: number; height: number },
): { pinned: boolean; left: number; top: number } {
  if (view.width < PINNED_BELOW) return { pinned: true, left: 0, top: 0 }
  const cut = spotlight(target)
  const left = cut ? cut.left + cut.width / 2 - card.width / 2 : (view.width - card.width) / 2
  const top = cut ? cut.top + cut.height + CARD_GAP : (view.height - card.height) / 2
  return {
    pinned: false,
    left: Math.round(clamp(left, GUTTER, view.width - GUTTER - card.width)),
    top: Math.round(clamp(top, GUTTER, view.height - GUTTER - card.height)),
  }
}
