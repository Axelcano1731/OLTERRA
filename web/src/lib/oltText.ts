/**
 * Cómo quedará un nombre en la OLT, igual que `clean_label` en
 * src/olterra/drivers/vsol_gpon/provisioning.py: sin tildes, espacios como "_" y solo letras,
 * números, punto, guion y guion bajo (la CLI de la OLT no acepta espacios).
 */
/** Lo que la OLT escribe cuando no sabe el modelo de la ONU ("NULL" en la V1600G0-B). */
const NO_MODEL = new Set(['null', 'n/a', 'na', 'none', 'unknown', '-', '--', '0'])

/** El modelo de la ONU (VSOLV422) si es uno de verdad; null si la OLT no lo sabe. */
export function realModel(model: string | null | undefined): string | null {
  const value = model?.trim() ?? ''
  return value && !NO_MODEL.has(value.toLowerCase()) ? value : null
}

export function oltLabel(text: string, limit: number): string {
  const ascii = text.normalize('NFKD').replace(/[^\x20-\x7e]/g, '')
  return ascii
    .trim()
    .replace(/[^A-Za-z0-9_.-]+/g, '_')
    .replace(/^_+|_+$/g, '')
    .replace(/_{2,}/g, '_')
    .slice(0, limit)
    .replace(/_+$/, '')
}
