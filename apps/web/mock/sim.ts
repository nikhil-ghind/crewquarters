/// <reference types="node" />
/** Timer-driven simulation: agent runs, model downloads/loads, document ingestion. */
import type { InputRequestOut, ModelLeaseOut } from '../src/api/schema.ts';
import { callerPreview, callerResult, digestResult, DISCLOSURE, LOAD_STAGES, SCRIPT, CALL_RECIPIENTS } from './fixtures.ts';
import { ApiErr, sleep, type Obj } from './http.ts';
import {
  appendEvent,
  audit,
  isTerminalState,
  newId,
  notifyModel,
  nowIso,
  setRunState,
  st,
  type ModelRec,
  type RunRec,
} from './state.ts';

const alive = (gen: number) => st().gen === gen;
const wait = (steps = 1) => sleep(st().speedMs * steps);

// --- Models ------------------------------------------------------------------------------

const loading = new Map<string, Promise<void>>();

export function changeModel(m: ModelRec, patch: Partial<ModelRec>): void {
  Object.assign(m, patch);
  notifyModel(m.id);
}

/** Load a model through the four stages; resolves when READY. */
export function loadModel(m: ModelRec): Promise<void> {
  if (m.memoryState === 'READY') return Promise.resolve();
  const inflight = loading.get(m.id);
  if (inflight) return inflight;
  if (m.downloadState !== 'INSTALLED') {
    return Promise.reject(new ApiErr(409, 'MODEL_NOT_INSTALLED', `${m.displayName} is not installed on disk.`));
  }
  const gen = st().gen;
  const p = (async () => {
    changeModel(m, {
      memoryState: 'LOADING',
      stage: LOAD_STAGES[0] ?? null,
      loadStartedAt: nowIso(),
      error: null,
      reservedBytes: m.expectedMemoryBytes ?? 0,
      idleUnloadAt: null,
    });
    for (const stage of LOAD_STAGES) {
      if (!alive(gen)) return;
      changeModel(m, { stage });
      await wait(2);
    }
    if (!alive(gen)) return;
    if (m.memoryState !== 'LOADING') throw new ApiErr(409, 'LOAD_FAILED', 'The model failed to load.');
    changeModel(m, { memoryState: 'READY', stage: null, readyAt: nowIso() });
    audit('model.ready', { type: 'model', id: m.id });
  })().finally(() => loading.delete(m.id));
  loading.set(m.id, p);
  return p;
}

export function addLease(m: ModelRec, holderType: ModelLeaseOut['holderType'], holderId: string, label: string): void {
  const leases = (m.activeLeases ?? []).filter((l) => l.holderId !== holderId);
  leases.push({ id: newId(), holderType, holderId, label, expiresAt: nowIso(300000) });
  changeModel(m, { activeLeases: leases, idleUnloadAt: null });
}

export function releaseLease(m: ModelRec, holderId: string): void {
  const leases = (m.activeLeases ?? []).filter((l) => l.holderId !== holderId);
  changeModel(m, {
    activeLeases: leases,
    idleUnloadAt: leases.length === 0 && m.memoryState === 'READY' ? nowIso(st().settings.idleUnloadSeconds * 1000) : m.idleUnloadAt,
  });
}

export async function unloadModel(m: ModelRec): Promise<void> {
  const gen = st().gen;
  changeModel(m, { memoryState: 'DRAINING', stage: 'Draining requests' });
  await wait(2);
  if (!alive(gen)) return;
  changeModel(m, {
    memoryState: 'NOT_LOADED',
    stage: null,
    reservedBytes: 0,
    readyAt: null,
    loadStartedAt: null,
    idleUnloadAt: null,
    activeLeases: [],
  });
}

