import { FormEvent, useEffect, useState } from 'react';
import { createKnowledgeChunk, fetchKnowledgeChunks } from '../lib/backend';
import { useAccountContext } from '../lib/accountContext';
import { useAuth } from '../lib/auth';
import type { KnowledgeChunk } from '../types';

function optionalValue(value: string) {
  const trimmed = value.trim();
  return trimmed || null;
}

function formatTimestamp(value: string) {
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(new Date(value));
}

export function Knowledge() {
  const { session } = useAuth();
  const { selectedInstagramAccount, selectedInstagramAccountId } = useAccountContext();
  const [chunks, setChunks] = useState<KnowledgeChunk[]>([]);
  const [text, setText] = useState('');
  const [title, setTitle] = useState('');
  const [type, setType] = useState('');
  const [sourceUrl, setSourceUrl] = useState('');
  const [pagePath, setPagePath] = useState('');
  const [loading, setLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  async function loadChunks() {
    if (!session?.access_token || !selectedInstagramAccountId) {
      setChunks([]);
      return;
    }

    setLoading(true);
    setError(null);
    try {
      const { chunks: nextChunks } = await fetchKnowledgeChunks(selectedInstagramAccountId, session.access_token);
      setChunks(nextChunks);
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : 'Unable to load knowledge chunks');
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    loadChunks().catch((loadError) => {
      setError(loadError instanceof Error ? loadError.message : 'Unable to load knowledge chunks');
      setLoading(false);
    });
  }, [selectedInstagramAccountId, session?.access_token]);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setNotice(null);

    if (!session?.access_token) {
      setError('You must be signed in to add knowledge.');
      return;
    }
    if (!selectedInstagramAccountId) {
      setError('Select an Instagram account before adding knowledge.');
      return;
    }
    if (!text.trim()) {
      setError('Knowledge text is required.');
      return;
    }

    setSubmitting(true);
    try {
      await createKnowledgeChunk(
        {
          instagram_account_id: selectedInstagramAccountId,
          text: text.trim(),
          title: optionalValue(title),
          type: optionalValue(type),
          source_url: optionalValue(sourceUrl),
          page_path: optionalValue(pagePath),
        },
        session.access_token,
      );
      setText('');
      setTitle('');
      setType('');
      setSourceUrl('');
      setPagePath('');
      setNotice('Knowledge chunk added.');
      await loadChunks();
    } catch (submitError) {
      setError(submitError instanceof Error ? submitError.message : 'Unable to add knowledge chunk');
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <section className="page-stack">
      <div className="page-heading">
        <p className="eyebrow">RAG</p>
        <h1>Knowledge</h1>
      </div>

      <form className="form-panel" onSubmit={handleSubmit}>
        <div className="form-grid">
          <label className="full-width-field">
            Knowledge text
            <textarea
              value={text}
              onChange={(event) => setText(event.target.value)}
              placeholder="Add a useful fact, policy, menu detail, service description, or FAQ answer."
              rows={6}
              required
            />
          </label>

          <label>
            Title
            <input value={title} onChange={(event) => setTitle(event.target.value)} placeholder="Lunch menu" />
          </label>

          <label>
            Type
            <input value={type} onChange={(event) => setType(event.target.value)} placeholder="faq" />
          </label>

          <label>
            Source URL
            <input
              value={sourceUrl}
              onChange={(event) => setSourceUrl(event.target.value)}
              placeholder="https://example.com/menu"
            />
          </label>

          <label>
            Page path
            <input value={pagePath} onChange={(event) => setPagePath(event.target.value)} placeholder="/menu" />
          </label>
        </div>

        {selectedInstagramAccount ? (
          <p className="inline-state">
            New chunks will be embedded for{' '}
            {selectedInstagramAccount.username ? `@${selectedInstagramAccount.username}` : selectedInstagramAccount.name}.
          </p>
        ) : null}

        {notice ? <p className="notice">{notice}</p> : null}
        {error ? <p className="form-error">{error}</p> : null}

        <button type="submit" disabled={submitting || !selectedInstagramAccountId || !text.trim()}>
          {submitting ? 'Adding knowledge...' : 'Add knowledge chunk'}
        </button>
      </form>

      <section className="status-panel knowledge-list">
        <div className="knowledge-list-header">
          <div>
            <p className="eyebrow">Chunks</p>
            <h2>{loading ? 'Loading' : `${chunks.length} knowledge chunk${chunks.length === 1 ? '' : 's'}`}</h2>
          </div>
          <button type="button" className="secondary-button" onClick={loadChunks} disabled={loading || !selectedInstagramAccountId}>
            Refresh
          </button>
        </div>

        {!loading && !chunks.length ? <p className="inline-state">No knowledge chunks found for this Instagram account.</p> : null}

        {chunks.length ? (
          <div className="knowledge-grid">
            {chunks.map((chunk) => (
              <article className="knowledge-card" key={chunk.id}>
                <div>
                  <p className="eyebrow">{chunk.type || 'Knowledge'}</p>
                  <h3>{chunk.title || 'Untitled chunk'}</h3>
                </div>
                <p>{chunk.text}</p>
                <dl>
                  <div>
                    <dt>Created</dt>
                    <dd>{formatTimestamp(chunk.created_at)}</dd>
                  </div>
                  <div>
                    <dt>Source</dt>
                    <dd>{chunk.source_url || chunk.page_path || 'Not set'}</dd>
                  </div>
                </dl>
              </article>
            ))}
          </div>
        ) : null}
      </section>
    </section>
  );
}
