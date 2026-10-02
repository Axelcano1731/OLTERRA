// Mismo formato que security/apikeys.py: olt_<tenant>_<id>_<secreto>.
const API_KEY = /^olt_[0-9a-f]{32}_[0-9a-f]{32}_[A-Za-z0-9_-]{43}$/

export function looksLikeApiKey(text: string): boolean {
  return API_KEY.test(text.trim())
}
