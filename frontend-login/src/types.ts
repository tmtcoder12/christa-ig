export type Business = {
  id: string;
  slug: string | null;
  name: string | null;
};

export type InstagramAccount = {
  id: string;
  business_id: string;
  instagram_user_id: string;
  username: string | null;
  name: string | null;
  status: 'connected' | 'disconnected' | 'error';
};

export type PromotionSetupStatus = 'pending' | 'polling' | 'found' | 'expired' | 'error';

export type PromotionSetup = {
  id: string;
  instagram_account_id: string;
  submitted_by: string;
  trigger_keywords: string[];
  automation_starts_at: string | null;
  automation_ends_at: string | null;
  promo_code_valid_duration_hours: number | null;
  comment_reply_text: string;
  dm_prompt: string | null;
  code_prefix: string | null;
  baseline_media_ids: string[];
  status: PromotionSetupStatus;
  post_id: string | null;
  found_instagram_media_id: string | null;
  found_caption: string | null;
  error_message: string | null;
  poll_started_at: string | null;
  poll_expires_at: string | null;
  last_polled_at: string | null;
  found_at: string | null;
  created_at: string;
  updated_at: string;
};

export type CreatePromotionSetupInput = {
  instagram_account_id: string;
  trigger_keywords: string[];
  automation_starts_at: string | null;
  automation_ends_at: string | null;
  promo_code_valid_duration_hours: number | null;
  comment_reply_text: string;
  dm_prompt: string | null;
  code_prefix: string | null;
};
