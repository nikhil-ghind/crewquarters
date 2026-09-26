import { useMutation } from '@tanstack/react-query';
import { Volume2 } from 'lucide-react';
import { useEffect, useState, type FormEvent } from 'react';
import { endpoints } from '../../api/endpoints';
import { useActionGuard } from '../../api/guards';
import type { ModelOut } from '../../api/schema';
import { Button } from '../../components/Button';
import { ErrorPanel } from '../../components/Feedback';
import { Field } from '../../components/Field';
import { Card } from '../../components/Layout';

const MAX_CHARS = 1000;

export function isSpeechModel(model: ModelOut): boolean {
  return (model.capabilities ?? []).includes('speech');
}

/** Try a text-to-speech model: speak a short text on this device. Nothing is saved. */
export function SpeechPanel({ model }: { model: ModelOut }) {
  const guard = useActionGuard();
  const voices = model.voices ?? [];
  const [text, setText] = useState('Hello! This is your local crew, speaking from this device.');
  const [voice, setVoice] = useState(voices[0]?.id ?? 'female');
  const [result, setResult] = useState<{ audio: string; captions: string } | null>(null);
  const speak = useMutation({
    mutationFn: () => endpoints.speak(model.id, text.trim(), voice),
    onSuccess: (blob, _vars) => {
      // Captions are the spoken text itself (the media policy allows only blob: URLs).
      const vtt = `WEBVTT\n\n00:00.000 --> 59:59.000\n${text.trim().replace(/-->/g, '→')}\n`;
      setResult({
        audio: URL.createObjectURL(blob),
        captions: URL.createObjectURL(new Blob([vtt], { type: 'text/vtt' })),
      });
    },
  });
  useEffect(
    () => () => {
      if (result) {
        URL.revokeObjectURL(result.audio);
        URL.revokeObjectURL(result.captions);
      }
    },
    [result],
  );
  const installed = model.downloadState === 'INSTALLED';
  const cold = model.memoryState !== 'READY';
  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    if (text.trim()) {
      setResult(null);
      speak.mutate();
    }
  };

  return (
    <Card title="Try speech" subtitle="Speak a short text with this model on this device. The text and audio are not saved.">
      <form className="stack" onSubmit={onSubmit}>
        <Field label="Text" help={`${text.length}/${MAX_CHARS} characters · English`}>
          <textarea className="input" rows={3} maxLength={MAX_CHARS} value={text} onChange={(e) => setText(e.target.value)} />
        </Field>
        <div className="row" style={{ alignItems: 'flex-end' }}>
          <div style={{ flex: '0 1 280px' }}>
            <Field label="Voice">
              <select className="input" value={voice} onChange={(e) => setVoice(e.target.value)}>
                {voices.map((v) => (
                  <option key={v.id} value={v.id}>
                    {v.label}
                  </option>
                ))}
              </select>
            </Field>
          </div>
          <Button
            type="submit"
            variant="primary"
            icon={<Volume2 size={16} aria-hidden="true" />}
            busy={speak.isPending}
            busyLabel={cold ? 'Loading model and speaking…' : 'Speaking…'}
            disabledReason={guard.offline ?? guard.runtime ?? (!installed ? 'Install this model first.' : !text.trim() ? 'Enter some text first.' : null)}
          >
            Speak
          </Button>
        </div>
        {cold && installed ? <p className="muted">The model is not loaded, so this also loads it. That can take about 30 seconds.</p> : null}
      </form>
      {speak.isError ? <ErrorPanel error={speak.error} title="Speech failed" /> : null}
      {result ? (
        <div className="stack-sm" style={{ marginTop: 16 }}>
          <span className="field-label">Result</span>
          <audio controls autoPlay src={result.audio} aria-label="Generated speech">
            <track kind="captions" src={result.captions} srcLang="en" label="English" default />
          </audio>
        </div>
      ) : null}
    </Card>
  );
}
