import { describe, expect, it } from 'vitest'

import { alarmWhere, duration, formatDbm, signalLevel } from './monitoring'

describe('monitoreo', () => {
  it('nombra la señal según la ventana GPON', () => {
    expect(signalLevel(-19.5)).toEqual({ label: 'Buena', tone: 'success' })
    expect(signalLevel(-25.4).label).toBe('Baja')
    expect(signalLevel(-28.4)).toEqual({ label: 'Muy baja', tone: 'danger' })
    expect(signalLevel(-7).label).toBe('Muy alta')
    expect(signalLevel(null).label).toBe('Sin lectura')
  })

  it('formatea dBm y dice dónde está la alarma', () => {
    expect(formatDbm(-19.5)).toBe('-19,50 dBm')
    expect(formatDbm(null)).toBe('—')
    expect(alarmWhere({ olt_name: 'VIOTA', pon: 0, onu: 0 })).toBe('VIOTA')
    expect(alarmWhere({ olt_name: 'VIOTA', pon: 2, onu: 0 })).toBe('VIOTA · PON 2')
    expect(alarmWhere({ olt_name: 'VIOTA', pon: 2, onu: 7 })).toBe('VIOTA · PON 2 · ONU 7')
  })

  it('dice cuánto lleva', () => {
    const now = Date.parse('2026-10-13T12:00:00Z')
    expect(duration('2026-10-13T11:59:40Z', now)).toBe('menos de 1 min')
    expect(duration('2026-10-13T11:55:00Z', now)).toBe('5 min')
    expect(duration('2026-10-13T08:40:00Z', now)).toBe('3 h 20 min')
    expect(duration('2026-10-11T08:00:00Z', now)).toBe('2 d 4 h')
  })
})
