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

export type PromoCodeFollowupSummary = {
  id: string;
  promo_code_id: string;
  purpose: 'promo_code' | 'post_redemption_followup';
  status: 'pending' | 'sending' | 'sent' | 'failed' | 'cancelled';
  scheduled_for: string | null;
  sent_at: string | null;
  twilio_message_sid: string | null;
  error_message: string | null;
};

export type RedeemPromoCodeInput = {
  instagram_account_id: string;
  code: string;
  redemption_notes?: string | null;
};

export type CustomerProfileSummary = {
  id: string;
  instagram_account_id: string;
  contact_id: string | null;
  phone_e164: string;
  display_name: string | null;
  first_redeemed_at: string;
  last_redeemed_at: string;
  redeem_count: number;
  last_order_notes: string | null;
  profile_summary: string | null;
};

export type RedeemPromoCodeResponse = {
  result: PromoCodeRedeemResult;
  promo_code: PromoCodeSummary | null;
  followup?: PromoCodeFollowupSummary | null;
  customer_profile?: CustomerProfileSummary | null;
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
  category: string | null;
  source_url: string | null;
  page_path: string | null;
};

export type KnowledgeChunkFilters = {
  types: string[];
  categories: string[];
};

export type KnowledgeChunkPagination = {
  page: number;
  page_size: number;
  has_more: boolean;
};

export type FetchKnowledgeChunksInput = {
  instagram_account_id: string;
  page: number;
  page_size: number;
  type: string | null;
  category: string | null;
};

export type FetchKnowledgeChunksResponse = {
  chunks: KnowledgeChunk[];
  pagination: KnowledgeChunkPagination;
  filters: KnowledgeChunkFilters;
};
