import { http, HttpResponse } from 'msw';
import { describe, expect, it } from 'vitest';
import { ApiError, apiRequest, createKnowledgeChunk, fetchPromotionSetup } from './backend';
import { server } from '../test/testServer';

describe('backend client', () => {
  it('retries a safe GET after a transient response', async () => {
    let attempts = 0;
    server.use(
      http.get('http://127.0.0.1:5000/api/promotions/setup-1', () => {
        attempts += 1;
        return attempts === 1
          ? HttpResponse.json({ error: 'busy' }, { status: 503 })
          : HttpResponse.json({ setup: { id: 'setup-1' } });
      }),
    );

    await expect(fetchPromotionSetup('setup-1', 'token')).resolves.toMatchObject({ setup: { id: 'setup-1' } });
    expect(attempts).toBe(2);
  });

  it('does not retry a mutation', async () => {
    let attempts = 0;
    server.use(
      http.post('http://127.0.0.1:5000/api/knowledge-chunks', () => {
        attempts += 1;
        return HttpResponse.json(
          { error: 'temporarily unavailable', code: 'storage_unavailable', request_id: 'req-1' },
          { status: 503 },
        );
      }),
    );

    const promise = createKnowledgeChunk(
      {
        instagram_account_id: 'ig-1',
        text: 'Hours are 9 to 5',
        title: null,
        type: null,
        category: null,
        source_url: null,
        page_path: null,
      },
      'token',
    );
    await expect(promise).rejects.toMatchObject({
      status: 503,
      code: 'storage_unavailable',
      requestId: 'req-1',
    });
    expect(attempts).toBe(1);
  });

  it('turns failed responses into typed errors', async () => {
    server.use(
      http.get('http://127.0.0.1:5000/nope', () =>
        HttpResponse.json({ error: 'Not found', code: 'not_found' }, { status: 404 }),
      ),
    );

    await expect(apiRequest('/nope', 'token')).rejects.toBeInstanceOf(ApiError);
  });
});
