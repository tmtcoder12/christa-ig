import { FormEvent, useEffect, useMemo, useState } from 'react';
import { createPromotionSetup, fetchPromotionSetup } from '../lib/backend';
import { useAccountContext } from '../lib/accountContext';
import { useAuth } from '../lib/auth';
import type { PromotionSetup } from '../types';

function parseKeywords(value: string) {
  return value
    .split(/[\n,]+/)
    .map((keyword) => keyword.trim())
    .filter(Boolean);
}

function datetimeLocalToIso(value: string) {
  return value ? new Date(value).toISOString() : null;
}

function formatTimestamp(value: string | null) {
  if (!value) {
    return 'Not set';
  }
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(new Date(value));
}

function isActiveSetup(setup: PromotionSetup | null) {
  return setup?.status === 'pending' || setup?.status === 'polling';
}

export function AddPromotion() {
  const { session } = useAuth();
  const { selectedInstagramAccount, selectedInstagramAccountId } = useAccountContext();
  const [triggerKeywords, setTriggerKeywords] = useState('');
  const [automationStartsAt, setAutomationStartsAt] = useState('');
  const [automationEndsAt, setAutomationEndsAt] = useState('');
  const [promoCodeValidDurationHours, setPromoCodeValidDurationHours] = useState('');
  const [commentReplyText, setCommentReplyText] = useState('Sent you a DM!');
  const [dmPrompt, setDmPrompt] = useState('');
  const [codePrefix, setCodePrefix] = useState('');
  const [setup, setSetup] = useState<PromotionSetup | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const keywords = useMemo(() => parseKeywords(triggerKeywords), [triggerKeywords]);

  useEffect(() => {
    if (!setup || !isActiveSetup(setup) || !session?.access_token) {
      return;
    }

    const intervalId = window.setInterval(() => {
      fetchPromotionSetup(setup.id, session.access_token)
        .then(({ setup: nextSetup }) => setSetup(nextSetup))
        .catch((statusError) => {
          setError(statusError instanceof Error ? statusError.message : 'Unable to refresh promotion status');
        });
    }, 5000);

    return () => window.clearInterval(intervalId);
  }, [session?.access_token, setup]);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);

    if (!session?.access_token) {
      setError('You must be signed in to create a promotion.');
      return;
    }
    if (!selectedInstagramAccountId) {
      setError('Select an Instagram account before creating a promotion.');
      return;
    }
    if (!keywords.length) {
      setError('Add at least one trigger keyword.');
      return;
    }

    setSubmitting(true);
    try {
      const duration = promoCodeValidDurationHours ? Number(promoCodeValidDurationHours) : null;
      const { setup: createdSetup } = await createPromotionSetup(
        {
          instagram_account_id: selectedInstagramAccountId,
          trigger_keywords: keywords,
          automation_starts_at: datetimeLocalToIso(automationStartsAt),
          automation_ends_at: datetimeLocalToIso(automationEndsAt),
          promo_code_valid_duration_hours: duration,
          comment_reply_text: commentReplyText,
          dm_prompt: dmPrompt.trim() || null,
          code_prefix: codePrefix.trim() || null,
        },
        session.access_token,
      );
      setSetup(createdSetup);
    } catch (submitError) {
      setError(submitError instanceof Error ? submitError.message : 'Unable to create promotion setup');
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <section className="page-stack">
      <div className="page-heading">
        <p className="eyebrow">Promotions</p>
        <h1>Add Promotion</h1>
      </div>

      <form className="form-panel" onSubmit={handleSubmit}>
        <div className="form-grid">
          <label className="full-width-field">
            Trigger keywords
            <textarea
              value={triggerKeywords}
              onChange={(event) => setTriggerKeywords(event.target.value)}
              placeholder="DM&#10;Menu&#10;Promo"
              rows={4}
              required
            />
          </label>

          <label>
            Automation starts
            <input
              type="datetime-local"
              value={automationStartsAt}
              onChange={(event) => setAutomationStartsAt(event.target.value)}
            />
          </label>

          <label>
            Automation ends
            <input
              type="datetime-local"
              value={automationEndsAt}
              onChange={(event) => setAutomationEndsAt(event.target.value)}
            />
          </label>

          <label>
            Code valid hours
            <input
              type="number"
              min={1}
              value={promoCodeValidDurationHours}
              onChange={(event) => setPromoCodeValidDurationHours(event.target.value)}
              placeholder="48"
            />
          </label>

          <label>
            Promo code prefix
            <input
              value={codePrefix}
              onChange={(event) => setCodePrefix(event.target.value)}
              placeholder="KOSOO"
            />
          </label>

          <label className="full-width-field">
            Comment reply text
            <input
              value={commentReplyText}
              onChange={(event) => setCommentReplyText(event.target.value)}
              required
            />
          </label>

          <label className="full-width-field">
            DM prompt
            <textarea
              value={dmPrompt}
              onChange={(event) => setDmPrompt(event.target.value)}
              placeholder="Optional campaign instructions for the private DM"
              rows={4}
            />
          </label>
        </div>

        {selectedInstagramAccount ? (
          <p className="inline-state">
            Polling will watch for the next unseen post from{' '}
            {selectedInstagramAccount.username ? `@${selectedInstagramAccount.username}` : selectedInstagramAccount.name}.
          </p>
        ) : null}

        {error ? <p className="form-error">{error}</p> : null}

        <button type="submit" disabled={submitting || !selectedInstagramAccountId || isActiveSetup(setup)}>
          {submitting ? 'Creating setup...' : 'Start promotion polling'}
        </button>
      </form>

      {setup ? (
        <section className="status-panel">
          <div>
            <p className="eyebrow">Setup status</p>
            <h2>{setup.status}</h2>
          </div>
          <dl>
            <div>
              <dt>Setup ID</dt>
              <dd>{setup.id}</dd>
            </div>
            <div>
              <dt>Polling expires</dt>
              <dd>{formatTimestamp(setup.poll_expires_at)}</dd>
            </div>
            <div>
              <dt>Last polled</dt>
              <dd>{formatTimestamp(setup.last_polled_at)}</dd>
            </div>
            <div>
              <dt>Media ID</dt>
              <dd>{setup.found_instagram_media_id || 'Waiting for new post'}</dd>
            </div>
            <div>
              <dt>Caption</dt>
              <dd>{setup.found_caption || 'Not found yet'}</dd>
            </div>
          </dl>
          {setup.error_message ? <p className="form-error">{setup.error_message}</p> : null}
        </section>
      ) : null}
    </section>
  );
}
