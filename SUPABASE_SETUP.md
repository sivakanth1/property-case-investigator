# Supabase setup (accounts and saved portfolio)

Accounts are handled by the app's backend, not Supabase Auth, so there are no confirmation emails and no email rate
limits. Supabase stores the account rows:

- `public.profiles`: `id`, `email` (unique, lowercase), `full_name`, `company`, `password_hash`.
- `public.saved_properties`: `(user_id, hcad)` unique; the properties each account tracks.
- `public.case_plans` / `public.case_plan_steps`: the AI resolution checklist per case, each step carrying
  `status` = `pending` or `completed`.
- `public.investigation_tasks` / `public.investigation_task_feedback`: verification tasks from investigations, with
  their status (`open`, `in_progress`, `verified`, `dismissed`) and the feedback notes behind each change.

Everything keyed by `user_id`, so signing in on another device shows the same lists in the same state. Guests (no
account) keep these in the backend's local SQLite file instead.

How passwords are protected:

- **Sign up:** the backend hashes the password with **scrypt** (a random salt per user, one-way) and stores only the
  hash in `profiles.password_hash`. The plain password is never stored, logged or sent back.
- **Sign in:** the backend hashes the password typed at sign-in the same way and compares it with the stored hash.
  A match returns a session token (valid 7 days, stored server-side only as a SHA-256 hash). Ten wrong attempts on
  one email lock that email for 15 minutes.
- Hashing is used instead of encryption because encryption can be reversed by anyone holding the key; a hash cannot.

Only the backend can read these tables: RLS is on, there are no policies, and all grants are revoked from the public
(anon/publishable) key. The backend uses the service-role key, which bypasses RLS and never reaches the browser.

## Steps

1. Supabase Dashboard → **SQL Editor** → **New query** → paste [supabase_schema.sql](supabase_schema.sql) → **Run**.
   It is safe to re-run. It adds the `password_hash` column and locks the tables.
2. Dashboard → **Project Settings → API Keys** → copy the **secret** key (`sb_secret_…`) or the legacy
   **service_role** key.
3. Paste it into `backend/.env`, which already has `SUPABASE_URL` filled in:
   ```
   SUPABASE_SERVICE_ROLE_KEY=<secret key>
   ```
   Never put this key in `frontend/.env`; everything there is visible in the browser.
4. Restart the backend window. `GET /api/health` should show `"accounts": {"configured": true}`.
5. Optional clean-up: delete old Supabase Auth users (Authentication → Users), especially
   `investor@houston-holdings.com`, whose password was published in earlier files. A profile row left over from the old
   Supabase Auth sign-up has no password; signing up again with that email attaches a password to it.

There is no email verification in this flow, so anyone can register any email address. Add verification before using
the app with real customers.
