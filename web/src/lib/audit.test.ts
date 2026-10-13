import { describe, expect, it } from 'vitest'

import { auditAction, auditDetails } from './audit'
import { actorLabel } from './labels'

describe('bitácora', () => {
  it('nombra las acciones en palabras del operador', () => {
    expect(auditAction('olt.password_change')).toBe('Cambió la clave de una OLT')
    expect(auditAction('algo.nuevo')).toBe('algo.nuevo')
  })

  it('con antes y después muestra solo lo que cambió', () => {
    const details = auditDetails({
      before: { model: null, firmware: 'V1.4.8R', ssh_port: 22 },
      after: { model: 'V1600G0-B', firmware: 'V1.4.8R', ssh_port: 22, credenciales: [] },
    })
    expect(details).toEqual([{ label: 'Modelo', value: 'V1600G0-B', before: '—' }])
    const keys = auditDetails({
      before: { model: 'x' },
      after: { model: 'x', credenciales: ['password'] },
    })
    expect(keys).toEqual([{ label: 'Credenciales', value: 'clave' }])
  })

  it('sin antes muestra los datos, sin identificadores internos', () => {
    const details = auditDetails({
      before: null,
      after: { job_id: 'x', username: 'admin', changed: ['login', 'enable'], saved: true },
    })
    expect(details).toEqual([
      { label: 'Usuario', value: 'admin' },
      { label: 'Claves cambiadas', value: 'acceso, enable' },
      { label: 'Guardada en la OLT', value: 'Sí' },
    ])
  })

  it('no muestra el cuerpo técnico de un plan ni datos vacíos', () => {
    const details = auditDetails({
      before: null,
      after: { name: 'Hogar 100', body: { wan: { vlan: 100 } }, nat_ip: null, claves_onu: [] },
    })
    expect(details).toEqual([{ label: 'Nombre', value: 'Hogar 100' }])
  })

  it('dice quién fue', () => {
    expect(actorLabel('usuario:AxelCano')).toBe('AxelCano')
    expect(actorLabel('olterra:respaldo')).toBe('Olterra (automático)')
  })
})
