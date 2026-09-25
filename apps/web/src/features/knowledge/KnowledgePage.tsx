import { useMutation, useQueryClient } from '@tanstack/react-query';
import { BookOpen } from 'lucide-react';
import { useState, type FormEvent } from 'react';
import { Link, useNavigate } from 'react-router';
import { isApiError, remediation } from '../../api/errors';
import { useActionGuard } from '../../api/guards';
import { useIntentKey } from '../../api/mutations';
import { endpoints as pendingApi } from '../../api/endpoints';
import { keys, useKnowledgeBases } from '../../api/queries';
import { Button } from '../../components/Button';
import { EmptyState, SkeletonBlock } from '../../components/Feedback';
import { Field } from '../../components/Field';
import { Card, Page, PageHeader } from '../../components/Layout';
import { LocalityChip } from '../../components/LocalityChip';
import { QueryView } from '../../components/QueryView';
import { formatDateTime } from '../../lib/format';
import { useTimeZone } from '../common/useTimeZone';

export default function KnowledgePage() {
  const kbs = useKnowledgeBases();
  const client = useQueryClient();
  const navigate = useNavigate();
  const guard = useActionGuard();
  const timeZone = useTimeZone();
  const [name, setName] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [key, resetKey] = useIntentKey();
  const create = useMutation({
    mutationFn: () => pendingApi.knowledgeBaseCreate({ name: name.trim() }, key),
    onSuccess: (kb) => {
      resetKey();
      void client.invalidateQueries({ queryKey: keys.knowledgeBases });
      void navigate(`/knowledge/${encodeURIComponent(kb.id)}`);
    },
    onError: (e) => setError(isApiError(e) ? remediation(e) : 'Could not create the knowledge base.'),
  });
  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    if (!name.trim()) {
      setError('Give the knowledge base a name.');
      return;
    }
    setError(null);
    create.mutate();
  };

  return (
    <Page>
      <PageHeader title="Knowledge" purpose="Upload documents, index them on this device, and test what chat and agents can find." />
      <Card title="Create a knowledge base">
        <form className="row" style={{ alignItems: 'flex-end' }} onSubmit={onSubmit} noValidate>
          <div style={{ flex: '1 1 280px' }}>
            <Field label="Name" error={error}>
              <input className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder="Team handbook" />
            </Field>
          </div>
          <Button type="submit" variant="primary" busy={create.isPending} busyLabel="Creating…" disabledReason={guard.offline}>
            Create knowledge base
          </Button>
        </form>
      </Card>
      <QueryView
        query={kbs}
        errorTitle="Could not load knowledge bases"
        loading={<SkeletonBlock />}
        isEmpty={(d) => d.length === 0}
        empty={<EmptyState icon={BookOpen} title="No knowledge bases yet">Create one above, then upload documents to it.</EmptyState>}
      >
        {(list) => (
          <div className="grid-3">
            {list.map((kb) => (
              <article key={kb.id} className="card stack-sm" aria-labelledby={`kb-${kb.id}`}>
                <h2 id={`kb-${kb.id}`} className="card-title">
                  <Link to={`/knowledge/${encodeURIComponent(kb.id)}`}>{kb.name}</Link>
                </h2>
                <LocalityChip provider="local" long />
                <span className="muted">Created {formatDateTime(kb.createdAt, timeZone)}</span>
                <span className="muted mono" style={{ fontSize: 12 }}>
                  {kb.embeddingProfile}
                </span>
              </article>
            ))}
          </div>
        )}
      </QueryView>
    </Page>
  );
}