export async function installModel(m: ModelRec): Promise<void> {
  const gen = st().gen;
  const total = m.diskBytes ?? 1;
  changeModel(m, {
    downloadState: 'DOWNLOADING',
    error: null,
    download: { bytesDone: 0, bytesTotal: total, currentFile: 'model-00001-of-00002.safetensors', revision: 'r1' },
  });
  for (let i = 1; i <= 10; i++) {
    await wait();
    if (!alive(gen) || m.downloadState !== 'DOWNLOADING') return;
    changeModel(m, {
      download: {
        bytesDone: Math.round((total * i) / 10),
        bytesTotal: total,
        currentFile: i <= 6 ? 'model-00001-of-00002.safetensors' : 'model-00002-of-00002.safetensors',
        revision: 'r1',
      },
    });
  }
  changeModel(m, { downloadState: 'INSTALLED', download: { bytesDone: total, bytesTotal: total, currentFile: null, revision: 'r1' } });
  audit('model.installed', { type: 'model', id: m.id });
}

// --- Input requests ----------------------------------------------------------------------------

const continuations = new Map<string, (value: unknown) => void>();

export interface AskSpec {
  key: string;
  title: string;
  prompt: string;
  schema: Obj;
  preview: Obj | null;
  timeoutSeconds: number;
}

/** Create a pending input request; `onAnswer` runs when the owner answers. */
export function ask(run: RunRec, spec: AskSpec, onAnswer: (value: unknown) => void): InputRequestOut {
  const req: InputRequestOut = {
    id: newId(),
    runId: run.id,
    agentName: run.agentName,
    key: spec.key,
    title: spec.title,
    prompt: spec.prompt,
    schema: spec.schema,
    preview: spec.preview,
    state: 'pending',
    answer: null,
    deadline: nowIso(spec.timeoutSeconds * 1000),
    version: 1,
    createdAt: nowIso(),
    answeredAt: null,
  };
  st().inputs.set(req.id, req);
  continuations.set(req.id, onAnswer);
  setRunState(run, 'WAITING_INPUT');
  appendEvent(run, 'run.input_requested', { inputRequestId: req.id, key: req.key, title: req.title, state: 'pending' });
  return req;
}

export function continueAfterAnswer(id: string, value: unknown): void {
  const fn = continuations.get(id);
  continuations.delete(id);
  fn?.(value);
}

export function closeInputsFor(run: RunRec, state: 'cancelled' | 'expired'): void {
  for (const req of st().inputs.values()) {
    if (req.runId === run.id && req.state === 'pending') {
      req.state = state;
      req.version += 1;
      continuations.delete(req.id);
      appendEvent(run, 'run.input_closed', { inputRequestId: req.id, key: req.key, title: req.title, state });
    }
  }
}

// --- Runs ------------------------------------------------------------------------------------------

function modelFor(run: RunRec): ModelRec | undefined {
  const profile = typeof run.config.modelProfile === 'string' ? run.config.modelProfile : 'local.general.small';
  return st().models.get(profile);
}

function finish(run: RunRec, result: Obj, summary: string): void {
  appendEvent(run, 'run.result', { status: 'succeeded', summary });
  run.result = result;
  setRunState(run, 'SUCCEEDED');
  audit('run.succeeded', { type: 'run', id: run.id }, { agent: run.agentId });
}

export function fail(run: RunRec, code: string, message: string, retryable: boolean): void {
  appendEvent(run, 'run.error', { code, message, retryable });
  appendEvent(run, 'run.result', { status: 'failed', summary: message });
  run.error = { code, message, retryable };
  run.retryable = retryable;
  setRunState(run, 'FAILED', { errorCode: code, reason: message });
  audit('run.failed', { type: 'run', id: run.id }, { code }, 'failure');
}

function releaseRunLease(run: RunRec): void {
  const m = modelFor(run);
  if (m && (m.activeLeases ?? []).some((l) => l.holderId === run.id)) releaseLease(m, run.id);
}

export function cancelRun(run: RunRec): void {
  if (isTerminalState(run.state)) return;
  run.cancelRequested = true;
  closeInputsFor(run, 'cancelled');
  if (run.state === 'QUEUED') {
    setRunState(run, 'CANCELLED', { reason: 'Cancelled before it started' });
    return;
  }
  setRunState(run, 'CANCELLING');
  const gen = st().gen;
  setTimeout(() => {
    if (!alive(gen) || run.state !== 'CANCELLING') return;
    releaseRunLease(run);
    appendEvent(run, 'run.log', { level: 'info', message: 'Container stopped; no further external actions.' });
    setRunState(run, 'CANCELLED');
  }, st().speedMs);
}

