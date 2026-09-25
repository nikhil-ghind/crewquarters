/// <reference types="node" />
/** HTTP helpers for the mock control API: JSON responses, error envelope, cookies, SSE. */
import type { IncomingMessage, ServerResponse } from 'node:http';

export type Json = null | boolean | number | string | Json[] | { [key: string]: Json };
export type Obj = Record<string, unknown>;

export class ApiErr extends Error {
  status: number;
  code: string;
  details: Obj;
  constructor(status: number, code: string, message: string, details: Obj = {}) {
    super(message);
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

export interface Resp {
  status: number;
  body?: unknown;
  headers?: Record<string, string | string[]>;
}

export const HANDLED = 'handled' as const;
export type Result = Resp | typeof HANDLED;

let requestCounter = 0;
export function nextRequestId(): string {
  requestCounter += 1;
  return `req-mock-${requestCounter.toString(16).padStart(6, '0')}`;
}

export function ok(body: unknown, status = 200, headers?: Record<string, string | string[]>): Resp {
  return { status, body, headers };
}

export function noContent(headers?: Record<string, string | string[]>): Resp {
  return { status: 204, headers };
}

export function errorBody(err: ApiErr, requestId: string): Obj {
  return { error: { code: err.code, message: err.message, requestId, details: err.details } };
}

export function send(res: ServerResponse, resp: Resp): void {
  const headers: Record<string, string | string[]> = { 'Cache-Control': 'no-store', ...(resp.headers ?? {}) };
  if (resp.status === 204 || resp.body === undefined) {
    res.writeHead(resp.status, headers);
    res.end();
    return;
  }
  const text = JSON.stringify(resp.body);
  res.writeHead(resp.status, { 'Content-Type': 'application/json', ...headers });
  res.end(text);
}

export function readBody(req: IncomingMessage): Promise<Buffer> {
  return new Promise((resolve, reject) => {
    const chunks: Buffer[] = [];
    req.on('data', (c: Buffer) => chunks.push(c));
    req.on('end', () => resolve(Buffer.concat(chunks)));
    req.on('error', reject);
  });
}

export function parseCookies(header: string | undefined): Record<string, string> {
  const out: Record<string, string> = {};
  if (!header) return out;
  for (const part of header.split(';')) {
    const [k, ...rest] = part.trim().split('=');
    if (k) out[k] = decodeURIComponent(rest.join('='));
  }
  return out;
}

export function cookie(
  name: string,
  value: string,
  opts: { httpOnly?: boolean; path?: string; maxAge?: number } = {},
): string {
  const parts = [`${name}=${encodeURIComponent(value)}`, `Path=${opts.path ?? '/'}`, 'SameSite=Lax'];
  if (opts.httpOnly) parts.push('HttpOnly');
  if (opts.maxAge !== undefined) parts.push(`Max-Age=${opts.maxAge}`);
  return parts.join('; ');
}

export function sseHeaders(res: ServerResponse): void {
  res.writeHead(200, {
    'Content-Type': 'text/event-stream',
    'Cache-Control': 'no-store',
    Connection: 'keep-alive',
    'X-Accel-Buffering': 'no',
  });
}

export function sseEvent(event: string, data: unknown, id?: string | number): string {
  const idLine = id === undefined ? '' : `id: ${id}\n`;
  return `${idLine}event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
}

export function asObj(value: unknown): Obj {
  return typeof value === 'object' && value !== null && !Array.isArray(value) ? (value as Obj) : {};
}

export function str(value: unknown): string | undefined {
  return typeof value === 'string' ? value : undefined;
}

export function deepEqual(a: unknown, b: unknown): boolean {
  if (a === b) return true;
  if (typeof a !== typeof b || a === null || b === null || typeof a !== 'object') return false;
  if (Array.isArray(a) !== Array.isArray(b)) return false;
  if (Array.isArray(a) && Array.isArray(b)) {
    return a.length === b.length && a.every((v, i) => deepEqual(v, b[i]));
  }
  const ao = a as Obj;
  const bo = b as Obj;
  const ak = Object.keys(ao).filter((k) => ao[k] !== undefined);
  const bk = Object.keys(bo).filter((k) => bo[k] !== undefined);
  return ak.length === bk.length && ak.every((k) => deepEqual(ao[k], bo[k]));
}

export const sleep = (ms: number): Promise<void> => new Promise((r) => setTimeout(r, ms));

export function notFound(what: string, id: string): ApiErr {
  return new ApiErr(404, 'NOT_FOUND', `${what} ${id} was not found.`);
}
