import { useState } from 'react';
import { ArrowRight, BookmarkCheck, MapPin, Trash2, RefreshCw } from 'lucide-react';
import type { SavedProperty, UserProfile } from '../api/accounts';
import { api } from '../api/backendClient';
import { Empty, Notice } from '../components/ui';
import { errorMessage } from '../lib/format';

interface Props {
  user: UserProfile | null;
  saved: SavedProperty[];
  onRemove: (hcad: string) => void;
  onOpen: (hcad: string) => Promise<void>;
  onSignIn: () => void;
  onBrowse: () => void;
}

export function PortfolioPage({ user, saved, onRemove, onOpen, onSignIn, onBrowse }: Props) {
  const [opening, setOpening] = useState<string | null>(null);
  const [error, setError] = useState('');
  const [refreshing, setRefreshing] = useState(false);
  const [newCases, setNewCases] = useState<Record<string, number>>({});

  if (!user) {
    return (
      <Empty>
        <p style={{ marginBottom: '1rem' }}>Sign in, or continue as a local guest, to keep a portfolio of properties.</p>
        <button className="btn btn-primary" onClick={onSignIn}>Sign in or continue as guest</button>
      </Empty>
    );
  }

  const open = async (hcad: string) => {
    setOpening(hcad);
    setError('');
    try {
      await onOpen(hcad);
      setNewCases((prev) => {
        const next = { ...prev };
        delete next[hcad];
        return next;
      });
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setOpening(null);
    }
  };

  const bulkRefresh = async () => {
    if (saved.length === 0) return;
    setRefreshing(true);
    setError('');
    try {
      const hcads = saved.map((p) => p.hcad);
      const res = await api.bulkRefresh(hcads);
      setNewCases(res.results);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setRefreshing(false);
    }
  };

  const sortedSaved = [...saved].sort((a, b) => {
    const newA = newCases[a.hcad] || 0;
    const newB = newCases[b.hcad] || 0;
    if (newA > 0 && newB === 0) return -1;
    if (newB > 0 && newA === 0) return 1;
    return new Date(b.created_at).getTime() - new Date(a.created_at).getTime();
  });

  return (
    <div className="stack-lg">
      <div className="row-between">
        <div className="stack-sm">
          <h1 className="page-title row"><BookmarkCheck size={24} color="#34D399" /> My portfolio</h1>
          <p className="secondary">Properties you are tracking. Tasks and investigation history are shared per property.</p>
          {user.kind === 'local_guest' && (
            <Notice tone="warn">You are a local guest: this portfolio is stored only in this browser. Sign in to keep it in your account.</Notice>
          )}
        </div>
        {saved.length > 0 && (
          <button className="btn btn-primary" disabled={refreshing} onClick={bulkRefresh}>
            <RefreshCw size={16} className={refreshing ? 'spinning' : ''} />
            {refreshing ? 'Refreshing...' : 'Refresh all properties'}
          </button>
        )}
      </div>
      {error && <Notice tone="error">{error}</Notice>}
      {saved.length === 0 ? (
        <Empty>
          <p style={{ marginBottom: '1rem' }}>No saved properties yet.</p>
          <button className="btn btn-primary" onClick={onBrowse}>Browse properties</button>
        </Empty>
      ) : (
        <div className="grid-cards">
          {sortedSaved.map((p) => {
            const added = newCases[p.hcad] || 0;
            return (
              <div key={p.hcad} className="glass-panel panel stack">
                <div className="row-between">
                  <div className="row">
                    <span className="chip">HCAD {p.hcad}</span>
                    {added > 0 && <span className="badge badge-success">{added} new case{added > 1 ? 's' : ''}</span>}
                  </div>
                  <button className="btn btn-danger btn-sm" title="Remove from portfolio" onClick={() => onRemove(p.hcad)}><Trash2 size={13} /></button>
                </div>
                <div className="card-title row"><MapPin size={17} color="#34D399" />{p.address}</div>
                <span className="small muted">{p.zip ? `ZIP ${p.zip} · ` : ''}saved {new Date(p.created_at).toLocaleDateString()}</span>
                <button className="btn btn-primary" disabled={opening !== null} onClick={() => open(p.hcad)}>
                  {opening === p.hcad ? 'Opening…' : 'Open investigation'} <ArrowRight size={15} />
                </button>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
