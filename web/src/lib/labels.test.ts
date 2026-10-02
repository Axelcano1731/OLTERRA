import { describe, expect, it } from 'vitest'

import { actorLabel, planStatus } from './labels'

describe('labels', () => {
  it('muestra el actor de la bitácora en palabras', () => {
    expect(actorLabel('llave:local')).toBe('Llave local')
    expect(actorLabel('llave:a:b')).toBe('Llave a:b')
    expect(actorLabel('sistema')).toBe('sistema')
  })

  it('un estado de plan desconocido se muestra tal cual', () => {
    expect(planStatus('ok')).toEqual({ label: 'Completo', tone: 'success' })
    expect(planStatus('nuevo')).toEqual({ label: 'nuevo', tone: 'neutral' })
  })
})
