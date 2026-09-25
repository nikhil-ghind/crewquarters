/**
 * Fails if the built bundle could talk to anything but its own origin (PLAN.md
 * section 13.19 / Person 4 brief): no provider, model-server, runtime-daemon or
 * internal-API addresses, no inline scripts in index.html, no source maps.
 *
 *   node scripts/check-bundle.ts [distDir]
 */
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';

const dist = process.argv[2] ?? 'dist';

export const FORBIDDEN: [RegExp, string][] = [
  [/api\.openai\.com/i, 'OpenAI API host'],
  [/api\.anthropic\.com/i, 'Anthropic API host'],
  [/googleapis\.com/i, 'Google API host'],
  [/accounts\.google\.com/i, 'Google OAuth host (the server supplies the authorization URL)'],
  [/twilio\.com/i, 'Twilio host'],
  [/\/internal\/v1/, 'internal service API'],
  [/runtime\.sock/, 'runtime daemon socket'],
  [/\/v1\/chat\/completions/, 'direct model-server (vLLM/OpenAI-compatible) endpoint'],
  [/localhost:8090|model-gateway:8090/, 'model gateway address'],
];

function files(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    return statSync(path).isDirectory() ? files(path) : [path];
  });
}

const problems: string[] = [];
let list: string[];
try {
  list = files(dist);
} catch {
  console.error(`No build output at ${dist}. Run "npm run build" first.`);
  process.exit(2);
}

for (const file of list) {
  const rel = relative(dist, file);
  if (rel.endsWith('.map')) problems.push(`${rel}: source maps must not ship`);
  if (!/\.(js|css|html|svg|json|txt)$/.test(rel)) continue;
  const text = readFileSync(file, 'utf8');
  for (const [pattern, what] of FORBIDDEN) {
    const match = pattern.exec(text);
    if (match) problems.push(`${rel}: contains ${what} ("${match[0]}")`);
  }
  if (rel === 'index.html') {
    for (const tag of text.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script>/g)) {
      const [, attrs = '', body = ''] = tag;
      if (!/\bsrc=/.test(attrs) || body.trim()) problems.push('index.html: inline <script> would need a CSP exception');
    }
    if (/\son[a-z]+=/i.test(text)) problems.push('index.html: inline event handler');
  }
}

const jsBytes = list.filter((f) => f.endsWith('.js')).reduce((sum, f) => sum + statSync(f).size, 0);
if (jsBytes > 1_500_000) problems.push(`total JavaScript is ${jsBytes} bytes (budget 1.5 MB)`);

if (problems.length > 0) {
  console.error('Bundle check failed:\n' + problems.map((p) => `  - ${p}`).join('\n'));
  process.exit(1);
}
console.log(`Bundle check passed: ${list.length} files, ${(jsBytes / 1024).toFixed(0)} KiB of JavaScript, same-origin only.`);
