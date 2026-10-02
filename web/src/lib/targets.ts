// Listas de PON y ONU para las consultas. Los rangos son los de PARAM_TYPES en el driver;
// la API vuelve a validar todo.

export const MAX_PON = 16
export const MAX_ONU = 128
export const MAX_TARGETS = 64

export class TargetError extends Error {}

function number(text: string, max: number, what: string): number {
  const value = Number(text)
  if (!Number.isInteger(value) || value < 1 || value > max) {
    throw new TargetError(`${what} ${text} fuera de rango (1 a ${max})`)
  }
  return value
}

function expand(from: number, to: number, what: string): number[] {
  if (to < from) throw new TargetError(`Rango de ${what} al revés: ${from}-${to}`)
  return Array.from({ length: to - from + 1 }, (_, i) => from + i)
}

function items(text: string): string[] {
  return text
    .split(/[\s,;]+/)
    .map((item) => item.trim())
    .filter(Boolean)
}

function limited<T>(values: T[]): T[] {
  const unique = [...new Set(values)]
  if (unique.length > MAX_TARGETS) {
    throw new TargetError(`Son ${unique.length}; el máximo por consulta es ${MAX_TARGETS}`)
  }
  return unique
}

/** "1, 2, 4-6" → [1, 2, 4, 5, 6] */
export function parsePons(text: string): number[] {
  const pons: number[] = []
  for (const item of items(text)) {
    const match = /^(\d+)(?:-(\d+))?$/.exec(item)
    if (!match) throw new TargetError(`"${item}" no es un PON (ejemplo: 1, 2, 4-6)`)
    const from = number(match[1]!, MAX_PON, 'PON')
    pons.push(...(match[2] ? expand(from, number(match[2], MAX_PON, 'PON'), 'PON') : [from]))
  }
  return limited(pons)
}

/** "1:1, 2:5-8" → ["1:1", "2:5", "2:6", "2:7", "2:8"] */
export function parseOnus(text: string): string[] {
  const onus: string[] = []
  for (const item of items(text)) {
    const match = /^(\d+):(\d+)(?:-(\d+))?$/.exec(item)
    if (!match) throw new TargetError(`"${item}" no es PON:ONU (ejemplo: 1:5, 2:1-4)`)
    const pon = number(match[1]!, MAX_PON, 'PON')
    const from = number(match[2]!, MAX_ONU, 'ONU')
    const range = match[3] ? expand(from, number(match[3], MAX_ONU, 'ONU'), 'ONU') : [from]
    onus.push(...range.map((onu) => `${pon}:${onu}`))
  }
  return limited(onus)
}
