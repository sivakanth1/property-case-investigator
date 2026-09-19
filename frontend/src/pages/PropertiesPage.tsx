import { useCallback, useEffect, useState, type FormEvent } from 'react';
import { ArrowRight, BookmarkCheck, BookmarkPlus, FileSearch, MapPin, Play, Radio, Search } from 'lucide-react';
import { api, ApiError, type CandidatesResponse, type PropertySummary } from '../api/backendClient';
import { CoveragePanel, Empty, ModeBadge, Notice, StatusBadge } from '../components/ui';
import { errorMessage } from '../lib/format';

interface Props {
  isSaved: (hcad: string) => boolean;
  onToggleSave: (p: { hcad: string; address: string; zip?: string | null }) => void;
  onOpen: (propertyId: number, runId?: number) => void;
}

const EXAMPLES = ['1801 SAKOWITZ', '2821 LUELL', '3410 BREMOND', '5918 SOUTHINGTON'];

export function PropertiesPage({ isSaved, onToggleSave, onOpen }: Props) {
  const [filter, setFilter] = useState('');
  const [properties, setProperties] = useState<PropertySummary[] | null>(null);
  const [error, setError] = useState('');
  const [starting, setStarting] = useState<number | null>(null);

  const load = useCallback(async (text: string) => {
    try {
      setProperties(await api.properties(text));
      setError('');
    } catch (e) {
      setError(errorMessage(e));
    }
  }, []);

  useEffect(() => {
    const t = setTimeout(() => load(filter), 250);
    return () => clearTimeout(t);
  }, [filter, load]);

  const anyActive = properties?.some((p) => p.active_run_id) ?? false;
  useEffect(() => {
    if (!anyActive) return;
    const t = setInterval(() => load(filter), 2000);
    return () => clearInterval(t);
  }, [anyActive, filter, load]);

  const investigate = async (p: PropertySummary) => {
    setStarting(p.id);
    try {
      const run = await api.startInvestigation(p.id);
      onOpen(p.id, run.run_id);
    } catch (e) {
      if (e instanceof ApiError && e.code === 'run_active' && p.active_run_id) onOpen(p.id, p.active_run_id);
      else setError(errorMessage(e));
    } finally {
      setStarting(null);
    }
  };

  return (
    <div className="stack-lg">
      <div className="stack-sm">
        <h1 className="page-title">Houston properties</h1>
        <p className="secondary">
          Search the City of Houston code-enforcement records live, open a property to see its cases, get step-by-step
          resolution checklists for open cases, and run an investigation that proposes verification tasks.
        </p>
        <Notice tone="info">
          Data comes live from the City of Houston open-data API (violations dataset covers roughly 2014–2018). Records
          are historical: they cannot show a property's current condition, and missing records do not mean no problems.
        </Notice>
      </div>

      <LiveSearch onOpen={onOpen} />

      <div className="stack">
        <div className="row-between">
          <div className="section-title">Properties you have opened ({properties?.length ?? 0})</div>
          <input className="input" style={{ maxWidth: 320 }} value={filter} onChange={(e) => setFilter(e.target.value)}
                 placeholder="Filter by address or HCAD…" aria-label="Filter opened properties" />
        </div>
        {error && <Notice tone="error">{error}</Notice>}
        {properties === null && !error && <Empty>Loading properties…</Empty>}
        {properties?.length === 0 && <Empty>{filter ? 'No opened property matches that filter.' : 'Search above to open a property.'}</Empty>}
        <div className="grid-cards">
          {properties?.map((p) => {
            const saved = isSaved(p.hcad);
            return (
              <div key={p.id} className="glass-panel panel stack" style={{ justifyContent: 'space-between' }}>
                <div className="stack-sm">
                  <div className="row-between">
                    <span className="chip">HCAD {p.hcad}</span>
                    <button className="btn btn-secondary btn-sm" onClick={() => onToggleSave(p)}
                            title={saved ? 'Remove from portfolio' : 'Save to portfolio'}>
                      {saved ? <BookmarkCheck size={14} color="#34D399" /> : <BookmarkPlus size={14} />}
                      {saved ? 'Saved' : 'Save'}
                    </button>
                  </div>
                  <div className="card-title row"><MapPin size={17} color="#60A5FA" />{p.address}</div>
                  <div className="row small secondary">
                    {p.zip && <span>ZIP {p.zip}</span>}
                    <span>{p.case_count} distinct case{p.case_count === 1 ? '' : 's'}</span>
                    <span>{p.open_task_count} open task{p.open_task_count === 1 ? '' : 's'}</span>
                  </div>
                  <CoveragePanel coverage={p.coverage} compact />
                  {p.latest_run && (
                    <div className="row small">
                      <span className="muted">Latest run #{p.latest_run.id}</span>
                      <StatusBadge status={p.latest_run.status} />
                      <ModeBadge mode={p.latest_run.mode} />
                    </div>
                  )}
                </div>
                <div className="row divider" style={{ paddingTop: '0.9rem' }}>
                  <button className="btn btn-primary" onClick={() => onOpen(p.id, p.active_run_id ?? undefined)}>
                    <FileSearch size={15} /> Open cases
                  </button>
                  <button className="btn btn-secondary" disabled={Boolean(p.active_run_id) || starting === p.id || !p.coverage.row_count}
                          onClick={() => investigate(p)}>
                    <Play size={15} />
                    {p.active_run_id ? `Run #${p.active_run_id} in progress` : starting === p.id ? 'Starting…' : 'Investigate'}
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

function LiveSearch({ onOpen }: { onOpen: (propertyId: number) => void }) {
  const [text, setText] = useState('');
  const [result, setResult] = useState<CandidatesResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [opening, setOpening] = useState<string | null>(null);
  const [error, setError] = useState('');

  const search = async (query: string) => {
    setLoading(true);
    setError('');
    setResult(null);
    try {
      setResult(await api.candidates(query.trim()));
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setLoading(false);
    }
  };

  const submit = (e: FormEvent) => {
    e.preventDefault();
    search(text);
  };

  const open = async (hcad: string, importedId: number | null) => {
    setOpening(hcad);
    setError('');
    try {
      onOpen(importedId ?? (await api.importProperty(hcad)).id);
    } catch (err) {
      setError(errorMessage(err));
      setOpening(null);
    }
  };

  return (
    <div className="glass-panel panel stack" style={{ borderColor: 'rgba(59, 130, 246, 0.35)' }}>
      <div className="section-title"><Radio size={17} color="#34D399" /> Search live Houston records</div>
      <form className="row" onSubmit={submit}>
        <input className="input" style={{ flex: 1, minWidth: 220 }} value={text} onChange={(e) => setText(e.target.value)}
               placeholder="Street address (e.g. 1801 SAKOWITZ) or HCAD number" aria-label="Search live Houston records" />
        <button className="btn btn-primary" disabled={loading || text.trim().length < 3}>
          <Search size={15} /> {loading ? 'Searching…' : 'Search'}
        </button>
      </form>
      <div className="row small">
        <span className="muted">Try:</span>
        {EXAMPLES.map((q) => (
          <button key={q} className="chip" onClick={() => { setText(q); search(q); }}>{q}</button>
        ))}
      </div>
      {error && <Notice tone="error">{error}</Notice>}
      {result && (
        <div className="stack-sm">
          {result.note && <Notice tone="warn">{result.note}</Notice>}
          {result.rows_without_hcad > 0 && (
            <p className="small muted">{result.rows_without_hcad} matching row(s) had no HCAD parcel number and cannot be opened.</p>
          )}
          {result.candidates.length === 0 ? <p className="small muted">No parcels matched in the Houston records.</p> : (
            <table className="data">
              <thead><tr><th>HCAD</th><th>Address(es)</th><th>ZIP</th><th>Rows matched</th><th>Cases</th><th /></tr></thead>
              <tbody>
                {result.candidates.map((c) => (
                  <tr key={c.hcad}>
                    <td className="mono">{c.hcad}</td>
                    <td>
                      {c.addresses.join(' / ')}
                      {c.shares_address_with_other_parcel && <div className="small" style={{ color: '#FCD34D' }}>Same address as another parcel</div>}
                    </td>
                    <td>{c.zips.join(', ') || '—'}</td>
                    <td>{c.matched_rows}</td>
                    <td>{c.case_count_in_sample}</td>
                    <td>
                      <button className="btn btn-primary btn-sm" disabled={opening !== null} onClick={() => open(c.hcad, c.imported_property_id)}>
                        {opening === c.hcad ? 'Fetching…' : 'Open'} <ArrowRight size={13} />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}
    </div>
  );
}
