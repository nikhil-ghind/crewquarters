import { useEffect, useRef, useState } from 'react';
import type { ModelOut } from '../../api/schema';
import { Banner } from '../../components/Feedback';
import { Progress } from '../../components/Meters';
import { formatBytes, formatDuration, percent } from '../../lib/format';

export const LOAD_STAGES = ['Starting container', 'Loading weights', 'Allocating cache', 'Health check'] as const;

/** Transfer rate from successive progress samples; null until two samples exist. */
function useRate(bytesDone: number | undefined): number | null {
  const samples = useRef<{ t: number; b: number }[]>([]);
  const [rate, setRate] = useState<number | null>(null);
  useEffect(() => {
    if (bytesDone === undefined) return;
    const now = Date.now();
    const list = samples.current.filter((s) => now - s.t < 20_000);
    list.push({ t: now, b: bytesDone });
    samples.current = list;
    const first = list[0];
    const last = list[list.length - 1];
    if (first && last && last.t - first.t >= 2_000 && last.b > first.b) {
      setRate(((last.b - first.b) * 1000) / (last.t - first.t));
    }
  }, [bytesDone]);
  return rate;
}

/**
 * Download detail (bytes, percentage, current file, rate, ETA when reliable,
 * resumability) and load stages with elapsed time — never a fake percentage.
 */
export function ModelProgress({ model }: { model: ModelOut }) {
  const download = model.download;
  const downloading = model.downloadState === 'DOWNLOADING';
  const rate = useRate(downloading ? download?.bytesDone : undefined);

  if (downloading) {
    const total = download?.bytesTotal ?? null;
    const done = download?.bytesDone ?? 0;
    const pct = total ? percent(done, total) : null;
    const remaining = total && rate ? (total - done) / rate : null;
    return (
      <div className="stack-sm" aria-live="polite">
        <Progress
          label={`Downloading ${model.displayName}`}
          percent={pct}
          stage={total ? `${formatBytes(done)} of ${formatBytes(total)}` : `${formatBytes(done)} downloaded`}
          detail={download?.currentFile ? `Current file: ${download.currentFile}` : null}
        />
        <span className="muted">
          {rate ? `${formatBytes(rate)}/s` : 'Measuring speed…'}
          {remaining !== null && remaining < 86_400 ? ` · about ${formatDuration(remaining)} left` : ''}
          {' · '}Resumes where it left off if interrupted.
        </span>
      </div>
    );
  }
  if (model.memoryState === 'LOADING') {
    const current = model.stage ?? 'Starting container';
    const index = LOAD_STAGES.findIndex((s) => s === current);
    return (
      <div className="stack-sm" aria-live="polite">
        <Progress label={`Loading ${model.displayName}`} stage={current} since={model.loadStartedAt ?? null} />
        <ol className="row muted" style={{ listStyle: 'none', gap: 12 }} aria-label="Load stages">
          {LOAD_STAGES.map((stage, i) => (
            <li key={stage} aria-current={i === index ? 'step' : undefined} style={{ fontWeight: i === index ? 700 : 400 }}>
              {i < index ? '✓ ' : ''}
              {stage}
            </li>
          ))}
        </ol>
      </div>
    );
  }
  if (model.memoryState === 'DRAINING') {
    return <Progress label="Unloading" stage="Finishing current requests, then releasing memory" since={null} />;
  }
  const error = model.error;
  if (model.downloadState === 'DOWNLOAD_ERROR' || model.memoryState === 'LOAD_ERROR' || model.memoryState === 'ERROR') {
    const code = typeof error?.code === 'string' ? error.code : 'MODEL_ERROR';
    const message = typeof error?.message === 'string' ? error.message : 'The model reported an error.';
    return (
      <Banner tone="danger" role="alert" title={model.downloadState === 'DOWNLOAD_ERROR' ? 'Download failed' : 'Model failed to load'}>
        {message} The installed files are kept. <span className="diag-code">Diagnostic code: {code}</span>
      </Banner>
    );
  }
  return null;
}
