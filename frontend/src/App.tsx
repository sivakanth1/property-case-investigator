import { useCallback, useEffect, useState } from 'react';
import { X } from 'lucide-react';
import { api, type Health } from './api/backendClient';
import { authService, portfolioService, type SavedProperty, type UserProfile } from './api/accounts';
import { AuthModal } from './components/AuthModal';
import { Navbar, type View } from './components/Navbar';
import { Notice } from './components/ui';
import { errorMessage } from './lib/format';
import { InvestigationPage } from './pages/InvestigationPage';
import { PortfolioPage } from './pages/PortfolioPage';
import { PropertiesPage } from './pages/PropertiesPage';
import { TasksPage } from './pages/TasksPage';

type Route =
  | { view: 'properties' }
  | { view: 'investigation'; propertyId: number; runId?: number }
  | { view: 'tasks'; propertyId?: number }
  | { view: 'portfolio' };

function parseHash(hash: string): Route {
  const [page, id, sub, subId] = hash.replace(/^#\/?/, '').split('/');
  if (page === 'investigation' && Number(id)) {
    return { view: 'investigation', propertyId: Number(id), runId: sub === 'run' && Number(subId) ? Number(subId) : undefined };
  }
  if (page === 'tasks') return { view: 'tasks', propertyId: Number(id) || undefined };
  if (page === 'portfolio') return { view: 'portfolio' };
  return { view: 'properties' };
}

function toHash(route: Route): string {
  switch (route.view) {
    case 'investigation':
      return `#/investigation/${route.propertyId}${route.runId ? `/run/${route.runId}` : ''}`;
    case 'tasks':
      return `#/tasks${route.propertyId ? `/${route.propertyId}` : ''}`;
    default:
      return `#/${route.view}`;
  }
}

export default function App() {
  const [route, setRoute] = useState<Route>(() => parseHash(window.location.hash));
  const [lastPropertyId, setLastPropertyId] = useState<number | null>(route.view === 'investigation' ? route.propertyId : null);
  const [user, setUser] = useState<UserProfile | null>(null);
  const [saved, setSaved] = useState<SavedProperty[]>([]);
  const [authOpen, setAuthOpen] = useState(false);
  const [health, setHealth] = useState<Health | null>(null);
  const [backendDown, setBackendDown] = useState(false);
  const [banner, setBanner] = useState('');

  useEffect(() => {
    const onHash = () => setRoute(parseHash(window.location.hash));
    window.addEventListener('hashchange', onHash);
    return () => window.removeEventListener('hashchange', onHash);
  }, []);

  const navigate = useCallback((next: Route) => {
    if (next.view === 'investigation') setLastPropertyId(next.propertyId);
    window.location.hash = toHash(next);
    setRoute(next);
  }, []);

  useEffect(() => {
    const check = async () => {
      try {
        setHealth(await api.health());
        setBackendDown(false);
      } catch {
        setBackendDown(true);
      }
    };
    check();
    const t = setInterval(check, 15000);
    return () => clearInterval(t);
  }, []);

  const loadPortfolio = useCallback(async (u: UserProfile | null) => {
    if (!u) {
      setSaved([]);
      return;
    }
    try {
      setSaved(await portfolioService.list(u));
    } catch (e) {
      setBanner(errorMessage(e));
    }
  }, []);

  const applyUser = useCallback((u: UserProfile | null) => {
    setUser(u);
    loadPortfolio(u);
  }, [loadPortfolio]);

  useEffect(() => {
    authService.getCurrentUser().then(applyUser);
  }, [applyUser]);

  const onAuthSuccess = applyUser;

  const signOut = async () => {
    await authService.signOut();
    setUser(null);
    setSaved([]);
  };

  const isSaved = (hcad: string) => saved.some((p) => p.hcad === hcad);

  const toggleSave = async (p: { hcad: string; address: string; zip?: string | null }) => {
    if (!user) {
      setAuthOpen(true);
      return;
    }
    try {
      if (isSaved(p.hcad)) {
        await portfolioService.remove(user, p.hcad);
        setSaved((prev) => prev.filter((x) => x.hcad !== p.hcad));
      } else {
        const created = await portfolioService.save(user, p);
        setSaved((prev) => [created, ...prev.filter((x) => x.hcad !== created.hcad)]);
      }
    } catch (e) {
      setBanner(errorMessage(e));
    }
  };

  const openSavedHcad = async (hcad: string) => {
    const local = (await api.properties(hcad)).find((p) => p.hcad === hcad);
    const property = local ?? (await api.importProperty(hcad));
    navigate({ view: 'investigation', propertyId: property.id });
  };

  const onNavigate = (view: View) => {
    if (view === 'investigation' && lastPropertyId) navigate({ view, propertyId: lastPropertyId });
    else if (view === 'tasks') navigate({ view: 'tasks' });
    else if (view !== 'investigation') navigate({ view });
  };

  const openProperty = (propertyId: number, runId?: number) => navigate({ view: 'investigation', propertyId, runId });

  return (
    <div style={{ minHeight: '100vh', display: 'flex', flexDirection: 'column' }}>
      <Navbar view={route.view} onNavigate={onNavigate} hasProperty={Boolean(lastPropertyId)} savedCount={saved.length}
              user={user} health={health} backendDown={backendDown} onOpenAuth={() => setAuthOpen(true)} onSignOut={signOut} />
      <main className="container" style={{ flex: 1, width: '100%' }}>
        {banner && (
          <div style={{ marginBottom: '1rem' }}>
            <Notice tone="error">
              <div className="row-between"><span>{banner}</span><button onClick={() => setBanner('')} aria-label="Dismiss"><X size={14} color="#FDA4AF" /></button></div>
            </Notice>
          </div>
        )}
        {backendDown && (
          <div style={{ marginBottom: '1rem' }}>
            <Notice tone="error">The backend is not reachable on port 8000. Start it with start.ps1 (or see README), then reload.</Notice>
          </div>
        )}
        {route.view === 'properties' && <PropertiesPage isSaved={isSaved} onToggleSave={toggleSave} onOpen={openProperty} />}
        {route.view === 'investigation' && (
          <InvestigationPage key={route.propertyId} propertyId={route.propertyId} runId={route.runId}
                             aiMode={health?.model.mode ?? null} isSaved={isSaved}
                             onToggleSave={toggleSave} onSelectRun={(runId) => openProperty(route.propertyId, runId)}
                             onOpenTasks={(propertyId) => navigate({ view: 'tasks', propertyId })} />
        )}
        {route.view === 'tasks' && (
          <TasksPage propertyId={route.propertyId} onFilter={(propertyId) => navigate({ view: 'tasks', propertyId })}
                     onOpenProperty={(propertyId) => openProperty(propertyId)} />
        )}
        {route.view === 'portfolio' && (
          <PortfolioPage user={user} saved={saved} onRemove={(hcad) => toggleSave({ hcad, address: '' })} onOpen={openSavedHcad}
                         onSignIn={() => setAuthOpen(true)} onBrowse={() => navigate({ view: 'properties' })} />
        )}
      </main>
      <AuthModal isOpen={authOpen} accountsEnabled={health?.accounts?.configured !== false}
                 onClose={() => setAuthOpen(false)} onAuthSuccess={onAuthSuccess} />
    </div>
  );
}
