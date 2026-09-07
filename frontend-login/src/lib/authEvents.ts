import type { User } from '@supabase/supabase-js';
import { supabase } from './supabase';

type AuthEventType = 'login' | 'logout';

function getClientInfo() {
  return {
    user_agent: navigator.userAgent,
    language: navigator.language,
    languages: Array.from(navigator.languages),
    platform: navigator.platform,
    timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
    screen: {
      width: window.screen.width,
      height: window.screen.height,
      pixel_ratio: window.devicePixelRatio,
    },
    viewport: {
      width: window.innerWidth,
      height: window.innerHeight,
    },
  };
}

export async function recordAuthEvent(user: User, eventType: AuthEventType) {
  if (!supabase) {
    return;
  }

  const { error } = await supabase.from('user_auth_events').insert({
    user_id: user.id,
    event_type: eventType,
    client_info: getClientInfo(),
  });

  if (error) {
    throw error;
  }
}
