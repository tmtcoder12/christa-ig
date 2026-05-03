import type {
  CreatePromotionSetupInput,
  PromotionSetup,
  RedeemPromoCodeInput,
  RedeemPromoCodeResponse,
} from '../types';

const backendUrl = (import.meta.env.VITE_BACKEND_URL as string | undefined) || 'http://127.0.0.1:5000';

async function apiRequest<T>(path: string, accessToken: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(`${backendUrl}${path}`, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${accessToken}`,
      ...(options.headers || {}),
    },
  });

  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.error || `Request failed with status ${response.status}`);
  }
  return payload as T;
}

export async function createPromotionSetup(input: CreatePromotionSetupInput, accessToken: string) {
  return apiRequest<{ setup: PromotionSetup }>('/api/promotions', accessToken, {
    method: 'POST',
    body: JSON.stringify(input),
  });
}

export async function fetchPromotionSetup(setupId: string, accessToken: string) {
  return apiRequest<{ setup: PromotionSetup }>(`/api/promotions/${setupId}`, accessToken);
}

export async function redeemPromoCode(input: RedeemPromoCodeInput, accessToken: string) {
  return apiRequest<RedeemPromoCodeResponse>('/api/promo-codes/redeem', accessToken, {
    method: 'POST',
    body: JSON.stringify(input),
  });
}
