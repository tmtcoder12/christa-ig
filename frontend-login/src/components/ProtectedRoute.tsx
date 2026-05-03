import { Navigate, Outlet, useLocation } from 'react-router-dom';
import { useAuth } from '../lib/auth';
import { hasSupabaseConfig } from '../lib/supabase';

export function ProtectedRoute() {
  const { user, loading } = useAuth();
  const location = useLocation();

  if (!hasSupabaseConfig) {
    return (
      <main className="state-page">
        <section className="state-panel">
          <h1>Supabase is not configured</h1>
          <p>Create `frontend-login/.env.local` with `VITE_SUPABASE_URL` and `VITE_SUPABASE_ANON_KEY`.</p>
        </section>
      </main>
    );
  }

  if (loading) {
    return (
      <main className="state-page">
        <section className="state-panel">
          <h1>Loading</h1>
        </section>
      </main>
    );
  }

  if (!user) {
    return <Navigate to="/signin" replace state={{ from: location }} />;
  }

  return <Outlet />;
}
