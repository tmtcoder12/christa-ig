import { NavLink, Outlet, useNavigate } from 'react-router-dom';
import { AccountProvider, useAccountContext } from '../lib/accountContext';
import { useAuth } from '../lib/auth';

function ShellContent() {
  const { user, signOut } = useAuth();
  const navigate = useNavigate();
  const {
    loading,
    error,
    businesses,
    instagramAccounts,
    selectedBusinessId,
    selectedInstagramAccountId,
    selectedBusiness,
    selectedInstagramAccount,
    setSelectedBusinessId,
    setSelectedInstagramAccountId,
  } = useAccountContext();

  async function handleSignOut() {
    await signOut();
    navigate('/signin', { replace: true });
  }

  return (
    <div className="app-shell">
      <header className="topbar">
        <div>
          <p className="eyebrow">Christa IG</p>
          <strong>{user?.email}</strong>
        </div>

        <div className="context-controls">
          <label>
            Business
            <select
              value={selectedBusinessId}
              onChange={(event) => setSelectedBusinessId(event.target.value)}
              disabled={!businesses.length}
            >
              {businesses.length ? (
                businesses.map((business) => (
                  <option key={business.id} value={business.id}>
                    {business.name || business.slug || business.id}
                  </option>
                ))
              ) : (
                <option value="">No business access</option>
              )}
            </select>
          </label>

          <label>
            Instagram
            <select
              value={selectedInstagramAccountId}
              onChange={(event) => setSelectedInstagramAccountId(event.target.value)}
              disabled={!instagramAccounts.length}
            >
              {instagramAccounts.length ? (
                instagramAccounts.map((account) => (
                  <option key={account.id} value={account.id}>
                    {account.username ? `@${account.username}` : account.name || account.instagram_user_id}
                  </option>
                ))
              ) : (
                <option value="">No Instagram accounts</option>
              )}
            </select>
          </label>

          <button type="button" className="secondary-button" onClick={handleSignOut}>
            Sign out
          </button>
        </div>
      </header>

      <div className="app-body">
        <aside className="sidebar">
          <nav>
            <NavLink to="/redeem">Redeem</NavLink>
            <NavLink to="/add-promotion">Add Promotion</NavLink>
            <NavLink to="/knowledge">Knowledge</NavLink>
          </nav>
        </aside>

        <main className="content">
          {loading ? <p className="inline-state">Loading account context...</p> : null}
          {error ? <p className="form-error">{error}</p> : null}
          {!loading && !businesses.length ? (
            <section className="empty-state">
              <h1>No business access</h1>
              <p>
                Your user is signed in, but it is not linked to a business yet. Add this profile to `business_users` in
                Supabase to unlock business and Instagram account data.
              </p>
            </section>
          ) : null}
          {!loading && businesses.length && !instagramAccounts.length ? (
            <section className="empty-state">
              <h1>No Instagram accounts</h1>
              <p>{selectedBusiness?.name || 'This business'} has no Instagram accounts available to this user yet.</p>
            </section>
          ) : null}
          {businesses.length && instagramAccounts.length ? (
            <>
              <div className="context-strip">
                <span>{selectedBusiness?.name || selectedBusiness?.slug || 'Business selected'}</span>
                <span>
                  {selectedInstagramAccount?.username ? `@${selectedInstagramAccount.username}` : 'IG selected'}
                </span>
              </div>
              <Outlet />
            </>
          ) : null}
        </main>
      </div>
    </div>
  );
}

export function AppShell() {
  return (
    <AccountProvider>
      <ShellContent />
    </AccountProvider>
  );
}
