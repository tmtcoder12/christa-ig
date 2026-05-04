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

export type PromoCodeRedeemResult = 'redeemed' | 'expired' | 'not_found' | 'already_redeemed' | 'void';

export type PromoCodeSummary = {
  id: string;
  code: string;
  status: 'issued' | 'redeemed' | 'expired' | 'void';
  valid_from: string | null;
  expires_at: string | null;
  redeemed_at: string | null;
};

export type RedeemPromoCodeInput = {
  instagram_account_id: string;
  code: string;
};

export type RedeemPromoCodeResponse = {
  result: PromoCodeRedeemResult;
  promo_code: PromoCodeSummary | null;
};

export type KnowledgeChunk = {
  id: string;
  instagram_account_id: string;
  text: string;
  type: string | null;
  source_url: string | null;
  page_path: string | null;
  title: string | null;
  meta_description: string | null;
  extra_metadata: Record<string, unknown>;
  content_hash: string | null;
  created_at: string;
};

export type CreateKnowledgeChunkInput = {
  instagram_account_id: string;
  text: string;
  title: string | null;
  type: string | null;
  source_url: string | null;
  page_path: string | null;
};
