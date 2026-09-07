import { FormEvent, useMemo, useState } from 'react';
import { redeemPromoCode } from '../lib/backend';
import { useAccountContext } from '../lib/accountContext';
import { useAuth } from '../lib/auth';
import type { RedeemPromoCodeResponse } from '../types';

function formatTimestamp(value: string | null | undefined) {
  if (!value) {
    return 'Not set';
  }
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(new Date(value));
}

function getResultCopy(result: RedeemPromoCodeResponse['result']) {
  switch (result) {
    case 'redeemed':
      return {
        title: 'Code redeemed',
        message: 'This promo code was successfully redeemed.',
      };
    case 'expired':
      return {
        title: 'Code expired',
        message: 'This promo code exists, but its validity window has ended.',
      };
    case 'already_redeemed':
      return {
        title: 'Already redeemed',
        message: 'This promo code has already been redeemed.',
      };
    case 'void':
      return {
        title: 'Code void',
        message: 'This promo code has been voided and cannot be redeemed.',
      };
    case 'not_found':
    default:
      return {
        title: 'Code not found',
        message: 'No promo code matched the selected Instagram account.',
      };
  }
}

export function Redeem() {
  const { session } = useAuth();
  const { selectedInstagramAccount, selectedInstagramAccountId } = useAccountContext();
  const [code, setCode] = useState('');
  const [redemptionNotes, setRedemptionNotes] = useState('');
  const [result, setResult] = useState<RedeemPromoCodeResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const normalizedCode = useMemo(() => code.trim().toUpperCase(), [code]);
  const resultCopy = result ? getResultCopy(result.result) : null;

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setResult(null);

    if (!session?.access_token) {
      setError('You must be signed in to redeem a promo code.');
      return;
    }
    if (!selectedInstagramAccountId) {
      setError('Select an Instagram account before redeeming a promo code.');
      return;
    }
    if (!normalizedCode) {
      setError('Enter a promo code to redeem.');
      return;
    }

    setSubmitting(true);
    try {
      const redemption = await redeemPromoCode(
        {
          instagram_account_id: selectedInstagramAccountId,
          code: normalizedCode,
          redemption_notes: redemptionNotes.trim() || null,
        },
        session.access_token,
      );
      setResult(redemption);
      if (redemption.result === 'redeemed') {
        setRedemptionNotes('');
      }
    } catch (redeemError) {
      setError(redeemError instanceof Error ? redeemError.message : 'Unable to redeem promo code');
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <section className="page-stack">
      <div className="page-heading">
        <p className="eyebrow">Promotions</p>
        <h1>Redeem</h1>
      </div>

      <form className="form-panel redeem-panel" onSubmit={handleSubmit}>
        <label>
          Promo code
          <input
            value={code}
            onChange={(event) => setCode(event.target.value)}
            placeholder="PROMO-7F3KQ2"
            autoComplete="off"
            required
          />
        </label>

        <label>
          Staff notes
          <textarea
            value={redemptionNotes}
            onChange={(event) => setRedemptionNotes(event.target.value)}
            placeholder="What did they order? Anything useful for follow-up?"
            maxLength={1000}
            rows={4}
          />
        </label>

        {selectedInstagramAccount ? (
          <p className="inline-state">
            Redeeming against{' '}
            {selectedInstagramAccount.username
              ? `@${selectedInstagramAccount.username}`
              : selectedInstagramAccount.name}
            .
          </p>
        ) : (
          <p className="inline-state">Select an Instagram account before redeeming a code.</p>
        )}

        {error ? <p className="form-error">{error}</p> : null}

        <button type="submit" disabled={submitting || !selectedInstagramAccountId}>
          {submitting ? 'Redeeming...' : 'Redeem code'}
        </button>
      </form>

      {result && resultCopy ? (
        <section className={`status-panel redeem-result redeem-result-${result.result}`}>
          <div>
            <p className="eyebrow">Redemption result</p>
            <h2>{resultCopy.title}</h2>
            <p>{resultCopy.message}</p>
          </div>
          <dl>
            <div>
              <dt>Code</dt>
              <dd>{result.promo_code?.code || normalizedCode}</dd>
            </div>
            <div>
              <dt>Status</dt>
              <dd>{result.promo_code?.status || result.result.replace('_', ' ')}</dd>
            </div>
            <div>
              <dt>Expires</dt>
              <dd>{formatTimestamp(result.promo_code?.expires_at)}</dd>
            </div>
            <div>
              <dt>Redeemed</dt>
              <dd>{formatTimestamp(result.promo_code?.redeemed_at)}</dd>
            </div>
            {result.followup ? (
              <div>
                <dt>Follow-up</dt>
                <dd>{formatTimestamp(result.followup.scheduled_for)}</dd>
              </div>
            ) : null}
            {result.customer_profile ? (
              <div>
                <dt>Customer profile</dt>
                <dd>
                  Updated
                  {result.customer_profile.redeem_count > 1
                    ? ` (${result.customer_profile.redeem_count} redemptions)`
                    : ''}
                </dd>
              </div>
            ) : null}
          </dl>
        </section>
      ) : null}
    </section>
  );
}