/** Start (or restart after retry) the scripted behavior for a run's current attempt. */
export function startRun(run: RunRec): void {
  const gen = st().gen;
  const attempt = run.currentAttempt;
  const live = () => alive(gen) && run.currentAttempt === attempt && !run.cancelRequested && !isTerminalState(run.state) && run.state !== 'CANCELLING';
  const tick = async (n = 1) => {
    await wait(n);
    return live();
  };
  const progress = (percent: number | null, step: string, message: string) =>
    appendEvent(run, 'run.progress', { percent, step, message });
  const log = (message: string, level = 'info') => appendEvent(run, 'run.log', { level, message });

  const script = async () => {
    if (!(await tick())) return;
    setRunState(run, 'PREPARING');
    progress(null, 'prepare', 'Starting the agent container');
    log(`Pulling ${run.agentId}@sha256 (already present)`);
    if (!(await tick())) return;

    if (run.agentId === 'daily-gmail-digest') {
      setRunState(run, 'RUNNING');
      log('SDK handshake complete');
      if (!(await tick())) return;
      await withModel();
      if (!live()) return;
      progress(20, 'fetch', 'Listing yesterday’s messages');
      if (!(await tick())) return;
      progress(55, 'classify', 'Classifying 11 messages with the local model');
      log('Classified batch 1 of 2');
      if (!(await tick())) return;
      progress(85, 'summarize', 'Writing the digest');
      log('Classified batch 2 of 2');
      if (!(await tick())) return;
      const maxMessages = typeof run.config.maxMessages === 'number' ? run.config.maxMessages : 200;
      const tz = typeof run.config.timezone === 'string' ? run.config.timezone : 'UTC';
      releaseRunLease(run);
      finish(run, digestResult(tz, maxMessages), 'Digest ready: 2 urgent, 3 important, 6 low priority');
      return;
    }

    if (run.agentId === 'caller') {
      setRunState(run, 'RUNNING');
      progress(5, 'read', 'Reading contacts from the sheet');
      if (!(await tick())) return;
      progress(15, 'validate', 'Checking consent and E.164 numbers');
      log('4 rows read; 3 eligible, 1 skipped (no consent)');
      if (!(await tick())) return;
      askCallerApproval(run);
      return;
    }

    // hello-crew
    const scenario = typeof run.config.fakeScenario === 'string' ? run.config.fakeScenario : 'succeed';
    setRunState(run, 'RUNNING');
    progress(25, 'work', 'Doing the first step');
    if (!(await tick())) return;
    if (scenario === 'model') {
      await withModel();
      if (!live()) return;
      releaseRunLease(run);
    }
    if (scenario === 'fail') {
      fail(run, 'FAKE_FAILURE', 'The fake agent reported a retryable failure.', true);
      return;
    }
    if (scenario === 'slow' || scenario === 'hang') {
      for (let i = 0; i < 1000; i++) {
        progress(Math.min(99, 25 + i), 'slow', `Still working (step ${i + 1})`);
        if (!(await tick(3))) return;
      }
      return;
    }
    if (scenario === 'crash') {
      setRunState(run, 'INTERRUPTED', { reason: 'The agent container exited unexpectedly', errorCode: 'HEARTBEAT_LOST' });
      run.error = { code: 'HEARTBEAT_LOST', message: 'The agent container exited unexpectedly.' };
      run.retryable = true;
      return;
    }
    if (scenario === 'ask') {
      ask(
        run,
        {
          key: 'confirm',
          title: 'Continue the demo run?',
          prompt: 'The fake agent wants to know whether to continue.',
          schema: {
            type: 'object',
            required: ['decision'],
            properties: { decision: { type: 'string', enum: ['continue', 'cancel'] } },
          },
          preview: null,
          timeoutSeconds: 3600,
        },
        (value) => {
          const decision = (value as Obj | null)?.decision;
          setRunState(run, 'RUNNING');
          void (async () => {
            if (!(await tick())) return;
            finish(run, { message: `Owner chose ${String(decision)}` }, `Finished (${String(decision)})`);
          })();
        },
      );
      return;
    }
    progress(75, 'work', 'Almost done');
    if (!(await tick())) return;
    finish(run, { message: 'Hello from the crew!' }, 'Hello Crew finished');
  };

  const withModel = async () => {
    const m = modelFor(run);
    if (!m) return;
    setRunState(run, 'LOADING_MODEL', { reason: `Waiting for ${m.displayName}` });
    addLease(m, 'run', run.id, `${run.agentName} run #${run.id.slice(-4)}`);
    if (m.memoryState !== 'READY') log(`Cold start: loading ${m.displayName}`);
    try {
      await loadModel(m);
    } catch (e) {
      releaseRunLease(run);
      const err = e instanceof ApiErr ? e : new ApiErr(500, 'MODEL_LOAD_FAILED', 'The model failed to load.');
      fail(run, err.code, err.message, true);
      return;
    }
    if (!live()) return;
    setRunState(run, 'RUNNING');
    log(`${m.displayName} ready`);
  };

  void script();
}

