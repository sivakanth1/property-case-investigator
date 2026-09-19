-- ==============================================================================
-- Property Case Investigator - Supabase schema (accounts + saved portfolio)
-- Run in Supabase: Dashboard -> SQL Editor -> New query -> paste -> Run. Safe to re-run.
-- Works whether profiles.id is uuid (older projects) or text.
--
-- Accounts are handled by the app's backend, not Supabase Auth:
--   * sign-up: the backend hashes the password with scrypt (salted, one-way) and stores it in
--     profiles.password_hash. The plain password is never stored or logged.
--   * sign-in: the backend re-hashes the typed password and compares it with password_hash.
-- The backend reaches these tables with the service_role key (server-only). The browser's public key has no
-- access at all, so nobody can list emails or password hashes from the browser.
-- ==============================================================================

-- 1. Tables (created only if missing) and the columns the app needs ------------------------------
CREATE TABLE IF NOT EXISTS public.profiles (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email TEXT UNIQUE NOT NULL,
    full_name TEXT,
    company TEXT,
    role TEXT DEFAULT 'investor',
    password_hash TEXT,
    created_at TIMESTAMPTZ DEFAULT now() NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT now() NOT NULL
);
ALTER TABLE public.profiles ADD COLUMN IF NOT EXISTS full_name TEXT;
ALTER TABLE public.profiles ADD COLUMN IF NOT EXISTS company TEXT;
ALTER TABLE public.profiles ADD COLUMN IF NOT EXISTS password_hash TEXT;
ALTER TABLE public.profiles ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ DEFAULT now();
ALTER TABLE public.profiles ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT now();

CREATE TABLE IF NOT EXISTS public.saved_properties (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id TEXT NOT NULL,
    hcad TEXT NOT NULL,
    address TEXT NOT NULL,
    zip TEXT,
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT now() NOT NULL
);
-- Needed for "save to portfolio" upserts (one row per account and parcel).
CREATE UNIQUE INDEX IF NOT EXISTS saved_properties_user_hcad_idx ON public.saved_properties (user_id, hcad);

-- 2. Give id columns a generated default that matches their existing type (uuid or text) --------
DO $$
DECLARE
    t TEXT;
    col_type TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['profiles', 'saved_properties'] LOOP
        SELECT data_type INTO col_type FROM information_schema.columns
         WHERE table_schema = 'public' AND table_name = t AND column_name = 'id';
        IF col_type = 'uuid' THEN
            EXECUTE format('ALTER TABLE public.%I ALTER COLUMN id SET DEFAULT gen_random_uuid()', t);
        ELSE
            EXECUTE format('ALTER TABLE public.%I ALTER COLUMN id SET DEFAULT gen_random_uuid()::text', t);
        END IF;
    END LOOP;
END $$;

-- 3. Profiles no longer come from Supabase Auth ----------------------------------------------------
-- Drop foreign keys from these tables to auth.users (they would reject accounts created by the backend).
DO $$
DECLARE r RECORD;
BEGIN
    FOR r IN
        SELECT conrelid::regclass::text AS tbl, conname
          FROM pg_constraint
         WHERE contype = 'f'
           AND confrelid = 'auth.users'::regclass
           AND conrelid IN ('public.profiles'::regclass, 'public.saved_properties'::regclass)
    LOOP
        EXECUTE format('ALTER TABLE %s DROP CONSTRAINT %I', r.tbl, r.conname);
    END LOOP;
END $$;

DROP TRIGGER IF EXISTS on_auth_user_created ON auth.users;
DROP FUNCTION IF EXISTS public.handle_new_user();

-- 4. Lock both tables to the backend ---------------------------------------------------------------
-- RLS on + no policies + revoked grants: the anon/publishable key and Supabase-Auth users can read or write
-- nothing. The service_role key used by the backend bypasses RLS.
ALTER TABLE public.profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.saved_properties ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Allow all on profiles" ON public.profiles;
DROP POLICY IF EXISTS "Allow all on saved_properties" ON public.saved_properties;
DROP POLICY IF EXISTS "Users can view their own profile" ON public.profiles;
DROP POLICY IF EXISTS "Users can update their own profile" ON public.profiles;
DROP POLICY IF EXISTS "Users can manage their saved properties" ON public.saved_properties;
DROP POLICY IF EXISTS "profiles_select_own" ON public.profiles;
DROP POLICY IF EXISTS "profiles_update_own" ON public.profiles;
DROP POLICY IF EXISTS "profiles_insert_own" ON public.profiles;
DROP POLICY IF EXISTS "saved_properties_own" ON public.saved_properties;

REVOKE ALL ON public.profiles FROM anon, authenticated;
REVOKE ALL ON public.saved_properties FROM anon, authenticated;

-- 5. Legacy prototype tables: locked the same way (data left in place) ------------------------------
DO $$
DECLARE t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['property_investigations', 'property_tasks', 'task_feedback_log'] LOOP
        IF to_regclass('public.' || t) IS NOT NULL THEN
            EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', t);
            EXECUTE format('DROP POLICY IF EXISTS %I ON public.%I', 'Allow all on ' || t, t);
            EXECUTE format('REVOKE ALL ON public.%I FROM anon, authenticated', t);
        END IF;
    END LOOP;
END $$;

-- ------------------------------------------------------------------------------
-- Earlier versions of this script created investor@houston-holdings.com in Supabase Auth with a password
-- published in the repository. Delete it in Authentication -> Users, or uncomment and run:
-- DELETE FROM auth.users WHERE email = 'investor@houston-holdings.com';
-- ------------------------------------------------------------------------------
