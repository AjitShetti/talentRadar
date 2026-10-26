/** How a stored seniority value (domain.enums.SeniorityLevel) reads to a person. */
const LABELS: Record<string, string> = {
  intern: 'Intern',
  junior: 'Junior',
  mid: 'Mid level',
  senior: 'Senior',
  lead: 'Lead',
  principal: 'Principal',
  staff: 'Staff',
  director: 'Director',
  vp: 'VP',
  c_level: 'C-level',
}

export function seniorityLabel(value: string | null | undefined): string | null {
  if (!value) return null
  return LABELS[value] ?? value.replace(/_/g, ' ')
}
