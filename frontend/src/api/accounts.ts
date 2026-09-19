import { api, ApiError, sessionStore, type AccountUser, type SavedPropertyRow } from './backendClient';

/** 'account' = signed in through the backend (stored in Supabase). 'local_guest' = no account; browser-only data. */
export interface UserProfile {
  id: string;
  email: string;
  fullName?: string;
  company?: string;
  kind: 'account' | 'local_guest';
}

export type SavedProperty = SavedPropertyRow;

const GUEST_KEY = 'pci_local_guest';
const PORTFOLIO_KEY = 'pci_local_portfolio';

function readJson<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : fallback;
  } catch {
    return fallback;
  }
}

function writeJson(key: string, value: unknown): void {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* storage unavailable (private mode) */
  }
}

function forget(key: string): void {
  try {
    localStorage.removeItem(key);
  } catch {
    /* ignore */
  }
}

function randomId(): string {
  return typeof crypto !== 'undefined' && 'randomUUID' in crypto
    ? crypto.randomUUID()
    : `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
}

function toProfile(u: AccountUser): UserProfile {
  return { id: u.id, email: u.email, fullName: u.full_name ?? undefined, company: u.company ?? undefined, kind: 'account' };
}

function startSession(result: { token: string; user: AccountUser }): UserProfile {
  sessionStore.set(result.token);
  forget(GUEST_KEY);
  return toProfile(result.user);
}

export const authService = {
  async getCurrentUser(): Promise<UserProfile | null> {
    if (sessionStore.get()) {
      try {
        return toProfile(await api.me());
      } catch (e) {
        if (e instanceof ApiError && e.status === 401) sessionStore.clear();
        else return null; // backend unreachable: keep the token and try again on the next load
      }
    }
    return readJson<UserProfile | null>(GUEST_KEY, null);
  },

  async signIn(email: string, password: string): Promise<UserProfile> {
    return startSession(await api.signIn(email.trim(), password));
  },

  async signUp(email: string, password: string, fullName: string, company: string): Promise<UserProfile> {
    return startSession(await api.signUp({
      email: email.trim(), password, full_name: fullName.trim() || undefined, company: company.trim() || undefined,
    }));
  },

  continueAsGuest(): UserProfile {
    const guest: UserProfile = { id: `local-${randomId()}`, email: 'Local guest', fullName: 'Local guest', kind: 'local_guest' };
    writeJson(GUEST_KEY, guest);
    return guest;
  },

  async signOut(): Promise<void> {
    if (sessionStore.get()) {
      try {
        await api.signOut();
      } catch {
        /* the token is dropped locally either way */
      }
      sessionStore.clear();
    }
    forget(GUEST_KEY);
  },
};

export const portfolioService = {
  async list(user: UserProfile): Promise<SavedProperty[]> {
    if (user.kind === 'account') return api.portfolio();
    return readJson<SavedProperty[]>(PORTFOLIO_KEY, []).filter((p) => p.user_id === user.id);
  },

  async save(user: UserProfile, prop: { hcad: string; address: string; zip?: string | null }): Promise<SavedProperty> {
    if (user.kind === 'account') return api.savePortfolio(prop);
    const all = readJson<SavedProperty[]>(PORTFOLIO_KEY, []);
    const existing = all.find((p) => p.user_id === user.id && p.hcad === prop.hcad);
    if (existing) return existing;
    const created: SavedProperty = {
      id: randomId(), user_id: user.id, hcad: prop.hcad, address: prop.address, zip: prop.zip ?? null,
      created_at: new Date().toISOString(),
    };
    writeJson(PORTFOLIO_KEY, [created, ...all]);
    return created;
  },

  async remove(user: UserProfile, hcad: string): Promise<void> {
    if (user.kind === 'account') {
      await api.removePortfolio(hcad);
      return;
    }
    const all = readJson<SavedProperty[]>(PORTFOLIO_KEY, []);
    writeJson(PORTFOLIO_KEY, all.filter((p) => !(p.user_id === user.id && p.hcad === hcad)));
  },
};
