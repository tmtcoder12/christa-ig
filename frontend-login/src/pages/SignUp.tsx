import { FormEvent, useState } from 'react';
import { Link, Navigate } from 'react-router-dom';
import { useAuth } from '../lib/auth';

export function SignUp() {
  const { user, signUp } = useAuth();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [needsEmailConfirmation, setNeedsEmailConfirmation] = useState(false);

  if (user) {
    return <Navigate to="/redeem" replace />;
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    setNeedsEmailConfirmation(false);
    try {
      const result = await signUp(email, password);
      setNeedsEmailConfirmation(result.needsEmailConfirmation);
    } catch (signUpError) {
      setError(signUpError instanceof Error ? signUpError.message : 'Unable to sign up');
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="auth-page">
      <section className="auth-panel">
        <div>
          <p className="eyebrow">Christa IG</p>
          <h1>Create account</h1>
        </div>

        {needsEmailConfirmation ? (
          <div className="notice">
            Check your email to confirm your account. After confirmation, sign in here and your business access will
            appear once your user is linked in Supabase.
          </div>
        ) : null}

        <form onSubmit={handleSubmit} className="auth-form">
          <label>
            Email
            <input
              autoComplete="email"
              type="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              required
            />
          </label>

          <label>
            Password
            <input
              autoComplete="new-password"
              type="password"
              minLength={8}
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              required
            />
          </label>

          {error ? <p className="form-error">{error}</p> : null}

          <button type="submit" disabled={submitting}>
            {submitting ? 'Creating account...' : 'Create account'}
          </button>
        </form>

        <p className="auth-switch">
          Already have an account? <Link to="/signin">Sign in</Link>
        </p>
      </section>
    </main>
  );
}
