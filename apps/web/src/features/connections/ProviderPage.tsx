import { useQueryClient } from '@tanstack/react-query';
import { useEffect } from 'react';
import { Navigate, useNavigate, useParams, useSearchParams } from 'react-router';
import { keys } from '../../api/queries';
import { Banner } from '../../components/Feedback';
import { Card, Page, PageHeader } from '../../components/Layout';
import { readPref, writePref } from '../../lib/storage';
import { PROVIDER_NAMES } from '../../lib/status';
import { googleErrorText } from './googleErrors';
import { useConnections } from '../../api/queries';
import { GOOGLE_RETURN_KEY, GoogleConnect, ProviderKeyForm, TwilioForm } from './forms';
import { VoiceCallCard } from './VoiceCallCard';

const PURPOSE: Record<string, string> = {
  google: 'Gmail (read-only) and Google Sheets access for your agents.',
  twilio: 'Phone calls with a fixed, disclosed script, placed only after you approve them.',
  github: 'Read pull requests and post review comments in the repository an agent is configured for. The token is set on the device (CQ_GITHUB_TOKEN).',
  openai: 'Optional cloud models. Nothing is sent unless an agent is approved for OpenAI.',
  anthropic: 'Optional cloud models. Nothing is sent unless an agent is approved for Anthropic.',
};

export default function ProviderPage() {
  const { provider = '' } = useParams();
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const client = useQueryClient();
  const connections = useConnections();
  const twilioConnected = (connections.data ?? []).some((c) => c.provider === 'twilio' && c.status === 'CONNECTED');
  const result = params.get('result');
  const code = params.get('code');

  // Google returns here after consent; go back to wherever the flow started.
  useEffect(() => {
    if (provider !== 'google' || !result) return;
    void client.invalidateQueries({ queryKey: keys.connections });
    const returnTo = readPref<string | null>(GOOGLE_RETURN_KEY, null);
    writePref(GOOGLE_RETURN_KEY, undefined);
    if (returnTo && returnTo !== '/connections/google' && returnTo.startsWith('/') && !returnTo.startsWith('//')) {
      void navigate(`${returnTo}?${params.toString()}`, { replace: true });
    }
  }, [provider, result, client, navigate, params]);

  if (!['google', 'twilio', 'openai', 'anthropic'].includes(provider)) return <Navigate to="/connections" replace />;
  const name = PROVIDER_NAMES[provider] ?? provider;

  return (
    <Page>
      <PageHeader title={name} purpose={PURPOSE[provider]} breadcrumbs={[{ label: 'Connections', to: '/connections' }, { label: name }]} />
      {provider === 'google' && result === 'connected' ? (
        <Banner tone="success" title="Google connected">
          Review the granted access below. Tokens are stored encrypted on this device.
        </Banner>
      ) : null}
      {provider === 'google' && result === 'error' ? (
        <Banner tone="danger" role="alert" title="Google sign-in did not finish">
          {googleErrorText(code)} <span className="diag-code">Diagnostic code: {code ?? 'OAUTH_ERROR'}</span>
        </Banner>
      ) : null}
      <Card className="form-width">
        {provider === 'google' ? <GoogleConnect returnTo="/connections/google" /> : null}
        {provider === 'twilio' ? <TwilioForm /> : null}
        {provider === 'openai' || provider === 'anthropic' ? <ProviderKeyForm provider={provider} /> : null}
      </Card>
      {provider === 'twilio' ? <VoiceCallCard connected={twilioConnected} /> : null}
    </Page>
  );
}