export function askCallerApproval(run: RunRec): InputRequestOut {
  const maxCalls = typeof run.config.maxCalls === 'number' ? run.config.maxCalls : 3;
  const disclosure = typeof run.config.disclosure === 'string' ? run.config.disclosure : DISCLOSURE;
  const script = typeof run.config.script === 'string' ? run.config.script : SCRIPT;
  return ask(
    run,
    {
      key: 'confirm-calls-v1:3f9a1c2b4d5e6f70',
      title: `Approve ${CALL_RECIPIENTS.length} automated calls`,
      prompt:
        'The caller agent is ready to call 3 consenting recipients from your sheet. Review the recipients, disclosure, and script before approving.',
      schema: {
        type: 'object',
        required: ['choice'],
        properties: { choice: { type: 'string', enum: ['approve', 'cancel'] } },
      },
      preview: callerPreview(disclosure, script, maxCalls),
      timeoutSeconds: 3600,
    },
    (value) => void callerAfterAnswer(run, value),
  );
}

async function callerAfterAnswer(run: RunRec, value: unknown): Promise<void> {
  const gen = st().gen;
  const attempt = run.currentAttempt;
  const choice = (value as Obj | null)?.choice;
  setRunState(run, 'RUNNING');
  const live = () => alive(gen) && run.currentAttempt === attempt && !run.cancelRequested && !isTerminalState(run.state);
  if (choice !== 'approve') {
    appendEvent(run, 'run.log', { level: 'info', message: 'operator cancelled; no calls placed' });
    await wait();
    if (!live()) return;
    finish(run, callerResult('cancelled', nowIso()), 'Cancelled by the operator: no calls placed');
    return;
  }
  for (const [i, r] of CALL_RECIPIENTS.entries()) {
    appendEvent(run, 'run.progress', {
      percent: 20 + Math.round((75 * i) / CALL_RECIPIENTS.length),
      step: 'call',
      message: `Calling row ${r.row} (${r.masked})`,
    });
    await wait(2);
    if (!live()) return;
  }
  appendEvent(run, 'run.progress', { percent: 97, step: 'sheet', message: 'Writing results to the sheet' });
  await wait();
  if (!live()) return;
  finish(run, callerResult('approved', nowIso()), 'Called 3: 2 answered, 1 response captured');
}

// --- Knowledge ingestion -------------------------------------------------------------------------

export async function ingest(docId: string): Promise<void> {
  const gen = st().gen;
  await wait(2);
  const doc = st().docs.get(docId);
  if (!alive(gen) || !doc) return;
  doc.state = 'PROCESSING';
  doc.updatedAt = nowIso();
  await wait(3);
  if (!alive(gen) || !st().docs.has(docId)) return;
  if (doc.name.toLowerCase().includes('scanned')) {
    doc.state = 'FAILED';
    doc.error = { code: 'SCANNED_PDF_UNSUPPORTED', message: 'This PDF has no text layer (scanned pages). OCR is not supported.' };
  } else {
    const chunks = Math.max(1, Math.round(doc.bytes / 3000));
    doc.state = 'READY';
    doc.error = null;
    doc.extracted = { segments: chunks * 3, chunks, tokens: chunks * 780 };
  }
  doc.updatedAt = nowIso();
}
