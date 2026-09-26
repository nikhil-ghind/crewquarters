import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';
import type { ModelOut } from '../../api/schema';
import * as f from '../../test/fixtures';
import { renderWithProviders } from '../../test/render';
import { errorEnvelope, server } from '../../test/server';
import ChatPage from '../chat/ChatPage';
import ModelDetailPage from './ModelDetailPage';

const asr: ModelOut = {
  ...f.model,
  id: 'local.asr.r2t2',
  displayName: 'Speech to text - Confucius4-R2T2 (2B)',
  family: 'local.asr',
  capabilities: ['transcription'],
};

function renderModel(model: ModelOut) {
  server.use(http.get('/api/v1/models/:id', () => HttpResponse.json(model)));
  return renderWithProviders(<ModelDetailPage />, { path: '/models/:modelId', route: `/models/${model.id}` });
}

describe('speech-to-text models', () => {
  it('transcribes an uploaded file and shows the transcript', async () => {
    let sent: FormData | null = null;
    server.use(
      http.post('/api/v1/models/:id/transcriptions', async ({ request }) => {
        sent = await request.formData();
        return HttpResponse.json({ modelId: asr.id, text: 'Hello from the crew.', language: 'en', audioSeconds: 2.5, latencyMs: 420, requestId: 'r1' });
      }),
    );
    renderModel(asr);
    const user = userEvent.setup();
    await screen.findByRole('heading', { name: 'Try transcription' });
    expect(screen.getByRole('button', { name: /Transcribe/ })).toBeDisabled();
    await user.upload(screen.getByLabelText(/Audio file/), new File([new Uint8Array(64)], 'memo.wav', { type: 'audio/wav' }));
    await user.selectOptions(screen.getByLabelText('Language'), 'en');
    await user.click(screen.getByRole('button', { name: /Transcribe/ }));
    expect(await screen.findByText('Hello from the crew.')).toBeInTheDocument();
    expect(screen.getByText('2.5 s')).toBeInTheDocument();
    expect(sent).not.toBeNull();
    const form = sent as unknown as FormData;
    expect(form.get('language')).toBe('en');
    // The control API picks the audio type from the file name's extension.
    const audio = form.get('file') as File;
    expect(audio.name).toBe('memo.wav');
    expect(audio.size).toBe(64);
  });

  it('explains a failure instead of showing a transcript', async () => {
    server.use(http.post('/api/v1/models/:id/transcriptions', () => errorEnvelope(409, 'MODEL_CAPACITY_EXCEEDED', 'Not enough safe unified memory for this model.')));
    renderModel(asr);
    const user = userEvent.setup();
    await user.upload(await screen.findByLabelText(/Audio file/), new File([new Uint8Array(8)], 'memo.wav', { type: 'audio/wav' }));
    await user.click(screen.getByRole('button', { name: /Transcribe/ }));
    expect(await screen.findByText('Transcription failed')).toBeInTheDocument();
  });

  it('is not offered for chat models', async () => {
    renderModel(f.model);
    await screen.findByRole('heading', { name: f.model.displayName });
    expect(screen.queryByRole('heading', { name: 'Try transcription' })).not.toBeInTheDocument();
  });

  it('is not offered as a chat model', async () => {
    server.use(
      http.get('/api/v1/models', () => HttpResponse.json({ items: [asr, f.model], nextCursor: null })),
      http.get('/api/v1/chat/sessions', () => HttpResponse.json({ items: [], nextCursor: null })),
    );
    renderWithProviders(<ChatPage />);
    await waitFor(() => expect(screen.getByRole('option', { name: new RegExp(f.model.displayName.replace(/[()]/g, '\\$&')) })).toBeInTheDocument());
    expect(screen.queryByRole('option', { name: /Speech to text/ })).not.toBeInTheDocument();
  });
});
