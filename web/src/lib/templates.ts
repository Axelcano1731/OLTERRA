import type { Management, TemplateBody, WanService } from '@/api'

/**
 * La forma corta de una plantilla: un T-CONT, un GEM, un servicio y un service-port en la
 * misma VLAN, como aprovisiona la mayoría de los ISP. Lo que no cabe aquí (dos VLAN, varios
 * GEM) se edita como JSON.
 */
export interface SimpleTemplate {
  authProfile: string
  onuProfile: string
  vlan: number
  dba: string
  limitDown: string
  cos: number
  pppoe: boolean
  mtu: number
  nat: boolean
  binds: string[]
  wifi: boolean
  /** Gestión remota: firewall y servicios abiertos desde internet. */
  management: boolean
  /** '' = no se toca el firewall. */
  firewall: Exclude<Management['firewall'], null> | ''
  pingWan: boolean
  wanAccess: WanService[]
}

export const WAN_SERVICES: { value: WanService; label: string }[] = [
  { value: 'http', label: 'HTTP (web)' },
  { value: 'https', label: 'HTTPS (web segura)' },
  { value: 'telnet', label: 'Telnet' },
  { value: 'ftp', label: 'FTP' },
]

/** El orden en que la API los devuelve (el de la OLT). */
const WAN_ORDER: WanService[] = ['telnet', 'ftp', 'http', 'https']

export const FIREWALL_LEVELS: { value: SimpleTemplate['firewall']; label: string }[] = [
  { value: 'low', label: 'Bajo' },
  { value: 'middle', label: 'Medio' },
  { value: 'high', label: 'Alto' },
  { value: '', label: 'No cambiar' },
]

export const UNI_PORTS = ['lan1', 'lan2', 'lan3', 'lan4', 'ssid1', 'ssid2', 'ssid3', 'ssid4']

export function emptySimple(): SimpleTemplate {
  return {
    authProfile: 'default',
    onuProfile: 'default',
    vlan: 100,
    dba: 'default1',
    limitDown: '',
    cos: 0,
    pppoe: true,
    mtu: 1492,
    nat: true,
    binds: ['lan1', 'lan2', 'lan3', 'lan4', 'ssid1'],
    wifi: true,
    management: false,
    firewall: 'low',
    pingWan: true,
    wanAccess: ['http', 'https'],
  }
}

export function simpleToBody(simple: SimpleTemplate): TemplateBody {
  return {
    auth_profile: simple.authProfile.trim(),
    onu_profile: simple.onuProfile.trim() || null,
    tconts: [{ id: 1, name: 'INTERNET', dba: simple.dba.trim() }],
    gemports: [{ id: 1, tcont: 1, name: 'INTERNET', limit_down: simple.limitDown.trim() || null }],
    services: [{ name: 'ser_1', gemport: 1, vlan: simple.vlan }],
    service_ports: [
      { id: 1, gemport: 1, user_vlan: simple.vlan, vlan: simple.vlan, cos: simple.cos },
    ],
    wan: simple.pppoe
      ? {
          index: 1,
          mtu: simple.mtu,
          vlan: simple.vlan,
          cos: simple.cos,
          nat: simple.nat,
          binds: [...simple.binds],
        }
      : null,
    wifi: simple.wifi ? { ssid_index: 1 } : null,
    management: simple.management
      ? {
          firewall: simple.firewall || null,
          ping_wan: simple.pingWan,
          wan_access: WAN_ORDER.filter((s) => simple.wanAccess.includes(s)),
        }
      : null,
  }
}

/** La forma corta de una plantilla, o null si no cabe (entonces se edita como JSON). */
export function bodyToSimple(body: TemplateBody): SimpleTemplate | null {
  const [tcont] = body.tconts
  const [gem] = body.gemports
  const [service] = body.services
  const [port] = body.service_ports
  if (
    body.tconts.length !== 1 ||
    body.gemports.length !== 1 ||
    body.services.length !== 1 ||
    body.service_ports.length !== 1 ||
    !tcont ||
    !gem ||
    !service ||
    !port
  ) {
    return null
  }
  const vlan = service.vlan
  const sameVlan =
    port.vlan === vlan && port.user_vlan === vlan && (!body.wan || body.wan.vlan === vlan)
  const standardIds =
    tcont.id === 1 && gem.id === 1 && gem.tcont === 1 && port.id === 1 && port.gemport === 1
  const standardNames =
    tcont.name === 'INTERNET' && gem.name === 'INTERNET' && service.name === 'ser_1'
  const wanStandard = !body.wan || (body.wan.index === 1 && body.wan.cos === port.cos)
  if (!sameVlan || !standardIds || !standardNames || !wanStandard || service.gemport !== 1) {
    return null
  }
  if (body.wifi && body.wifi.ssid_index !== 1) return null
  const management = body.management ?? null
  return {
    authProfile: body.auth_profile,
    onuProfile: body.onu_profile ?? '',
    vlan,
    dba: tcont.dba,
    limitDown: gem.limit_down ?? '',
    cos: port.cos,
    pppoe: body.wan !== null,
    mtu: body.wan?.mtu ?? 1492,
    nat: body.wan?.nat ?? true,
    binds: body.wan ? [...body.wan.binds] : ['lan1', 'lan2', 'lan3', 'lan4', 'ssid1'],
    wifi: body.wifi !== null,
    management: management !== null,
    firewall: management ? (management.firewall ?? '') : 'low',
    pingWan: management?.ping_wan ?? true,
    wanAccess: management ? [...management.wan_access] : ['http', 'https'],
  }
}

/** Siguiente índice libre de ONU en un PON (1..128), sabiendo cuáles ya están ocupados. */
export function nextFreeOnu(used: Iterable<number>): number | null {
  const taken = new Set(used)
  for (let index = 1; index <= 128; index += 1) {
    if (!taken.has(index)) return index
  }
  return null
}
