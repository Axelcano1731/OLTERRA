import type { Finding, Severity } from '@/api/types'

import { fold } from './format'

export const SEVERITIES: Severity[] = ['error', 'advertencia', 'info']

export const SEVERITY_LABEL: Record<Severity, string> = {
  error: 'Errores',
  advertencia: 'Advertencias',
  info: 'Por documentar',
}

// Mismas columnas y orden que el CSV de olterra-conciliar (reconciliation/report.py).
const REF_COLUMNS = [
  'customer_id',
  'other_customer_id',
  'pppoe',
  'expected',
  'serial',
  'olt',
  'pon',
  'onu',
  'router',
] as const

const REF_LABEL: Record<(typeof REF_COLUMNS)[number], string> = {
  customer_id: 'Cliente',
  other_customer_id: 'Otro cliente',
  pppoe: 'PPPoE',
  expected: 'Esperado',
  serial: 'Serial',
  olt: 'OLT',
  pon: 'PON',
  onu: 'ONU',
  router: 'Router',
}

export interface FindingGroup {
  kind: string
  title: string
  severity: Severity
  findings: Finding[]
}

/** Agrupa por tipo: primero lo más grave, y dentro de cada gravedad lo más frecuente. */
export function groupFindings(findings: Finding[]): FindingGroup[] {
  const groups = new Map<string, FindingGroup>()
  for (const finding of findings) {
    let group = groups.get(finding.kind)
    if (!group) {
      group = {
        kind: finding.kind,
        title: finding.title,
        severity: finding.severity,
        findings: [],
      }
      groups.set(finding.kind, group)
    }
    group.findings.push(finding)
  }
  return [...groups.values()].sort(
    (a, b) =>
      SEVERITIES.indexOf(a.severity) - SEVERITIES.indexOf(b.severity) ||
      b.findings.length - a.findings.length ||
      a.kind.localeCompare(b.kind),
  )
}

export function filterFindings(
  findings: Finding[],
  severities: ReadonlySet<Severity>,
  text: string,
): Finding[] {
  const needle = fold(text.trim())
  return findings.filter((finding) => {
    if (!severities.has(finding.severity)) return false
    if (!needle) return true
    const haystack = [finding.title, finding.detail, finding.suggestion, ...refValues(finding)]
    return haystack.some((value) => fold(value).includes(needle))
  })
}

function refValues(finding: Finding): string[] {
  return Object.values(finding.refs)
    .filter((value) => value !== null && value !== '')
    .map(String)
}

export function refChips(finding: Finding): { label: string; value: string }[] {
  return REF_COLUMNS.filter((column) => {
    const value = finding.refs[column]
    return value !== undefined && value !== null && value !== ''
  }).map((column) => ({ label: REF_LABEL[column], value: String(finding.refs[column]) }))
}

function csvCell(value: string | number | null | undefined): string {
  const text = value === null || value === undefined ? '' : String(value)
  return /[",\r\n]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text
}

/** CSV con BOM para que Excel en Windows muestre bien las tildes. */
export function findingsToCsv(findings: Finding[]): string {
  const header = ['severidad', 'tipo', 'titulo', 'detalle', 'accion_sugerida', ...REF_COLUMNS]
  const rows = findings.map((f) => [
    f.severity,
    f.kind,
    f.title,
    f.detail,
    f.suggestion,
    ...REF_COLUMNS.map((column) => f.refs[column]),
  ])
  return '\uFEFF' + [header, ...rows].map((row) => row.map(csvCell).join(',')).join('\r\n') + '\r\n'
}
