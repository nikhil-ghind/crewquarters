import { useMutation, useQuery } from '@tanstack/react-query';
import { PhoneCall, PhoneOff } from 'lucide-react';
import { useState, type FormEvent } from 'react';
import { endpoints } from '../../api/endpoints';
import { useActionGuard } from '../../api/guards';
import { useIntentKey } from '../../api/mutations';
import { useModel } from '../../api/queries';
import type { VoiceCallOut, VoiceTurnOut } from '../../api/schema';
import type { StatusSpec } from '../../lib/status';
import { Button } from '../../components/Button';
import { ConfirmDialog } from '../../components/Dialog';
import { Banner, ErrorPanel } from '../../components/Feedback';
import { Field } from '../../components/Field';
import { Card, KeyValue } from '../../components/Layout';
import { StatusBadge } from '../../components/StatusBadge';

const E164 = /^\+[1-9][0-9]{7,14}$/;
const TTS_MODEL = 'local.tts.voxtream';
const LIVE = new Set(['created', 'dialing', 'ringing', 'connected']);

const STATE: Record<VoiceCallOut['state'], StatusSpec> = {
  created: { label: 'Starting', tone: 'info', icon: 'spinner' },
  dialing: { label: 'Dialing', tone: 'info', icon: 'spinner' },
  ringing: { label: 'Ringing', tone: 'info', icon: 'spinner' },
  connected: { label: 'In conversation', tone: 'success', icon: 'play' },
  ended: { label: 'Ended', tone: 'neutral', icon: 'check' },
  failed: { label: 'Failed', tone: 'danger', icon: 'x' },
};

function Timings({ turn }: { turn: VoiceTurnOut }) {
  const t = turn.timings ?? {};
  const parts = [
    t.asrMs != null ? `heard in ${t.asrMs} ms` : null,
    t.firstAudioMs != null ? `answered in ${t.firstAudioMs} ms` : null,
  ].filter(Boolean);
  return parts.length ? <span className="muted"> · {parts.join(', ')}</span> : null;
}

/** Start a realtime AI phone conversation and follow its transcript live. */
export function VoiceCallCard({ connected }: { connected: boolean }) {
  const guard = useActionGuard();
  const tts = useModel(TTS_MODEL);
  const voices = tts.data?.voices ?? [];
  const [to, setTo] = useState('');
  const [voice, setVoice] = useState('female');
  const [instructions, setInstructions] = useState('');
  const [confirming, setConfirming] = useState(false);
  const [callId, setCallId] = useState<string | null>(null);
  const [key, resetKey] = useIntentKey();
  const start = useMutation({
    mutationFn: () => endpoints.voiceCallStart({ to: to.trim(), confirm: true, voice, instructions: instructions.trim() }, key),
    onSuccess: (call) => {
      setConfirming(false);
      resetKey();
      setCallId(call.id);
    },
  });
  const call = useQuery({
    queryKey: ['voice-call', callId],
    queryFn: () => endpoints.voiceCall(callId ?? ''),
    enabled: callId !== null,
    refetchInterval: (q) => (q.state.data && !LIVE.has(q.state.data.state) ? false : 1000),
  });
  const hangup = useMutation({ mutationFn: () => endpoints.voiceCallHangup(callId ?? '') });
  const live = call.data ? LIVE.has(call.data.state) : callId !== null;
  const numberOk = E164.test(to.trim());
  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    if (numberOk) setConfirming(true);
  };

  return (
    <Card
      title="Talk to your crew"
      subtitle="A realtime phone conversation with the local models: speech to text, a chat model, and streaming text to speech."
      className="form-width"
    >
      {!connected ? <Banner tone="info">Connect Twilio above to place calls.</Banner> : null}
      <form className="stack" onSubmit={onSubmit}>
        <Field label="Number to call" required help="An allowed number (CQ_TWILIO_ALLOWED_NUMBERS) of someone who agreed to take this call.">
          <input className="input mono" value={to} inputMode="tel" placeholder="+15551234567" onChange={(e) => setTo(e.target.value)} />
        </Field>
        <div className="row" style={{ alignItems: 'flex-end' }}>
          <div style={{ flex: '0 1 280px' }}>
            <Field label="Voice">
              <select className="input" value={voice} onChange={(e) => setVoice(e.target.value)}>
                {(voices.length ? voices : [{ id: 'female', label: 'Voice A' }]).map((v) => (
                  <option key={v.id} value={v.id}>
                    {v.label}
                  </option>
                ))}
              </select>
            </Field>
          </div>
        </div>
        <Field label="Instructions (optional)" help="What the assistant should do on this call, for example: take a pizza order.">
          <textarea className="input" rows={2} maxLength={1000} value={instructions} onChange={(e) => setInstructions(e.target.value)} />
        </Field>
        <div className="row">
          <Button
            type="submit"
            variant="primary"
            icon={<PhoneCall size={16} aria-hidden="true" />}
            disabledReason={guard.offline ?? guard.runtime ?? (!connected ? 'Connect Twilio first.' : live ? 'A call is in progress.' : !numberOk ? 'Enter a number in international format.' : null)}
          >
            Start call
          </Button>
        </div>
      </form>
      {start.isError ? <ErrorPanel error={start.error} title="The call was not placed" /> : null}
      {call.data ? (
        <div className="stack" style={{ marginTop: 16 }} aria-live="polite">
          <div className="row-between">
            <StatusBadge status={STATE[call.data.state]} context="Call" />
            {live ? (
              <Button icon={<PhoneOff size={16} aria-hidden="true" />} busy={hangup.isPending} busyLabel="Ending…" onClick={() => hangup.mutate()}>
                Hang up
              </Button>
            ) : null}
          </div>
          <KeyValue
            items={[
              ['To', <span key="to" className="mono">{call.data.to}</span>],
              ['Duration', call.data.durationSeconds != null ? `${call.data.durationSeconds} s` : '—'],
              ...(call.data.endReason ? [['Ended because', call.data.endReason.replaceAll('_', ' ')] as [string, string]] : []),
              ...(call.data.error ? [['Error', call.data.error] as [string, string]] : []),
            ]}
          />
          {(call.data.turns ?? []).length === 0 ? (
            <p className="muted">The transcript appears here once the call connects.</p>
          ) : (
            <ol className="stack-sm" style={{ listStyle: 'none' }}>
              {(call.data.turns ?? []).map((turn, i) => (
                <li key={i} className="card card-compact stack-sm">
                  <span className="field-label">
                    {turn.role === 'caller' ? 'Caller' : 'Assistant'}
                    {turn.interrupted ? <span className="badge tone-warning" style={{ marginLeft: 8 }}>Interrupted</span> : null}
                    <Timings turn={turn} />
                  </span>
                  <span>{turn.text || <span className="muted">…</span>}</span>
                </li>
              ))}
            </ol>
          )}
          <p className="muted">The transcript is kept in memory for this view only and is not saved.</p>
        </div>
      ) : null}
      <ConfirmDialog
        open={confirming}
        title={`Call ${to.trim()}?`}
        consequence="This places a real phone call. The person hears that it is an automated call from an AI assistant, then talks with your local models. Call only people who agreed to it."
        confirmLabel="Place the call"
        busy={start.isPending}
        onConfirm={() => start.mutate()}
        onCancel={() => setConfirming(false)}
      >
        {start.isError ? <ErrorPanel error={start.error} title="The call was not placed" /> : null}
      </ConfirmDialog>
    </Card>
  );
}
