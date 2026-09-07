import { describe, expect, it } from 'vitest';
import { readClientEnvironment } from './env';

describe('readClientEnvironment', () => {
  it('normalizes valid configuration', () => {
    const result = readClientEnvironment({
      VITE_SUPABASE_URL: 'https://example.supabase.co/',
      VITE_SUPABASE_ANON_KEY: 'anon-key',
      VITE_BACKEND_URL: 'https://api.example.com/',
    });

    expect(result.errors).toEqual([]);
    expect(result.backendUrl).toBe('https://api.example.com');
  });

  it('reports missing and invalid values', () => {
    const result = readClientEnvironment({ VITE_BACKEND_URL: 'ftp://example.com' });

    expect(result.errors).toContain('VITE_SUPABASE_URL is required');
    expect(result.errors).toContain('VITE_SUPABASE_ANON_KEY is required');
    expect(result.errors).toContain('VITE_BACKEND_URL must be a valid HTTP(S) URL');
  });
});
