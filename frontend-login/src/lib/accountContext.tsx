import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import type { ReactNode } from 'react';
import { supabase } from './supabase';
import { useAuth } from './auth';
import type { Business, InstagramAccount } from '../types';

type AccountContextValue = {
  loading: boolean;
  error: string | null;
  businesses: Business[];
  instagramAccounts: InstagramAccount[];
  selectedBusinessId: string;
  selectedInstagramAccountId: string;
  selectedBusiness: Business | null;
  selectedInstagramAccount: InstagramAccount | null;
  setSelectedBusinessId: (id: string) => void;
  setSelectedInstagramAccountId: (id: string) => void;
  refresh: () => Promise<void>;
};

const AccountContext = createContext<AccountContextValue | undefined>(undefined);

export function AccountProvider({ children }: { children: ReactNode }) {
  const { user } = useAuth();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [businesses, setBusinesses] = useState<Business[]>([]);
  const [instagramAccounts, setInstagramAccounts] = useState<InstagramAccount[]>([]);
  const [selectedBusinessId, setSelectedBusinessId] = useState('');
  const [selectedInstagramAccountId, setSelectedInstagramAccountId] = useState('');

  const refresh = useCallback(async () => {
    if (!user) {
      setBusinesses([]);
      setInstagramAccounts([]);
      setSelectedBusinessId('');
      setSelectedInstagramAccountId('');
      return;
    }

    if (!supabase) {
      setError('Supabase is not configured');
      return;
    }

    setLoading(true);
    setError(null);

    const { data: businessRows, error: businessError } = await supabase
      .from('businesses')
      .select('id, slug, name')
      .order('name', { ascending: true, nullsFirst: false });

    if (businessError) {
      setError(businessError.message);
      setLoading(false);
      return;
    }

    const nextBusinesses = businessRows ?? [];
    setBusinesses(nextBusinesses);

    setSelectedBusinessId((current) =>
      current && nextBusinesses.some((business) => business.id === current)
        ? current
        : nextBusinesses[0]?.id ?? '',
    );
    setLoading(false);
  }, [user]);

  useEffect(() => {
    refresh().catch((refreshError) => {
      setError(refreshError instanceof Error ? refreshError.message : 'Failed to load account context');
      setLoading(false);
    });
  }, [refresh]);

  useEffect(() => {
    if (!user || !selectedBusinessId) {
      setInstagramAccounts([]);
      setSelectedInstagramAccountId('');
      return;
    }

    let isMounted = true;
    setLoading(true);

    if (!supabase) {
      setError('Supabase is not configured');
      return;
    }

    supabase
      .from('instagram_accounts')
      .select('id, business_id, instagram_user_id, username, name, status')
      .eq('business_id', selectedBusinessId)
      .order('username', { ascending: true, nullsFirst: false })
      .then(({ data, error: accountError }) => {
        if (!isMounted) {
          return;
        }
        if (accountError) {
          setError(accountError.message);
          setLoading(false);
          return;
        }
        const nextAccounts = data ?? [];
        setInstagramAccounts(nextAccounts);
        setSelectedInstagramAccountId((current) =>
          current && nextAccounts.some((account) => account.id === current)
            ? current
            : nextAccounts[0]?.id ?? '',
        );
        setLoading(false);
      });

    return () => {
      isMounted = false;
    };
  }, [selectedBusinessId, user]);

  const selectedBusiness = businesses.find((business) => business.id === selectedBusinessId) ?? null;
  const selectedInstagramAccount =
    instagramAccounts.find((account) => account.id === selectedInstagramAccountId) ?? null;

  const value = useMemo(
    () => ({
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
      refresh,
    }),
    [
      businesses,
      error,
      instagramAccounts,
      loading,
      refresh,
      selectedBusiness,
      selectedBusinessId,
      selectedInstagramAccount,
      selectedInstagramAccountId,
    ],
  );

  return <AccountContext.Provider value={value}>{children}</AccountContext.Provider>;
}

export function useAccountContext() {
  const context = useContext(AccountContext);
  if (!context) {
    throw new Error('useAccountContext must be used inside AccountProvider');
  }
  return context;
}
