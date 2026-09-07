// Generated-shape subset for the tables queried directly by this frontend.
// Regenerate from Supabase when the schema changes.
export type Database = {
  public: {
    Tables: {
      profiles: {
        Row: { id: string; email: string | null; created_at: string };
        Insert: { id: string; email?: string | null; created_at?: string };
        Update: { email?: string | null };
        Relationships: [];
      };
      businesses: {
        Row: { id: string; slug: string | null; name: string | null; created_at: string; updated_at: string };
        Insert: { id?: string; slug?: string | null; name?: string | null };
        Update: { slug?: string | null; name?: string | null };
        Relationships: [];
      };
      business_users: {
        Row: {
          id: string;
          business_id: string;
          user_id: string;
          role: 'owner' | 'manager' | 'staff';
          created_at: string;
        };
        Insert: { id?: string; business_id: string; user_id: string; role: 'owner' | 'manager' | 'staff' };
        Update: { role?: 'owner' | 'manager' | 'staff' };
        Relationships: [];
      };
      instagram_accounts: {
        Row: {
          id: string;
          business_id: string;
          instagram_user_id: string;
          username: string | null;
          name: string | null;
          status: 'connected' | 'disconnected' | 'error';
        };
        Insert: {
          id?: string;
          business_id: string;
          instagram_user_id: string;
          username?: string | null;
          name?: string | null;
          status?: 'connected' | 'disconnected' | 'error';
        };
        Update: {
          username?: string | null;
          name?: string | null;
          status?: 'connected' | 'disconnected' | 'error';
        };
        Relationships: [];
      };
      user_auth_events: {
        Row: { id: string; user_id: string; event_type: 'login' | 'logout'; client_info: Json; created_at: string };
        Insert: { id?: string; user_id: string; event_type: 'login' | 'logout'; client_info?: Json };
        Update: never;
        Relationships: [];
      };
    };
    Views: Record<string, never>;
    Functions: Record<string, never>;
    Enums: Record<string, never>;
    CompositeTypes: Record<string, never>;
  };
};

export type Json = string | number | boolean | null | { [key: string]: Json | undefined } | Json[];
