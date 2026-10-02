import { describe, expect, it } from 'vitest'

import { fold, plural, timeAgo } from './format'

describe('format', () => {
  it('fold quita tildes y mayúsculas', () => {
    expect(fold('María JOSÉ Ñandú')).toBe('maria jose nandu')
  })

  it('plural', () => {
    expect(plural(1, 'ONU', 'ONUs')).toBe('1 ONU')
    expect(plural(1200, 'ONU', 'ONUs')).toBe('1.200 ONUs')
  })

  it('timeAgo', () => {
    const now = Date.parse('2026-10-01T12:00:00Z')
    expect(timeAgo('2026-10-01T11:59:40Z', now)).toBe('hace un momento')
    expect(timeAgo('2026-10-01T11:55:00Z', now)).toBe('hace 5 minutos')
    expect(timeAgo('2026-09-30T12:00:00Z', now)).toBe('ayer')
  })
})
