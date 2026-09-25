/**
 * Regenerates the TypeScript API types from packages/contracts/openapi.yaml into a
 * temporary file and fails if they differ from the committed client
 * (packages/contracts/clients/typescript/schema.d.ts). Same generator and version as
 * `make contracts` (openapi-typescript 7.4.4).
 *
 *   node scripts/check-generated.ts
 */
import { execFileSync } from 'node:child_process';
import { mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const spec = '../../packages/contracts/openapi.yaml';
const committed = '../../packages/contracts/clients/typescript/schema.d.ts';
const dir = mkdtempSync(join(tmpdir(), 'cq-gen-'));
const out = join(dir, 'schema.d.ts');
try {
  execFileSync('npx', ['--no-install', 'openapi-typescript', spec, '-o', out], { stdio: 'inherit' });
  const fresh = readFileSync(out, 'utf8');
  const current = readFileSync(committed, 'utf8');
  if (fresh !== current) {
    console.error('schema.d.ts is out of date with openapi.yaml. Run "npm run gen:api" (or "make contracts") and commit.');
    process.exit(1);
  }
  console.log('Generated TypeScript client matches openapi.yaml.');
} finally {
  rmSync(dir, { recursive: true, force: true });
}
