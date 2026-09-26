import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ModelOut, VoiceCallOut } from '../../api/schema';
import * as f from '../../test/fixtures';
import { renderWithProviders } from '../../test/render';
import { errorEnvelope, server } from '../../test/server';
import ModelDetailPage from '../models/ModelDetailPage';
import { VoiceCallCard } from './VoiceCallCard';

const tts: ModelOut = {
  ...f.model,
  id: 'local.tts.voxtream',
  displayName: 'Text to speech - VoXtream (streaming, English)',
  family: 'local.tts',
  capabilities: ['speech', 'streaming'],
  voices: [
    { id: 'female', label: 'Sample voice A' },
    { id: 'male', label: 'Sample voice B' },
  ],
};

beforeEach(() => {
  let n = 0;
  vi.stubGlobal('URL', Object.assign(URL, { createObjectURL: vi.fn(() => `blob:test/${++n}`), revokeObjectURL: vi.fn() }));
  vi.spyOn(HTMLMediaElement.prototype, 'play').mockResolvedValue(undefined);
});
afterEach(() => vi.restoreAllMocks());

describe('text to speech', () => {
  it('speaks the text with the chosen voice and plays it with captions', async () => {
    let sent: unknown = null;
    server.use(
      http.get('/api/v1/models/:id', () => HttpResponse.json(tts)),
      http.post('/api/v1/models/:id/speech', async ({ request }) => {
        sent = await request.json();
        return new HttpResponse(new Uint8Array([82, 73, 70, 70]), { headers: { 'Content-Type': 'audio/wav' } });
      }),
    );
    renderWithProviders(<ModelDetailPage />, { path: '/models/:modelId', route: `/models/${tts.id}` });
    const user = userEvent.setup();
    await screen.findByRole('heading', { name: 'Try speech' });
    const text = screen.getByLabelText(/^Text/);
    await user.clear(text);
    await user.type(text, 'Good morning, crew.');
    await user.selectOptions(screen.getByLabelText('Voice'), 'male');
    await user.click(screen.getByRole('button', { name: /Speak/ }));
    const audio = await screen.findByLabelText('Generated speech');
    expect(sent).toEqual({ text: 'Good morning, crew.', voice: 'male' });
    expect(audio).toHaveAttribute('src', 'blob:test/1');
    expect(audio.querySelector('track')).toHaveAttribute('kind', 'captions');
  });

  it('explains failures', async () => {
    server.use(
      http.get('/api/v1/models/:id', () => HttpResponse.json(tts)),
      http.post('/api/v1/models/:id/speech', () => errorEnvelope(409, 'MODEL_NOT_INSTALLED', 'Install local.tts.voxtream before using it.')),
    );
    renderWithProviders(<ModelDetailPage />, { path: '/models/:modelId', route: `/models/${tts.id}` });
    const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: /Speak/ }));
    expect(await screen.findByText('Speech failed')).toBeInTheDocument();
  });
});

describe('voice calls', () => {
  const live: VoiceCallOut = {
    id: 'a'.repeat(32),
    state: 'connected',
    to: '+1******0101',
    voice: 'female',
    simulated: false,
    createdAt: f.NOW,
    connectedAt: f.NOW,
    durationSeconds: 12,
    turns: [
      { role: 'assistant', text: 'Hi! How can I help you today?', at: f.NOW, interrupted: false, timings: {} },
      { role: 'caller', text: 'What time do you open?', at: f.NOW, interrupted: false, timings: { asrMs: 480 } },
      { role: 'assistant', text: 'We open at nine.', at: f.NOW, interrupted: true, timings: { asrMs: 480, firstAudioMs: 1350 } },
    ],
  };

  it('confirms, places the call, shows the live transcript, and hangs up', async () => {
    let started: unknown = null;
    let hungUp = false;
    server.use(
      http.get('/api/v1/models/:id', () => HttpResponse.json(tts)),
      http.post('/api/v1/connections/twilio/voice-calls', async ({ request }) => {
        started = await request.json();
        return HttpResponse.json({ ...live, state: 'dialing', turns: [] });
      }),
      http.get('/api/v1/connections/twilio/voice-calls/:id', () => HttpResponse.json(hungUp ? { ...live, state: 'ended', endReason: 'hung_up_by_owner' } : live)),
      http.post('/api/v1/connections/twilio/voice-calls/:id/hangup', () => {
        hungUp = true;
        return HttpResponse.json({ ...live, state: 'ended' });
      }),
    );
    renderWithProviders(<VoiceCallCard connected />);
    const user = userEvent.setup();
    expect(screen.getByRole('button', { name: /Start call/ })).toBeDisabled();
    await user.type(screen.getByLabelText(/Number to call/), '+15555550101');
    await user.type(screen.getByLabelText(/Instructions/), 'Take a pizza order.');
    await user.click(screen.getByRole('button', { name: /Start call/ }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText(/automated call from an AI assistant/)).toBeInTheDocument();
    await user.click(within(dialog).getByRole('button', { name: 'Place the call' }));
    expect(await screen.findByText('We open at nine.')).toBeInTheDocument();
    expect(started).toEqual({ to: '+15555550101', confirm: true, voice: 'female', instructions: 'Take a pizza order.' });
    expect(screen.getByText('In conversation')).toBeInTheDocument();
    expect(screen.getByText('Interrupted')).toBeInTheDocument();
    expect(screen.getByText(/answered in 1350 ms/)).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /Hang up/ }));
    await waitFor(() => expect(screen.getByText('Ended')).toBeInTheDocument(), { timeout: 3000 });
    expect(screen.queryByRole('button', { name: /Hang up/ })).not.toBeInTheDocument();
  });

  it('is unavailable until Twilio is connected', () => {
    renderWithProviders(<VoiceCallCard connected={false} />);
    expect(screen.getByText('Connect Twilio above to place calls.')).toBeInTheDocument();
  });
});
