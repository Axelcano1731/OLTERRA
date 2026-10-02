import { describe, expect, it } from 'vitest'

import { MAX_TARGETS, parseOnus, parsePons, TargetError } from './targets'

describe('parsePons', () => {
  it('acepta comas, espacios y rangos, sin repetir', () => {
    expect(parsePons('1, 2 4-6;2')).toEqual([1, 2, 4, 5, 6])
    expect(parsePons('  ')).toEqual([])
  })

  it('rechaza lo que el driver no acepta', () => {
    expect(() => parsePons('0')).toThrow(TargetError)
    expect(() => parsePons('17')).toThrow(/1 a 16/)
    expect(() => parsePons('3-1')).toThrow(/al revés/)
    expect(() => parsePons('a')).toThrow(/no es un PON/)
  })
})

describe('parseOnus', () => {
  it('expande rangos dentro de un PON', () => {
    expect(parseOnus('1:5, 2:1-3')).toEqual(['1:5', '2:1', '2:2', '2:3'])
  })

  it('pone tope a la cantidad por consulta', () => {
    expect(() => parseOnus(`1:1-${MAX_TARGETS + 1}`)).toThrow(/máximo/)
    expect(() => parseOnus('1:129')).toThrow(/1 a 128/)
    expect(() => parseOnus('1/5')).toThrow(/PON:ONU/)
  })
})
