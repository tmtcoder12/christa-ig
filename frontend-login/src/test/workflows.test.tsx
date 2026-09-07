import type { ReactNode } from 'react';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Outlet, Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  auth: {
    session: { access_token: 'access-token' },
    user: { id: 'user-1', email: 'staff@example.com' },
    loading: false,
    signOut: vi.fn(),
  },
  account: {
    loading: false,
    error: null as string | null,
    businesses: [
      { id: 'business-1', name: 'First Business', slug: 'first' },
      { id: 'business-2', name: 'Second Business', slug: 'second' },
    ],
    instagramAccounts: [
      {
        id: 'ig-1',
        business_id: 'business-1',
        instagram_user_id: '1784',
        username: 'example_shop',
        name: 'Example Shop',
        status: 'connected',
      },
    ],
    selectedBusinessId: 'business-1',
    selectedInstagramAccountId: 'ig-1',
    selectedBusiness: { id: 'business-1', name: 'First Business', slug: 'first' },
    selectedInstagramAccount: {
      id: 'ig-1',
      business_id: 'business-1',
      instagram_user_id: '1784',
      username: 'example_shop',
      name: 'Example Shop',
      status: 'connected',
    },
    setSelectedBusinessId: vi.fn(),
    setSelectedInstagramAccountId: vi.fn(),
    refresh: vi.fn(),
  },
  createPromotionSetup: vi.fn(),
  fetchPromotionSetup: vi.fn(),
  fetchKnowledgeChunks: vi.fn(),
  createKnowledgeChunk: vi.fn(),
  redeemPromoCode: vi.fn(),
}));

vi.mock('../lib/auth', () => ({
  useAuth: () => mocks.auth,
  AuthProvider: ({ children }: { children: ReactNode }) => children,
}));

vi.mock('../lib/accountContext', () => ({
  useAccountContext: () => mocks.account,
  AccountProvider: ({ children }: { children: ReactNode }) => children,
}));

vi.mock('../lib/backend', () => ({
  createPromotionSetup: mocks.createPromotionSetup,
  fetchPromotionSetup: mocks.fetchPromotionSetup,
  fetchKnowledgeChunks: mocks.fetchKnowledgeChunks,
  createKnowledgeChunk: mocks.createKnowledgeChunk,
  redeemPromoCode: mocks.redeemPromoCode,
}));

vi.mock('../lib/supabase', () => ({
  hasSupabaseConfig: true,
  supabaseConfigErrors: [],
}));

import { AppShell } from '../components/AppShell';
import { ProtectedRoute } from '../components/ProtectedRoute';
import { AddPromotion } from '../pages/AddPromotion';
import { Knowledge } from '../pages/Knowledge';
import { Redeem } from '../pages/Redeem';

function renderPage(element: ReactNode) {
  return render(<MemoryRouter>{element}</MemoryRouter>);
}

beforeEach(() => {
  mocks.auth.user = { id: 'user-1', email: 'staff@example.com' };
  mocks.auth.session = { access_token: 'access-token' };
  mocks.auth.loading = false;
  mocks.account.setSelectedBusinessId.mockReset();
  mocks.account.setSelectedInstagramAccountId.mockReset();
  mocks.createPromotionSetup.mockReset();
  mocks.fetchPromotionSetup.mockReset();
  mocks.fetchKnowledgeChunks.mockReset();
  mocks.createKnowledgeChunk.mockReset();
  mocks.redeemPromoCode.mockReset();
});

describe('staff workflows', () => {
  it('redirects signed-out users away from protected routes', () => {
    mocks.auth.user = null as never;
    mocks.auth.session = null as never;

    render(
      <MemoryRouter initialEntries={['/private']}>
        <Routes>
          <Route element={<ProtectedRoute />}>
            <Route path="/private" element={<p>Private</p>} />
          </Route>
          <Route path="/signin" element={<p>Sign in screen</p>} />
        </Routes>
      </MemoryRouter>,
    );

    expect(screen.getByText('Sign in screen')).toBeInTheDocument();
  });

  it('passes business and account selections through the shell', () => {
    render(
      <MemoryRouter initialEntries={['/redeem']}>
        <Routes>
          <Route element={<AppShell />}>
            <Route path="/redeem" element={<Outlet />} />
          </Route>
        </Routes>
      </MemoryRouter>,
    );

    fireEvent.change(screen.getByLabelText('Business'), { target: { value: 'business-2' } });
    fireEvent.change(screen.getByLabelText('Instagram'), { target: { value: 'ig-1' } });

    expect(mocks.account.setSelectedBusinessId).toHaveBeenCalledWith('business-2');
    expect(mocks.account.setSelectedInstagramAccountId).toHaveBeenCalledWith('ig-1');
  });

  it('polls while a newly created promotion is active', async () => {
    vi.useFakeTimers();
    const pendingSetup = {
      id: 'setup-1',
      status: 'polling',
      poll_expires_at: null,
      last_polled_at: null,
      found_instagram_media_id: null,
      found_caption: null,
      error_message: null,
    };
    mocks.createPromotionSetup.mockResolvedValue({ setup: pendingSetup });
    mocks.fetchPromotionSetup.mockResolvedValue({ setup: { ...pendingSetup, status: 'found' } });
    renderPage(<AddPromotion />);

    fireEvent.change(screen.getByLabelText('Trigger mode'), { target: { value: 'restaurant_intent' } });
    fireEvent.click(screen.getByRole('button', { name: 'Start promotion polling' }));
    await act(async () => Promise.resolve());
    expect(mocks.createPromotionSetup).toHaveBeenCalledTimes(1);

    await act(async () => {
      vi.advanceTimersByTime(5_000);
      await Promise.resolve();
    });
    expect(mocks.fetchPromotionSetup).toHaveBeenCalledWith('setup-1', 'access-token');
    vi.useRealTimers();
  });

  it('loads the next knowledge page', async () => {
    mocks.fetchKnowledgeChunks
      .mockResolvedValueOnce({
        chunks: [],
        filters: { types: [], categories: [] },
        pagination: { page: 1, page_size: 10, has_more: true },
      })
      .mockResolvedValueOnce({
        chunks: [],
        filters: { types: [], categories: [] },
        pagination: { page: 2, page_size: 10, has_more: false },
      });
    renderPage(<Knowledge />);

    await waitFor(() => expect(mocks.fetchKnowledgeChunks).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByRole('button', { name: 'Next' }));
    await waitFor(() => expect(mocks.fetchKnowledgeChunks).toHaveBeenCalledTimes(2));
    expect(mocks.fetchKnowledgeChunks.mock.calls[1][0]).toMatchObject({ page: 2 });
  });

  it('shows an expired redemption result', async () => {
    mocks.redeemPromoCode.mockResolvedValue({
      result: 'expired',
      promo_code: { code: 'SAVE10', status: 'expired', expires_at: null, redeemed_at: null },
    });
    renderPage(<Redeem />);

    fireEvent.change(screen.getByLabelText('Promo code'), { target: { value: 'save10' } });
    fireEvent.click(screen.getByRole('button', { name: 'Redeem code' }));

    expect(await screen.findByRole('heading', { name: 'Code expired' })).toBeInTheDocument();
    expect(mocks.redeemPromoCode).toHaveBeenCalledWith(
      expect.objectContaining({ code: 'SAVE10', instagram_account_id: 'ig-1' }),
      'access-token',
    );
  });
});
