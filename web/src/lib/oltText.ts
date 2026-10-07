/**
 * Cómo quedará un nombre en la OLT, igual que `clean_label` en
 * src/olterra/drivers/vsol_gpon/provisioning.py: sin tildes, espacios como "_" y solo letras,
 * números, punto, guion y guion bajo (la CLI de la OLT no acepta espacios).
 */
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
