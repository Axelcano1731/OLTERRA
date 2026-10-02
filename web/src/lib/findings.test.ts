import { describe, expect, it } from 'vitest'

import type { Finding } from '@/api'

import { filterFindings, findingsToCsv, groupFindings, refChips, SEVERITIES } from './findings'

function finding(overrides: Partial<Finding>): Finding {
  return {
    kind: 'onu_sin_cliente',
    severity: 'advertencia',
    title: 'ONU autorizada sin cliente',
    detail: 'La ONU VSOL0000A004 no está ligada a ningún cliente.',
    suggestion: 'Ligarla a su cliente.',
    refs: {},
    ...overrides,
  }
}

const sample: Finding[] = [
  finding({ refs: { serial: 'VSOL0000A004' } }),
  finding({ refs: { serial: 'VSOL0000B001' } }),
  finding({
    kind: 'pppoe_digitacion',
    severity: 'error',
    title: 'Usuario PPPoE con error de digitación',
    detail: 'Aparece "maria _demo", parecido a maría_demo.',
    suggestion: 'Dejar el mismo usuario exacto.',
    refs: { pppoe: 'maria _demo', expected: 'maría_demo' },
  }),
  finding({ kind: 'serial_por_registrar', severity: 'info', title: 'Serial por registrar' }),
]

describe('groupFindings', () => {
  it('ordena por gravedad y luego por cantidad', () => {
    const groups = groupFindings(sample)
    expect(groups.map((g) => [g.kind, g.findings.length])).toEqual([
      ['pppoe_digitacion', 1],
      ['onu_sin_cliente', 2],
      ['serial_por_registrar', 1],
    ])
  })
})

describe('filterFindings', () => {
  const all = new Set(SEVERITIES)

  it('filtra por gravedad', () => {
    expect(filterFindings(sample, new Set(['error'] as const), '')).toHaveLength(1)
  })

  it('busca sin importar tildes ni mayúsculas, también en las referencias', () => {
    expect(filterFindings(sample, all, 'MARIA_DEMO')).toHaveLength(1)
    expect(filterFindings(sample, all, 'b001')).toHaveLength(1)
    expect(filterFindings(sample, all, '  ')).toHaveLength(sample.length)
  })
})

describe('refChips', () => {
  it('sigue el orden del CSV y salta los vacíos', () => {
    const chips = refChips(finding({ refs: { router: 'BNG', serial: 'X1', pon: 0, onu: null } }))
    expect(chips).toEqual([
      { label: 'Serial', value: 'X1' },
      { label: 'PON', value: '0' },
      { label: 'Router', value: 'BNG' },
    ])
  })
})

describe('findingsToCsv', () => {
  it('lleva BOM, las columnas del CLI y escapa comas y comillas', () => {
    const csv = findingsToCsv([sample[2]!])
    expect(csv.startsWith('\uFEFFseveridad,tipo,titulo,detalle,accion_sugerida,customer_id')).toBe(
      true,
    )
    expect(csv).toContain('"Aparece ""maria _demo"", parecido a maría_demo."')
    expect(csv.trimEnd().split('\r\n')).toHaveLength(2)
  })
})
