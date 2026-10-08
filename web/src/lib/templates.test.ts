import { describe, expect, it } from 'vitest'

import type { TemplateBody } from '@/api'

import { bodyToSimple, emptySimple, nextFreeOnu, simpleToBody } from './templates'

// La plantilla que sale de copiar la ONU 3 de la V1600G0-B del laboratorio.
const COPIED: TemplateBody = {
  auth_profile: 'default',
  onu_profile: 'default',
  tconts: [{ id: 1, name: 'INTERNET', dba: 'default1' }],
  gemports: [{ id: 1, tcont: 1, name: 'INTERNET', limit_down: 'default' }],
  services: [{ name: 'ser_1', gemport: 1, vlan: 111 }],
  service_ports: [{ id: 1, gemport: 1, user_vlan: 111, vlan: 111, cos: 0 }],
  wan: { index: 1, mtu: 1492, vlan: 111, cos: 0, nat: true, binds: ['lan1', 'lan2', 'ssid1'] },
  wifi: { ssid_index: 1 },
  management: {
    firewall: 'low',
    ping_wan: true,
    wan_access: ['telnet', 'http', 'https'],
    admin_user: 'soporte',
    user_account: null,
  },
}

describe('plantilla simple', () => {
  it('una ONU copiada cabe en el formulario y vuelve igual', () => {
    const simple = bodyToSimple(COPIED)
    expect(simple).not.toBeNull()
    expect(simple?.vlan).toBe(111)
    expect(simpleToBody(simple!)).toEqual(COPIED)
  })

  it('lo que no cabe se edita como JSON', () => {
    expect(bodyToSimple({ ...COPIED, services: [] })).toBeNull()
    const twoVlans = {
      ...COPIED,
      service_ports: [{ id: 1, gemport: 1, user_vlan: 10, vlan: 111, cos: 0 }],
    }
    expect(bodyToSimple(twoVlans)).toBeNull()
  })

  it('bridge sin PPPoE ni WiFi', () => {
    const body = simpleToBody({ ...emptySimple(), pppoe: false, wifi: false, limitDown: '' })
    expect(body.wan).toBeNull()
    expect(body.wifi).toBeNull()
    expect(body.gemports[0]?.limit_down).toBeNull()
  })

  it('gestión remota: solo lo marcado queda abierto desde internet', () => {
    const body = simpleToBody({
      ...emptySimple(),
      management: true,
      firewall: '',
      pingWan: false,
      wanAccess: ['https', 'http'],
    })
    expect(body.management).toMatchObject({
      firewall: null,
      ping_wan: false,
      wan_access: ['http', 'https'],
      admin_user: 'admin',
      user_account: null,
    })
    expect(simpleToBody(emptySimple()).management).toBeNull()
    expect(bodyToSimple({ ...COPIED, management: null })?.management).toBe(false)
    const both = simpleToBody({ ...emptySimple(), management: true, userAccount: true })
    expect(both.management?.user_account).toBe('user')
    const none = simpleToBody({ ...emptySimple(), management: true, accounts: false })
    expect(none.management?.admin_user).toBeNull()
    expect(none.management?.user_account).toBeNull()
  })

  it('siguiente ONU libre', () => {
    expect(nextFreeOnu([1, 2, 4])).toBe(3)
    expect(nextFreeOnu([])).toBe(1)
    expect(nextFreeOnu(Array.from({ length: 128 }, (_, i) => i + 1))).toBeNull()
  })
})
