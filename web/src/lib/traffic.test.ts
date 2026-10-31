import { describe, expect, it } from 'vitest'

import type { OnuTrafficHour } from '@/api'

import { byDay, byHour, formatBps, formatBytes, niceCeiling, totals } from './traffic'

function hour(at: Date, down: number, up = 0, peak = 0): OnuTrafficHour {
  return {
    hour: at.toISOString(),
    down_bytes: down,
    up_bytes: up,
    peak_down_bps: peak,
    peak_up_bps: 0,
  }
}

const now = new Date(2026, 9, 31, 15, 20).getTime() // sábado 31 de octubre, 3:20 p. m. (local)

describe('consumo', () => {
  it('escribe bytes y velocidades en unidades decimales', () => {
    expect(formatBytes(1_234_000_000)).toBe('1,2 GB')
    expect(formatBytes(350_400_000)).toBe('350 MB')
    expect(formatBytes(999)).toBe('999 B')
    expect(formatBps(3_400_000)).toBe('3,4 Mbps')
    expect(formatBps(850_000)).toBe('850 kbps')
    expect(formatBps(null)).toBe('—')
  })

  it('agrupa por día local y rellena los días sin datos', () => {
    const rows = [
      hour(new Date(2026, 9, 31, 9), 100, 10, 8_000),
      hour(new Date(2026, 9, 31, 14), 50, 5, 9_000),
      hour(new Date(2026, 9, 29, 23), 7),
    ]
    const days = byDay(rows, 3, now)
    expect(days.map((d) => [d.down, d.up])).toEqual([
      [7, 0],
      [0, 0],
      [150, 15],
    ])
    expect(days[2]?.peakDown).toBe(9_000)
  })

  it('agrupa por hora, la última es la actual', () => {
    const hours = byHour(
      [hour(new Date(2026, 9, 31, 15), 40), hour(new Date(2026, 9, 31, 13), 2)],
      3,
      now,
    )
    expect(hours.map((h) => h.down)).toEqual([2, 0, 40])
  })

  it('suma hoy, la semana y el mes', () => {
    const rows = [
      hour(new Date(2026, 9, 31, 8), 10, 1),
      hour(new Date(2026, 9, 26, 8), 20, 2), // hace 5 días
      hour(new Date(2026, 9, 2, 8), 40, 4), // este mes, no esta semana
      hour(new Date(2026, 8, 30, 8), 80, 8), // el mes pasado
    ]
    expect(totals(rows, now)).toEqual({
      today: { down: 10, up: 1 },
      week: { down: 30, up: 3 },
      month: { down: 70, up: 7 },
    })
  })

  it('redondea el tope del eje', () => {
    expect(niceCeiling(0)).toBe(1)
    expect(niceCeiling(3_200_000_000)).toBe(5_000_000_000)
    expect(niceCeiling(120)).toBe(200)
    expect(niceCeiling(1000)).toBe(1000)
  })
})
