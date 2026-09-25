import { Pause, Play } from 'lucide-react';
import { useEffect, useId, useMemo, useRef, useState } from 'react';
import { formatTime } from '../lib/format';
import { redact } from '../lib/redact';
import { CopyButton, EmptyState } from './Feedback';

export type LogLevel = 'debug' | 'info' | 'warning' | 'error';

export interface LogEntry {
  id: string | number;
  time: string;
  level: LogLevel;
  message: string;
}

const LEVEL_RANK: Record<LogLevel, number> = { debug: 0, info: 1, warning: 2, error: 3 };
const WINDOWS = [
  { value: 0, label: 'All time' },
  { value: 5 * 60, label: 'Last 5 minutes' },
  { value: 60 * 60, label: 'Last hour' },
] as const;

interface LogViewerProps {
  entries: LogEntry[];
  label?: string;
  timeZone?: string;
}

/**
 * Escaped text only (React text nodes), level and time filters, pause/autoscroll,
 * copy of the visible, redacted segment. Keyboard-scrollable; live updates never move
 * focus.
 */
export function LogViewer({ entries, label = 'Logs', timeZone }: LogViewerProps) {
  const [minLevel, setMinLevel] = useState<LogLevel>('info');
  const [windowSeconds, setWindowSeconds] = useState<number>(0);
  const [paused, setPaused] = useState(false);
  const [frozen, setFrozen] = useState<LogEntry[] | null>(null);
  const listRef = useRef<HTMLOListElement>(null);
  const levelId = useId();
  const windowId = useId();

  const source = paused && frozen ? frozen : entries;
  const visible = useMemo(() => {
    const cutoff = windowSeconds > 0 ? Date.now() - windowSeconds * 1000 : 0;
    return source.filter(
      (e) => LEVEL_RANK[e.level] >= LEVEL_RANK[minLevel] && (cutoff === 0 || Date.parse(e.time) >= cutoff),
    );
  }, [source, minLevel, windowSeconds]);

  useEffect(() => {
    if (paused) return;
    const list = listRef.current;
    if (list) list.scrollTop = list.scrollHeight;
  }, [visible.length, paused]);

  const pending = paused && frozen ? entries.length - frozen.length : 0;
  const copyText = redact(
    visible.map((e) => `${e.time} ${e.level.toUpperCase()} ${e.message}`).join('\n'),
  );

  return (
    <div className="log-viewer">
      <div className="row">
        <label htmlFor={levelId} className="field-label">
          Level
        </label>
        <select
          id={levelId}
          className="select"
          style={{ width: 'auto' }}
          value={minLevel}
          onChange={(e) => setMinLevel(e.target.value as LogLevel)}
        >
          <option value="debug">Debug and above</option>
          <option value="info">Info and above</option>
          <option value="warning">Warnings and errors</option>
          <option value="error">Errors only</option>
        </select>
        <label htmlFor={windowId} className="field-label">
          Time
        </label>
        <select
          id={windowId}
          className="select"
          style={{ width: 'auto' }}
          value={windowSeconds}
          onChange={(e) => setWindowSeconds(Number(e.target.value))}
        >
          {WINDOWS.map((w) => (
            <option key={w.value} value={w.value}>
              {w.label}
            </option>
          ))}
        </select>
        <button
          type="button"
          className="btn btn-secondary"
          aria-pressed={paused}
          onClick={() => {
            if (paused) {
              setFrozen(null);
              setPaused(false);
            } else {
              setFrozen(entries);
              setPaused(true);
            }
          }}
        >
          {paused ? <Play size={16} aria-hidden="true" /> : <Pause size={16} aria-hidden="true" />}
          {paused ? `Resume${pending > 0 ? ` (${pending} new)` : ''}` : 'Pause autoscroll'}
        </button>
        <CopyButton text={copyText} label="Copy visible lines" />
      </div>
      {visible.length === 0 ? (
        <EmptyState title="No log lines">
          {entries.length === 0 ? 'The agent has not written any logs yet.' : 'No lines match the filters.'}
        </EmptyState>
      ) : (
        <ol ref={listRef} className="log-lines" tabIndex={0} aria-label={label}>
          {visible.map((e) => (
            <li key={e.id} className={`log-line log-level-${e.level}`}>
              <span className="muted">{formatTime(e.time, timeZone)}</span>
              <span>{e.level}</span>
              <span>{e.message}</span>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}
