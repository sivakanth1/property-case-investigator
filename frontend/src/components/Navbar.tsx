import { BookmarkCheck, Building2, FileSearch, ListChecks, LogOut, Shield, User } from 'lucide-react';
import type { Health } from '../api/backendClient';
import type { UserProfile } from '../api/accounts';

export type View = 'properties' | 'investigation' | 'tasks' | 'portfolio';

interface NavbarProps {
  view: View;
  onNavigate: (view: View) => void;
  hasProperty: boolean;
  savedCount: number;
  user: UserProfile | null;
  health: Health | null;
  backendDown: boolean;
  onOpenAuth: () => void;
  onSignOut: () => void;
}

export function Navbar({ view, onNavigate, hasProperty, savedCount, user, health, backendDown, onOpenAuth, onSignOut }: NavbarProps) {
  const tabs: { id: View; label: string; icon: typeof Building2; disabled?: boolean }[] = [
    { id: 'properties', label: 'Properties', icon: Building2 },
    { id: 'investigation', label: 'Investigation', icon: FileSearch, disabled: !hasProperty },
    { id: 'tasks', label: 'Tasks', icon: ListChecks },
    { id: 'portfolio', label: `Portfolio (${savedCount})`, icon: BookmarkCheck },
  ];

  return (
    <header style={{ borderBottom: '1px solid var(--border-subtle)', backgroundColor: 'rgba(7, 11, 20, 0.85)',
                     backdropFilter: 'blur(20px)', position: 'sticky', top: 0, zIndex: 50 }}>
      <div className="container navbar-inner">
        <button className="row" onClick={() => onNavigate('properties')} style={{ color: '#fff' }}>
          <span style={{ width: 36, height: 36, borderRadius: 10, display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
                         background: 'linear-gradient(135deg, #2563EB 0%, #7C3AED 100%)' }}>
            <Shield size={19} />
          </span>
          <span style={{ textAlign: 'left' }}>
            <span style={{ display: 'block', fontWeight: 800, fontSize: '1.05rem' }}>Property Case Investigator</span>
            <span className="small muted">Houston historical code-enforcement records</span>
          </span>
        </button>

        <nav className="tabs" aria-label="Main">
          {tabs.map((t) => (
            <button key={t.id} className={`tab${view === t.id ? ' active' : ''}`} disabled={t.disabled}
                    onClick={() => onNavigate(t.id)} title={t.disabled ? 'Select a property first' : undefined}>
              <t.icon size={15} /> {t.label}
            </button>
          ))}
        </nav>

        <div className="row">
          {backendDown ? (
            <span className="badge badge-critical" title="Start the backend with start.ps1">Backend offline</span>
          ) : health?.model && (
            health.model.mode === 'live_model'
              ? <span className="badge badge-success" title={`Endpoint: ${health.model.endpoint_host}`}>Featherless AI · {health.model.model}</span>
              : <span className="badge badge-demo" title={health.model.warning ?? 'No Featherless key configured'}>AI off · demo mode</span>
          )}
          {user ? (
            <>
              <span className="chip" title={user.kind === 'local_guest' ? 'No account: data stays in this browser' : user.email}>
                <User size={12} /> {user.kind === 'local_guest' ? 'Local guest' : user.fullName || user.email}
              </span>
              <button className="btn btn-secondary btn-sm" onClick={onSignOut} title="Sign out"><LogOut size={14} /></button>
            </>
          ) : (
            <button className="btn btn-primary btn-sm" onClick={onOpenAuth}><User size={14} /> Sign in</button>
          )}
        </div>
      </div>
    </header>
  );
}
