import type {
  CreateKnowledgeChunkInput,
  CreatePromotionSetupInput,
  FetchKnowledgeChunksInput,
  FetchKnowledgeChunksResponse,
  KnowledgeChunk,
  PromotionSetup,
  RedeemPromoCodeInput,
  RedeemPromoCodeResponse,
} from '../types';
import { clientEnvironment } from './env';

const REQUEST_TIMEOUT_MS = 10_000;
const SAFE_RETRY_DELAYS_MS = [250, 750];

type ErrorPayload = {
  error?: string;
  code?: string;
  request_id?: string;
};

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code: string,
    readonly requestId: string | null,
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

function sleep(delayMs: number) {
  return new Promise((resolve) => window.setTimeout(resolve, delayMs));
}

function shouldRetry(status: number) {
  return status === 408 || status === 429 || status === 502 || status === 503 || status === 504;
}

async function sendRequest(path: string, accessToken: string, options: RequestInit): Promise<Response> {
  const controller = new AbortController();
  const timeoutId = window.setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  try {
    return await fetch(`${clientEnvironment.backendUrl}${path}`, {
      ...options,
      signal: controller.signal,
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${accessToken}`,
        ...(options.headers || {}),
      },
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') {
      throw new ApiError('The server took too long to respond.', 0, 'request_timeout', null);
    }
    throw error;
  } finally {
    window.clearTimeout(timeoutId);
  }
}

export async function apiRequest<T>(path: string, accessToken: string, options: RequestInit = {}): Promise<T> {
  const method = (options.method || 'GET').toUpperCase();
  const maxRetries = method === 'GET' ? SAFE_RETRY_DELAYS_MS.length : 0;
  let response: Response | null = null;

  for (let attempt = 0; attempt <= maxRetries; attempt += 1) {
    try {
      response = await sendRequest(path, accessToken, options);
      if (!shouldRetry(response.status) || attempt === maxRetries) {
        break;
      }
    } catch (error) {
      if (attempt === maxRetries || (error instanceof ApiError && error.code !== 'request_timeout')) {
        throw error;
      }
    }
    await sleep(SAFE_RETRY_DELAYS_MS[attempt]);
  }

  if (!response) {
    throw new ApiError('Unable to reach the server.', 0, 'network_error', null);
  }

  const payload = (await response.json().catch(() => ({}))) as ErrorPayload;
  if (!response.ok) {
    throw new ApiError(
      payload.error || `Request failed with status ${response.status}`,
      response.status,
      payload.code || 'request_failed',
      payload.request_id || response.headers.get('X-Request-ID'),
    );
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

export async function fetchKnowledgeChunks(input: FetchKnowledgeChunksInput, accessToken: string) {
  const query = new URLSearchParams({
    instagram_account_id: input.instagram_account_id,
    page: String(input.page),
    page_size: String(input.page_size),
  });
  if (input.type) {
    query.set('type', input.type);
  }
  if (input.category) {
    query.set('category', input.category);
  }
  return apiRequest<FetchKnowledgeChunksResponse>(`/api/knowledge-chunks?${query.toString()}`, accessToken);
}

export async function createKnowledgeChunk(input: CreateKnowledgeChunkInput, accessToken: string) {
  return apiRequest<{ chunk: KnowledgeChunk }>('/api/knowledge-chunks', accessToken, {
    method: 'POST',
    body: JSON.stringify(input),
  });
}
