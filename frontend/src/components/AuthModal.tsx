import { useState, type FormEvent } from 'react';
import { Lock, Mail, UserRound, X } from 'lucide-react';
import { authService, type UserProfile } from '../api/accounts';
import { errorMessage } from '../lib/format';
import { Notice } from './ui';

interface AuthModalProps {
  isOpen: boolean;
  accountsEnabled: boolean;
  onClose: () => void;
  onAuthSuccess: (user: UserProfile) => void;
}

export function AuthModal({ isOpen, accountsEnabled, onClose, onAuthSuccess }: AuthModalProps) {
  const [mode, setMode] = useState<'signin' | 'signup'>('signin');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [fullName, setFullName] = useState('');
  const [company, setCompany] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  if (!isOpen) return null;

  const finish = (user: UserProfile) => {
    setPassword('');
    setConfirm('');
    onAuthSuccess(user);
    onClose();
  };

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setError('');
    if (mode === 'signup' && password !== confirm) {
      setError('The two passwords do not match.');
      return;
    }
    setLoading(true);
    try {
      finish(mode === 'signin'
        ? await authService.signIn(email, password)
        : await authService.signUp(email, password, fullName, company));
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setLoading(false);
    }
  };

  const switchMode = (next: 'signin' | 'signup') => {
    setMode(next);
    setError('');
  };

  return (
    <div role="dialog" aria-modal="true" aria-label="Sign in" style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.8)',
         backdropFilter: 'blur(10px)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 100, padding: '1rem' }}>
      <div className="glass-panel panel stack" style={{ width: '100%', maxWidth: 440, background: 'rgba(15, 23, 42, 0.97)', position: 'relative' }}>
        <button onClick={onClose} aria-label="Close" style={{ position: 'absolute', top: 14, right: 14, color: 'var(--text-muted)' }}><X size={18} /></button>
        <div className="section-title" style={{ fontSize: '1.25rem' }}><Lock size={18} color="#60A5FA" />
          {mode === 'signin' ? 'Sign in' : 'Create an account'}</div>

        {accountsEnabled ? (
          <>
            <div className="tabs">
              <button className={`tab${mode === 'signin' ? ' active' : ''}`} onClick={() => switchMode('signin')}>Sign in</button>
              <button className={`tab${mode === 'signup' ? ' active' : ''}`} onClick={() => switchMode('signup')}>Sign up</button>
            </div>
            {error && (
              <Notice tone="error">
                <div className="stack-sm">
                  <span>{error}</span>
                  {/incorrect/i.test(error) && mode === 'signin' && (
                    <span className="small">New here, or only used the earlier version? Create an account with Sign up.</span>
                  )}
                </div>
              </Notice>
            )}
            <form className="stack" onSubmit={submit}>
              {mode === 'signup' && (
                <>
                  <label className="stack-sm small secondary">Full name
                    <input className="input" value={fullName} onChange={(e) => setFullName(e.target.value)} autoComplete="name" />
                  </label>
                  <label className="stack-sm small secondary">Company (optional)
                    <input className="input" value={company} onChange={(e) => setCompany(e.target.value)} autoComplete="organization" />
                  </label>
                </>
              )}
              <label className="stack-sm small secondary"><span className="row"><Mail size={13} /> Email</span>
                <input className="input" type="email" required value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="email" />
              </label>
              <label className="stack-sm small secondary"><span className="row"><Lock size={13} /> Password</span>
                <input className="input" type="password" required minLength={mode === 'signup' ? 8 : undefined} maxLength={128}
                       value={password} onChange={(e) => setPassword(e.target.value)}
                       autoComplete={mode === 'signin' ? 'current-password' : 'new-password'} />
              </label>
              {mode === 'signup' && (
                <label className="stack-sm small secondary"><span className="row"><Lock size={13} /> Confirm password</span>
                  <input className="input" type="password" required value={confirm} onChange={(e) => setConfirm(e.target.value)}
                         autoComplete="new-password" />
                  <span className="muted">At least 8 characters. Stored only as a one-way hash; nobody can read it back.</span>
                </label>
              )}
              <button className="btn btn-primary" disabled={loading}>
                {loading ? 'Please wait…' : mode === 'signin' ? 'Sign in' : 'Create account'}
              </button>
            </form>
          </>
        ) : (
          <Notice tone="info">
            Accounts are not configured yet: add SUPABASE_SERVICE_ROLE_KEY to backend/.env and restart the backend.
          </Notice>
        )}

        <div className="divider" style={{ paddingTop: '1rem' }}>
          <button className="btn btn-secondary" style={{ width: '100%' }} onClick={() => finish(authService.continueAsGuest())}>
            <UserRound size={15} /> Continue as a local guest
          </button>
          <p className="small muted" style={{ marginTop: '0.5rem' }}>
            No account: your saved portfolio stays in this browser only. Investigations and tasks work the same.
          </p>
        </div>
      </div>
    </div>
  );
}
