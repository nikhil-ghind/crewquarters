import { ArrowUpRight, Mail, Phone, Sparkles } from 'lucide-react';
import type { ReactNode } from 'react';
import { Link } from 'react-router';
import { useConnections } from '../../api/queries';
import { SkeletonBlock } from '../../components/Feedback';
import { Card, Page, PageHeader } from '../../components/Layout';
import { QueryView } from '../../components/QueryView';
import { StatusBadge } from '../../components/StatusBadge';
import { CONNECTION_STATUS } from '../../lib/status';
import { CallbackUrls } from './CallbackUrls';
import { LastChecked } from './forms';

const DESCRIPTIONS: Record<string, { text: string; icon: ReactNode; cloud?: boolean }> = {
  google: { text: 'Gmail (read-only) and Google Sheets for the demo agents.', icon: <Mail size={20} aria-hidden="true" /> },
  twilio: { text: 'Places fixed-script phone calls after you approve them.', icon: <Phone size={20} aria-hidden="true" /> },
  openai: { text: 'Optional cloud models. Data sent to OpenAI leaves this device.', icon: <Sparkles size={20} aria-hidden="true" />, cloud: true },
  anthropic: { text: 'Optional cloud models. Data sent to Anthropic leaves this device.', icon: <Sparkles size={20} aria-hidden="true" />, cloud: true },
};

/** The demo's own connections first, then the optional cloud providers. */
const ORDER = ['google', 'twilio', 'openai', 'anthropic'];

export default function ConnectionsPage() {
  const connections = useConnections();
  return (
    <Page>
      <PageHeader title="Connections" purpose="Services your agents may use. Secrets are stored encrypted and never shown after you save them." />
      <QueryView query={connections} errorTitle="Could not load connections" loading={<div className="grid-2">{[0, 1, 2, 3].map((i) => <SkeletonBlock key={i} lines={2} />)}</div>}>
        {(list) => (
          <div className="grid-2">
            {[...list].sort((a, b) => ORDER.indexOf(a.provider) - ORDER.indexOf(b.provider)).map((c) => {
              const d = DESCRIPTIONS[c.provider];
              const action = c.status === 'NEEDS_ATTENTION' && c.provider === 'google' ? 'Reconnect Google' : c.status === 'NOT_CONNECTED' ? `Set up ${c.displayName}` : `Manage ${c.displayName}`;
              return (
                <article key={c.provider} className={`card stack-sm ${c.status === 'NEEDS_ATTENTION' ? 'card-attention' : ''}`} aria-labelledby={`conn-${c.provider}`}>
                  <div className="row-between">
                    <h2 id={`conn-${c.provider}`} className="card-title row">
                      {d?.icon}
                      {c.displayName}
                    </h2>
                    <StatusBadge status={CONNECTION_STATUS[c.status]} context={c.displayName} />
                  </div>
                  <p className="muted">{d?.text}</p>
                  {d?.cloud ? (
                    <span className="badge chip-cloud" style={{ alignSelf: 'flex-start' }}>
                      <ArrowUpRight size={14} aria-hidden="true" />
                      Cloud · {c.displayName}
                    </span>
                  ) : null}
                  {c.account ? <span>Account: {c.account}</span> : null}
                  {c.detail ? <span className="muted">{c.detail}</span> : null}
                  <LastChecked connection={c} />
                  <Link className="btn btn-secondary" style={{ alignSelf: 'flex-start' }} to={`/connections/${c.provider}`}>
                    {action}
                  </Link>
                </article>
              );
            })}
          </div>
        )}
      </QueryView>
      <Card title="Callback addresses" subtitle="Register these exact addresses with Google and Twilio.">
        <CallbackUrls />
      </Card>
    </Page>
  );
}
