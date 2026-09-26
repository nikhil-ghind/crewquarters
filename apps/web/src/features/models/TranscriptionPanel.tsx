import { useMutation } from '@tanstack/react-query';
import { Mic } from 'lucide-react';
import { useState, type FormEvent } from 'react';
import { MAX_UPLOAD_BYTES, endpoints } from '../../api/endpoints';
import { useActionGuard } from '../../api/guards';
import type { ModelOut } from '../../api/schema';
import { Button } from '../../components/Button';
import { ErrorPanel } from '../../components/Feedback';
import { Field } from '../../components/Field';
import { Card, KeyValue } from '../../components/Layout';
import { formatBytes } from '../../lib/format';

export const AUDIO_TYPES = ['.wav', '.flac', '.mp3', '.ogg', '.m4a', '.webm'];

const LANGUAGES: [string, string][] = [
  ['', 'Detect automatically'],
  ['en', 'English'],
  ['zh', 'Chinese'],
  ['ja', 'Japanese'],
  ['ko', 'Korean'],
  ['fr', 'French'],
  ['de', 'German'],
  ['es', 'Spanish'],
];

export function isTranscriptionModel(model: ModelOut): boolean {
  return (model.capabilities ?? []).includes('transcription');
}

/** Try a speech-to-text model on one audio file. Nothing is stored. */
export function TranscriptionPanel({ model }: { model: ModelOut }) {
  const guard = useActionGuard();
  const [file, setFile] = useState<File | null>(null);
  const [language, setLanguage] = useState('');
  const transcribe = useMutation({
    mutationFn: ({ audio, lang }: { audio: File; lang: string }) => endpoints.transcribe(model.id, audio, lang || null),
  });
  const tooLarge = file !== null && file.size > MAX_UPLOAD_BYTES;
  const installed = model.downloadState === 'INSTALLED';
  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    if (file && !tooLarge) transcribe.mutate({ audio: file, lang: language });
  };
  const cold = model.memoryState !== 'READY';

  return (
    <Card title="Try transcription" subtitle="Transcribe one audio file on this device. The audio and the transcript are not saved.">
      <form className="stack" onSubmit={onSubmit}>
        <div className="row" style={{ alignItems: 'flex-end' }}>
          <div style={{ flex: '1 1 320px' }}>
            <Field
              label="Audio file"
              help={`${AUDIO_TYPES.join(', ')} · up to ${formatBytes(MAX_UPLOAD_BYTES)}`}
              error={tooLarge ? `Too large: audio files can be up to ${formatBytes(MAX_UPLOAD_BYTES)}.` : null}
            >
              <input
                className="input"
                type="file"
                accept={AUDIO_TYPES.join(',')}
                onChange={(e) => {
                  setFile(e.target.files?.[0] ?? null);
                  transcribe.reset();
                }}
              />
            </Field>
          </div>
          <div style={{ flex: '0 1 220px' }}>
            <Field label="Language">
              <select className="input" value={language} onChange={(e) => setLanguage(e.target.value)}>
                {LANGUAGES.map(([code, label]) => (
                  <option key={code} value={code}>
                    {label}
                  </option>
                ))}
              </select>
            </Field>
          </div>
          <Button
            type="submit"
            variant="primary"
            icon={<Mic size={16} aria-hidden="true" />}
            busy={transcribe.isPending}
            busyLabel={cold ? 'Loading model and transcribing…' : 'Transcribing…'}
            disabledReason={guard.offline ?? guard.runtime ?? (!installed ? 'Install this model first.' : !file ? 'Choose an audio file first.' : tooLarge ? 'The file is too large.' : null)}
          >
            Transcribe
          </Button>
        </div>
        {cold && installed ? <p className="muted">The model is not loaded, so the first transcription also loads it. That can take a minute or two.</p> : null}
      </form>
      {transcribe.isError ? <ErrorPanel error={transcribe.error} title="Transcription failed" /> : null}
      {transcribe.data ? (
        <div className="stack-sm" style={{ marginTop: 16 }} aria-live="polite">
          <span className="field-label">Transcript</span>
          <blockquote className="passage" style={{ margin: 0, whiteSpace: 'pre-wrap' }}>
            {transcribe.data.text || <span className="muted">No speech was recognized.</span>}
          </blockquote>
          <KeyValue
            items={[
              ['Audio length', transcribe.data.audioSeconds != null ? `${transcribe.data.audioSeconds.toFixed(1)} s` : '—'],
              ['Transcription time', `${(transcribe.data.latencyMs / 1000).toFixed(2)} s`],
              ['Language hint', transcribe.data.language ?? 'None'],
            ]}
          />
        </div>
      ) : null}
    </Card>
  );
}
