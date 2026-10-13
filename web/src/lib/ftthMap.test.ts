import { describe, expect, it } from 'vitest'

import { bounds, boxState, circle, formatMeters, nextName } from './ftthMap'

describe('mapa FTTH', () => {
  it('el estado de la caja: caídos primero, luego llena o con puertos', () => {
    expect(boxState({ kind: 'nap', ports: 8, used: 3, down: 1 })).toBe('down')
    expect(boxState({ kind: 'nap', ports: 8, used: 8, down: 0 })).toBe('full')
    expect(boxState({ kind: 'nap', ports: 8, used: 2, down: 0 })).toBe('free')
    expect(boxState({ kind: 'ceo', ports: 0, used: 0, down: 0 })).toBe('passive')
  })

  it('formatea distancias', () => {
    expect(formatMeters(118.4)).toBe('118 m')
    expect(formatMeters(1234)).toBe('1,23 km')
  })

  it('el círculo de factibilidad tiene el radio pedido', () => {
    const points = circle(4.4386, -74.5226, 300)
    expect(points).toHaveLength(65)
    const [lng, lat] = points[16] ?? [0, 0] // a 90°: hacia el norte
    expect((lat - 4.4386) * 111_320).toBeCloseTo(300, 0)
    expect(lng).toBeCloseTo(-74.5226, 6)
  })

  it('encuadra todo lo dibujado y nombra la siguiente caja', () => {
    const area = bounds({
      olts: [{ id: 'o', name: 'OLT', status: 'online', lat: 4.4, lng: -74.5 }],
      boxes: [],
      poles: [],
      cables: [
        { id: 'c', name: 'T', fibers: 12, coords: [[-74.6, 4.5]], length_m: 0, notes: null },
      ],
    })
    expect(area).toEqual([
      [-74.6, 4.4],
      [-74.5, 4.5],
    ])
    expect(bounds({ olts: [], boxes: [], poles: [], cables: [] })).toBeNull()
    expect(nextName('NAP', ['NAP-01', 'NAP-02'])).toBe('NAP-03')
  })
})
