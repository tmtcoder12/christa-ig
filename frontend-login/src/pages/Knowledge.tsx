import { FormEvent, useEffect, useState } from 'react';
import { createKnowledgeChunk, fetchKnowledgeChunks } from '../lib/backend';
import { useAccountContext } from '../lib/accountContext';
import { useAuth } from '../lib/auth';
import type { KnowledgeChunk, KnowledgeChunkFilters, KnowledgeChunkPagination } from '../types';

const PAGE_SIZE = 10;

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

function getChunkCategory(chunk: KnowledgeChunk) {
  const category = chunk.extra_metadata?.category;
  return typeof category === 'string' && category.trim() ? category : null;
}

export function Knowledge() {
  const { session } = useAuth();
  const { selectedInstagramAccount, selectedInstagramAccountId } = useAccountContext();
  const [chunks, setChunks] = useState<KnowledgeChunk[]>([]);
  const [text, setText] = useState('');
  const [title, setTitle] = useState('');
  const [type, setType] = useState('');
  const [category, setCategory] = useState('');
  const [sourceUrl, setSourceUrl] = useState('');
  const [pagePath, setPagePath] = useState('');
  const [selectedType, setSelectedType] = useState('');
  const [selectedCategory, setSelectedCategory] = useState('');
  const [page, setPage] = useState(1);
  const [pagination, setPagination] = useState<KnowledgeChunkPagination>({
    page: 1,
    page_size: PAGE_SIZE,
    has_more: false,
  });
  const [filters, setFilters] = useState<KnowledgeChunkFilters>({ types: [], categories: [] });
  const [loading, setLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  async function loadChunks(nextPage = page) {
    if (!session?.access_token || !selectedInstagramAccountId) {
      setChunks([]);
      setFilters({ types: [], categories: [] });
      setPagination({ page: 1, page_size: PAGE_SIZE, has_more: false });
      return;
    }

    setLoading(true);
    setError(null);
    try {
      const response = await fetchKnowledgeChunks(
        {
          instagram_account_id: selectedInstagramAccountId,
          page: nextPage,
          page_size: PAGE_SIZE,
          type: selectedType || null,
          category: selectedCategory || null,
        },
        session.access_token,
      );
      const { chunks: nextChunks, filters: nextFilters, pagination: nextPagination } = response;
      setChunks(nextChunks);
      setFilters(nextFilters);
      setPagination(nextPagination);
      setPage(nextPagination.page);
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
  }, [page, selectedCategory, selectedInstagramAccountId, selectedType, session?.access_token]);

  useEffect(() => {
    setSelectedType('');
    setSelectedCategory('');
    setPage(1);
  }, [selectedInstagramAccountId]);

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
          category: optionalValue(category),
          source_url: optionalValue(sourceUrl),
          page_path: optionalValue(pagePath),
        },
        session.access_token,
      );
      setText('');
      setTitle('');
      setType('');
      setCategory('');
      setSourceUrl('');
      setPagePath('');
      setNotice('Knowledge chunk added.');
      setPage(1);
      await loadChunks(1);
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
            Category
            <input value={category} onChange={(event) => setCategory(event.target.value)} placeholder="menu" />
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
            {selectedInstagramAccount.username
              ? `@${selectedInstagramAccount.username}`
              : selectedInstagramAccount.name}
            .
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
            <h2>{loading ? 'Loading' : `${chunks.length} shown`}</h2>
          </div>
          <button
            type="button"
            className="secondary-button"
            onClick={() => loadChunks()}
            disabled={loading || !selectedInstagramAccountId}
          >
            Refresh
          </button>
        </div>

        <div className="knowledge-controls">
          <label>
            Type
            <select
              value={selectedType}
              onChange={(event) => {
                setSelectedType(event.target.value);
                setPage(1);
              }}
            >
              <option value="">All types</option>
              {filters.types.map((filterType) => (
                <option key={filterType} value={filterType}>
                  {filterType}
                </option>
              ))}
            </select>
          </label>

          <label>
            Category
            <select
              value={selectedCategory}
              onChange={(event) => {
                setSelectedCategory(event.target.value);
                setPage(1);
              }}
            >
              <option value="">All categories</option>
              {filters.categories.map((filterCategory) => (
                <option key={filterCategory} value={filterCategory}>
                  {filterCategory}
                </option>
              ))}
            </select>
          </label>
        </div>

        {!loading && !chunks.length ? (
          <p className="inline-state">No knowledge chunks found for this Instagram account.</p>
        ) : null}

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
                    <dt>Category</dt>
                    <dd>{getChunkCategory(chunk) || 'Not set'}</dd>
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

        <div className="pagination-controls">
          <button
            type="button"
            className="secondary-button"
            onClick={() => setPage((current) => Math.max(1, current - 1))}
            disabled={loading || pagination.page <= 1}
          >
            Previous
          </button>
          <span>Page {pagination.page}</span>
          <button
            type="button"
            className="secondary-button"
            onClick={() => setPage((current) => current + 1)}
            disabled={loading || !pagination.has_more}
          >
            Next
          </button>
        </div>
      </section>
    </section>
  );
}
