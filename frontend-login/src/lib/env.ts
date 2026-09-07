export type ClientEnvironment = {
  supabaseUrl: string | null;
  supabaseAnonKey: string | null;
  backendUrl: string;
  errors: string[];
};

type ClientEnvironmentSource = {
  readonly VITE_SUPABASE_URL?: string;
  readonly VITE_SUPABASE_ANON_KEY?: string;
  readonly VITE_BACKEND_URL?: string;
};

function validUrl(name: string, value: string | undefined, required: boolean): [string | null, string | null] {
  const normalized = value?.trim().replace(/\/$/, '') || '';
  if (!normalized) {
    return required ? [null, `${name} is required`] : [null, null];
  }
  try {
    const url = new URL(normalized);
    if (!['http:', 'https:'].includes(url.protocol)) {
      throw new Error('unsupported protocol');
    }
    return [url.toString().replace(/\/$/, ''), null];
  } catch {
    return [null, `${name} must be a valid HTTP(S) URL`];
  }
}

export function readClientEnvironment(
  source: ClientEnvironmentSource = import.meta.env as ClientEnvironmentSource,
): ClientEnvironment {
  const [supabaseUrl, supabaseUrlError] = validUrl('VITE_SUPABASE_URL', source.VITE_SUPABASE_URL, true);
  const anonKey = source.VITE_SUPABASE_ANON_KEY?.trim() || null;
  const [configuredBackendUrl, backendUrlError] = validUrl('VITE_BACKEND_URL', source.VITE_BACKEND_URL, false);
  const errors = [supabaseUrlError, backendUrlError, anonKey ? null : 'VITE_SUPABASE_ANON_KEY is required'].filter(
    (value): value is string => Boolean(value),
  );
  return {
    supabaseUrl,
    supabaseAnonKey: anonKey,
    backendUrl: configuredBackendUrl || 'http://127.0.0.1:5000',
    errors,
  };
}

export const clientEnvironment = readClientEnvironment();
