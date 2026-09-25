/**
 * Defense-in-depth redaction for text the operator copies out of the UI (logs,
 * diagnostics). The server already redacts; this catches anything that slipped
 * through before it reaches the clipboard.
 */
const PATTERNS: [RegExp, string][] = [
  [/\b(sk|rk|pk)-[A-Za-z0-9_-]{12,}\b/g, '[redacted-key]'],
  [/\bsk-ant-[A-Za-z0-9_-]{12,}\b/g, '[redacted-key]'],
  [/\bya29\.[A-Za-z0-9._-]+/g, '[redacted-token]'],
  [/\b1\/\/[A-Za-z0-9._-]{20,}/g, '[redacted-token]'],
  [/\bAC[a-f0-9]{32}\b/g, '[redacted-sid]'],
  [/\bBearer\s+[A-Za-z0-9._~+/-]+=*/gi, 'Bearer [redacted]'],
  [/\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}/g, '[redacted-jwt]'],
  [/("?(?:password|secret|token|api[_-]?key|authToken)"?\s*[:=]\s*)("[^"]*"|[^\s,}]+)/gi, '$1[redacted]'],
];
const PHONE = /\+\d{7,15}\b/g;

export function redact(text: string): string {
  let out = text;
  for (const [pattern, replacement] of PATTERNS) {
    out = out.replace(pattern, replacement);
  }
  return out.replace(PHONE, (match) => `••••${match.slice(-2)}`);
}
