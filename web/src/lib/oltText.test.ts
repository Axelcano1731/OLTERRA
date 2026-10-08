import { describe, expect, it } from 'vitest'

import { oltLabel, realModel } from './oltText'

describe('oltLabel (igual que clean_label del servidor)', () => {
  it('quita tildes y cambia espacios por guion bajo', () => {
    expect(oltLabel('José Pérez  Ñuñez', 64)).toBe('Jose_Perez_Nunez')
    expect(oltLabel('  Casa de Ana #2 ', 32)).toBe('Casa_de_Ana_2')
  })

  it('respeta el largo máximo sin dejar "_" al final', () => {
    expect(oltLabel('x'.repeat(80), 64)).toBe('x'.repeat(64))
    expect(oltLabel('abc def', 4)).toBe('abc')
  })

  it('sin letras ni números queda vacío', () => {
    expect(oltLabel('¿?¡!', 10)).toBe('')
  })
})

describe('realModel', () => {
  it('"NULL" del autofind no es un modelo', () => {
    expect(realModel('NULL')).toBeNull()
    expect(realModel(' n/a ')).toBeNull()
    expect(realModel(null)).toBeNull()
    expect(realModel('VSOLV824')).toBe('VSOLV824')
  })
})
