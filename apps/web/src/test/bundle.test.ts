// @vitest-environment node
/**
 * The built bundle must never contain provider, model-server or internal addresses.
 * Runs scripts/check-bundle.ts against dist/ when a build exists (CI builds first).
 */
import { execFileSync } from 'node:child_process';
import { existsSync, mkdtempSync, mkdirSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

const root = resolve(__dirname, '../..');
const script = join(root, 'scripts/check-bundle.ts');

function check(dir: string): { ok: boolean; output: string } {
  try {
    const output = execFileSync(process.execPath, [script, dir], { encoding: 'utf8', stdio: 'pipe' });
    return { ok: true, output };
  } catch (error) {
    const e = error as { stdout?: string; stderr?: string };
    return { ok: false, output: `${e.stdout ?? ''}${e.stderr ?? ''}` };
  }
}

describe('bundle check', () => {
  it('flags provider hostnames and inline scripts', () => {
    const dir = mkdtempSync(join(tmpdir(), 'cq-bundle-'));
    try {
      mkdirSync(join(dir, 'assets'));
      writeFileSync(join(dir, 'index.html'), '<script>alert(1)</script>');
      writeFileSync(join(dir, 'assets/a.js'), 'fetch("https://api.openai.com/v1/x");fetch("https://api.twilio.com")');
      const result = check(dir);
      expect(result.ok).toBe(false);
      expect(result.output).toContain('OpenAI API host');
      expect(result.output).toContain('Twilio host');
      expect(result.output).toContain('inline <script>');
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  });

  it.skipIf(!existsSync(join(root, 'dist/index.html')))('passes for the current dist/', () => {
    const result = check(join(root, 'dist'));
    expect(result.output).toContain('Bundle check passed');
    expect(result.ok).toBe(true);
  });
});
