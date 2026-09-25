/// <reference types="node" />
/**
 * Security headers the reverse proxy should send with the web UI's HTML (and that the
 * mock server sends when it serves `dist/`). Shared with docs and tests.
 */
export const RECOMMENDED_CSP = [
  "default-src 'self'",
  "script-src 'self'",
  "style-src 'self'",
  "img-src 'self' data:",
  "font-src 'self'",
  "connect-src 'self'",
  "object-src 'none'",
  "base-uri 'self'",
  "form-action 'self'",
  "frame-ancestors 'none'",
].join('; ');

export const HTML_SECURITY_HEADERS: Record<string, string> = {
  'Content-Security-Policy': RECOMMENDED_CSP,
  'X-Content-Type-Options': 'nosniff',
  'Referrer-Policy': 'same-origin',
};
