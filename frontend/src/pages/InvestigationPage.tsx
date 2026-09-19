import { useCallback, useEffect, useRef, useState } from 'react';
import {
  Activity, BookmarkCheck, BookmarkPlus, ChevronDown, ChevronUp, ClipboardList, History, ListChecks, MapPin, Play,
  RefreshCw, RotateCcw, Sparkles,
} from 'lucide-react';
import {
  ACTIVE_RUN_STATUSES, api, type CaseGroup, type CasesResponse, type Investigation, type Proposal, type PropertySummary,
  type RunListItem, type RunMode, type Task,
} from '../api/backendClient';
import { CasePlanPanel } from '../components/CasePlanPanel';
import {
  CoveragePanel, Empty, EvidenceIds, EvidenceRecordView, ModeBadge, Notice, PriorityBadge, StatusBadge,
} from '../components/ui';
import { ACTION_LABELS, errorMessage, formatTime } from '../lib/format';

interface Props {
  propertyId: number;
  runId?: number;
  aiMode: RunMode | null;
  isSaved: (hcad: string) => boolean;
  onToggleSave: (p: { hcad: string; address: string; zip?: string | null }) => void;
  onSelectRun: (runId: number) => void;
  onOpenTasks: (propertyId: number) => void;
}

export function InvestigationPage({ propertyId, runId, aiMode, isSaved, onToggleSave, onSelectRun, onOpenTasks }: Props) {
  const [property, setProperty] = useState<PropertySummary | null>(null);
  const [cases, setCases] = useState<CasesResponse | null>(null);
  const [runs, setRuns] = useState<RunListItem[]>([]);
  const [run, setRun] = useState<Investigation | null>(null);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [error, setError] = useState('');
  const [message, setMessage] = useState<{ tone: 'ok' | 'warn' | 'error'; text: string } | null>(null);
  const [busy, setBusy] = useState(false);

  const loadProperty = useCallback(async () => {
    try {
      const [p, c, r, t] = await Promise.all([
        api.property(propertyId), api.cases(propertyId), api.investigations(propertyId), api.tasks(propertyId),
      ]);
      setProperty(p);
      setCases(c);
      setRuns(r);
      setTasks(t);
      setError('');
    } catch (e) {
      setError(errorMessage(e));
    }
  }, [propertyId]);

  useEffect(() => {
    loadProperty();
  }, [loadProperty]);

  // Opening a property re-fetches it from the live Houston API when the cached copy is stale.
  const [live, setLive] = useState<'idle' | 'checking' | 'refreshed' | 'cached'>('idle');
  const liveChecked = useRef(false);
  useEffect(() => {
    if (!property || liveChecked.current || !property.coverage.refresh_due) return;
    liveChecked.current = true;
    const run = async () => {
      setLive('checking');
      try {
        const r = await api.refresh(propertyId);
        setLive(r.status === 'refreshed' ? 'refreshed' : 'cached');
        if (r.status !== 'refreshed') setMessage({ tone: 'warn', text: r.message });
      } catch (e) {
        setLive('cached');
        setMessage({ tone: 'warn', text: `Live Houston API unavailable; showing cached records. ${errorMessage(e)}` });
      }
      await loadProperty();
    };
    run();
  }, [property, propertyId, loadProperty]);

  const selectedRunId = runId ?? runs[0]?.id;

  const loadRun = useCallback(async () => {
    if (!selectedRunId) return;
    try {
      setRun(await api.investigation(selectedRunId));
    } catch (e) {
      setError(errorMessage(e));
    }
  }, [selectedRunId]);

  useEffect(() => {
    loadRun();
  }, [loadRun]);

  const active = run ? ACTIVE_RUN_STATUSES.includes(run.status) : false;
  useEffect(() => {
    if (!active) return;
    const t = setInterval(loadRun, 2000);
    return () => clearInterval(t);
  }, [active, loadRun]);

  const wasActive = useRef(false);
  useEffect(() => {
    if (wasActive.current && !active) loadProperty();
    wasActive.current = active;
  }, [active, loadProperty]);

  const act = async (fn: () => Promise<void>) => {
    setBusy(true);
    setMessage(null);
    try {
      await fn();
    } catch (e) {
      setMessage({ tone: 'error', text: errorMessage(e) });
    } finally {
      setBusy(false);
    }
  };

  const start = () => act(async () => {
    const r = await api.startInvestigation(propertyId);
    await loadProperty();
    onSelectRun(r.run_id);
  });
  const resume = () => act(async () => {
    await api.resume(run!.id);
    await loadRun();
  });
  const refresh = () => act(async () => {
    const r = await api.refresh(propertyId);
    setMessage({ tone: r.status === 'refreshed' ? 'ok' : 'warn', text: r.message });
    await loadProperty();
  });
  const decide = (p: Proposal, approve: boolean) => act(async () => {
    if (approve) {
      const r = await api.approveProposal(p.id);
      if (r.outcome.outcome === 'rejected') setMessage({ tone: 'error', text: `Commit gate rejected ${p.proposal_id}: ${r.outcome.issues?.join(' ')}` });
    } else {
      await api.rejectProposal(p.id);
    }
    await Promise.all([loadRun(), loadProperty()]);
  });

  if (error && !property) return <Notice tone="error">{error}</Notice>;
  if (!property || !cases) return <Empty>Loading property…</Empty>;

  const propertyActiveRun = property.active_run_id;
  const memory = tasks.filter((t) => t.feedback.length > 0 || t.status === 'verified' || t.status === 'dismissed');

  return (
    <div className="stack-lg">
      <div className="glass-panel panel stack">
        <div className="row-between">
          <div className="stack-sm">
            <div className="row">
              <span className="chip">HCAD {property.hcad}</span>
              {property.zip && <span className="chip">ZIP {property.zip}</span>}
              {live === 'checking' && <span className="row small secondary"><span className="pulsing-dot pulsing-dot-amber" /> Checking live Houston records…</span>}
              {live === 'refreshed' && <span className="badge badge-success">Live data refreshed just now</span>}
              {live === 'idle' && property.coverage.origin === 'live_api' && (
                <span className="small muted">Live data fetched {formatTime(property.coverage.fetched_at)}</span>
              )}
            </div>
            <h1 className="page-title row"><MapPin size={24} color="#60A5FA" />{property.address}</h1>
          </div>
          <div className="row">
            <button className="btn btn-secondary" onClick={() => onToggleSave(property)}>
              {isSaved(property.hcad) ? <BookmarkCheck size={15} color="#34D399" /> : <BookmarkPlus size={15} />}
              {isSaved(property.hcad) ? 'In portfolio' : 'Save to portfolio'}
            </button>
            <button className="btn btn-secondary" disabled={busy} onClick={refresh} title="Re-fetch this HCAD from the Houston API">
              <RefreshCw size={15} /> Refresh source records
            </button>
            <button className="btn btn-secondary" onClick={() => onOpenTasks(propertyId)}>
              <ListChecks size={15} /> Tasks ({tasks.length})
            </button>
            <button className="btn btn-primary" disabled={busy || Boolean(propertyActiveRun)} onClick={start}>
              <Play size={15} /> {propertyActiveRun ? `Run #${propertyActiveRun} in progress` : 'Start investigation'}
            </button>
          </div>
        </div>
        {message && <Notice tone={message.tone}>{message.text}</Notice>}
        {error && <Notice tone="error">{error}</Notice>}
      </div>

      <OpenCases propertyId={propertyId} cases={cases.cases.filter((c) => c.is_open)} aiMode={aiMode} />

      <div className="grid-2">
        <div className="stack">
          {run ? <RunPanel run={run} runs={runs} busy={busy} onSelectRun={onSelectRun} onResume={resume} onDecide={decide} />
               : <Empty>No investigation has run for this property yet. Start one to have the agent review these records.</Empty>}
        </div>
        <div className="stack">
          <div className="glass-panel panel stack-sm">
            <div className="section-title">Source coverage</div>
            <CoveragePanel coverage={property.coverage} />
          </div>
          <div className="glass-panel panel stack-sm">
            <div className="section-title"><History size={16} color="#A78BFA" /> Memory used by the next run</div>
            {memory.length === 0 ? <p className="small muted">No human feedback recorded yet for this property.</p> : (
              <ul className="stack-sm" style={{ listStyle: 'none' }}>
                {memory.map((t) => (
                  <li key={t.id} className="record small stack-sm">
                    <div className="row"><StatusBadge status={t.status} /><span className="secondary">#{t.id} {t.title}</span></div>
                    {t.feedback[0]?.note && <span className="muted">Latest feedback: “{t.feedback[0].note}”</span>}
                  </li>
                ))}
              </ul>
            )}
          </div>
          {run && <ActivityLog run={run} />}
        </div>
      </div>

      <CaseTimeline propertyId={propertyId} data={cases} aiMode={aiMode} />
    </div>
  );
}

function OpenCases({ propertyId, cases, aiMode }: { propertyId: number; cases: CaseGroup[]; aiMode: RunMode | null }) {
  return (
    <div className="stack">
      <div className="section-title">Open cases: resolution steps ({cases.length})</div>
      {cases.length === 0 ? (
        <p className="small muted">No case is open in the source records for this property, so there are no resolution steps to generate.</p>
      ) : (
        <>
          <p className="small secondary">
            Cases the source does not mark closed. Generate a step-by-step resolution checklist once; it is saved and shown
            again whenever you open this property, and you can tick steps off as you complete them.
          </p>
          {cases.map((c) => <CaseCard key={c.case_id} c={c} propertyId={propertyId} aiMode={aiMode} expanded />)}
        </>
      )}
    </div>
  );
}

function RunPanel({ run, runs, busy, onSelectRun, onResume, onDecide }: {
  run: Investigation; runs: RunListItem[]; busy: boolean; onSelectRun: (id: number) => void; onResume: () => void;
  onDecide: (p: Proposal, approve: boolean) => void;
}) {
  const active = ACTIVE_RUN_STATUSES.includes(run.status);
  const b = run.budget;
  return (
    <div className="glass-panel panel stack">
      <div className="row-between">
        <div className="section-title"><Sparkles size={16} color="#93C5FD" /> Investigation #{run.id}</div>
        {runs.length > 1 && (
          <select className="input small" value={run.id} onChange={(e) => onSelectRun(Number(e.target.value))} aria-label="Select run">
            {runs.map((r) => <option key={r.id} value={r.id}>#{r.id} · {r.status} · {formatTime(r.created_at)}</option>)}
          </select>
        )}
      </div>
      <div className="row">
        <StatusBadge status={run.status} />
        <ModeBadge mode={run.mode} model={run.model} />
        {active && <span className="row small secondary"><span className="pulsing-dot pulsing-dot-green" /> updating every 2 s</span>}
      </div>
      {run.mode === 'deterministic_demo' && (
        <Notice tone="demo">Deterministic demo mode: a scripted policy exercised the same tools, review and commit gate. No
          AI model was called. Add FEATHERLESS_API_KEY to backend/.env for live Featherless AI runs.</Notice>
      )}
      <div className="row small">
        <span className="chip">tool calls {b.tool_calls_used}/{b.max_tool_calls}</span>
        {b.revision_calls_used > 0 && <span className="chip">revision calls {b.revision_calls_used}</span>}
        <span className="chip">{b.elapsed_s ?? 0}s of {b.time_budget_s}s</span>
        {b.phase && <span className="chip">phase {b.phase}</span>}
      </div>
      {run.status === 'interrupted' && (
        <Notice tone="warn">
          <div className="row-between">
            <span>{run.error}</span>
            <button className="btn btn-primary btn-sm" disabled={busy} onClick={onResume}><RotateCcw size={13} /> Resume</button>
          </div>
        </Notice>
      )}
      {run.error && run.status !== 'interrupted' && <Notice tone="error">{run.error}</Notice>}
      {run.summary && (
        <div className="stack-sm">
          <span className="small muted">Summary</span>
          <p style={{ lineHeight: 1.65 }}>{run.summary}</p>
        </div>
      )}

      <div className="stack-sm">
        <div className="section-title"><ClipboardList size={16} color="#93C5FD" /> Findings ({run.findings.length})</div>
        {run.findings.length === 0 && <p className="small muted">{active ? 'Waiting for findings…' : 'No findings proposed.'}</p>}
        {run.findings.map((f) => (
          <div key={f.id} className="record stack-sm">
            <div className="row"><span className="chip">{f.proposal_id}</span><span className="badge badge-outline">{f.type.replace(/_/g, ' ')}</span><StatusBadge status={f.review_status} /></div>
            <p>{f.summary}</p>
            <p className="small" style={{ color: '#FDE68A' }}>Uncertainty: {f.uncertainty}</p>
            {f.issues.length > 0 && <Notice tone="warn">{f.issues.join(' ')}</Notice>}
            <EvidenceIds ids={f.evidence_ids} />
          </div>
        ))}
      </div>

      <div className="stack-sm">
        <div className="section-title"><ListChecks size={16} color="#93C5FD" /> Proposed tasks ({run.proposals.length})</div>
        {run.proposals.length === 0 && <p className="small muted">{active ? 'Waiting for proposals…' : 'No tasks proposed.'}</p>}
        {run.proposals.map((p) => {
          const task = run.tasks.find((t) => t.id === p.task_id);
          const reviewable = ['draft', 'needs_review', 'revise'].includes(p.status) && !active;
          return (
            <div key={p.id} className="record stack-sm">
              <div className="row">
                <span className="chip">{p.proposal_id}</span>
                <span className="badge badge-outline">{ACTION_LABELS[p.action_type] ?? p.action_type}</span>
                <PriorityBadge priority={p.priority} />
                <StatusBadge status={p.status} />
                {task && <span className="small secondary">→ task #{task.id} ({task.status.replace('_', ' ')})</span>}
              </div>
              <div className="card-title" style={{ fontSize: '0.98rem' }}>{p.title}</div>
              <p className="small secondary">{p.reason}</p>
              <div className="row small"><span className="muted">Cases:</span>{p.case_ids.map((c) => <span key={c} className="chip">{c}</span>)}</div>
              {p.issues.length > 0 && <Notice tone="warn">{p.issues.join(' ')}</Notice>}
              <EvidenceIds ids={p.evidence_ids} />
              {reviewable && (
                <div className="row">
                  <span className="small muted">Held for human review. Approval still passes the deterministic commit gate.</span>
                  <button className="btn btn-accent-emerald btn-sm" disabled={busy} onClick={() => onDecide(p, true)}>Approve</button>
                  <button className="btn btn-danger btn-sm" disabled={busy} onClick={() => onDecide(p, false)}>Reject</button>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function ActivityLog({ run }: { run: Investigation }) {
  return (
    <div className="glass-panel panel stack-sm">
      <div className="section-title"><Activity size={16} color="#34D399" /> Activity log</div>
      <ul className="event-log">
        {run.events.map((e) => (
          <li key={e.id} className={/error|failed/.test(e.type) ? 'ev-error' : undefined}>
            <span className="muted mono">{new Date(e.created_at).toLocaleTimeString()}</span>
            <span className="mono secondary">{e.type}</span>
            <span>{e.summary}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function ViolationSummary({ c }: { c: CaseGroup }) {
  const rows = c.records.filter((r) => r.fields.category || r.fields.short_description);
  if (!rows.length) return <p className="small muted">This case has only a project record; no violation details are listed.</p>;
  return (
    <ul className="stack-sm" style={{ listStyle: 'none' }}>
      {rows.map((r) => (
        <li key={r.evidence_id} className="small row" style={{ alignItems: 'flex-start' }}>
          <span className="chip">E{r.evidence_id}</span>
          <span>
            <strong>{r.fields.category ?? 'Violation'}</strong>
            {r.fields.short_description && <span className="secondary">: {r.fields.short_description}</span>}
            {r.fields.ordinance && <span className="muted"> (Ord. {r.fields.ordinance})</span>}
            {r.fields.deadline_date && <span className="muted"> · historical deadline {r.fields.deadline_date}</span>}
          </span>
        </li>
      ))}
    </ul>
  );
}

function CaseTimeline({ propertyId, data, aiMode }: { propertyId: number; data: CasesResponse; aiMode: RunMode | null }) {
  const recurring = data.category_recurrence.filter((r) => r.distinct_cases > 1);
  const closed = data.cases.filter((c) => !c.is_open);
  return (
    <div className="stack">
      <div className="section-title">Closed cases ({closed.length} of {data.cases.filter((c) => c.case_id).length} distinct cases)</div>
      <p className="small secondary">
        Rows that share a case ID are one case, not separate incidents. Recurrence below counts distinct cases.
      </p>
      {recurring.length > 0 && (
        <div className="row small">
          {recurring.map((r) => <span key={r.category} className="chip">{r.category}: {r.distinct_cases} distinct cases</span>)}
        </div>
      )}
      {closed.map((c) => <CaseCard key={c.case_id ?? 'none'} c={c} propertyId={propertyId} aiMode={aiMode} />)}
    </div>
  );
}

function CaseCard({ c, propertyId, aiMode, expanded = false }: {
  c: CaseGroup; propertyId: number; aiMode: RunMode | null; expanded?: boolean;
}) {
  const [open, setOpen] = useState(false);
  return (
    <div className="glass-panel panel stack-sm">
      <div className="row-between">
        <div className="row">
          <span className="card-title" style={{ fontSize: '1rem' }}>{c.case_id ? `Case ${c.case_id}` : 'Rows without a case ID'}</span>
          {c.source_statuses.map((s) => <span key={s} className={`badge ${s.toUpperCase() === 'CLOSED' ? 'badge-low' : 'badge-high'}`}>source: {s}</span>)}
        </div>
        <button className="btn btn-secondary btn-sm" onClick={() => setOpen(!open)}>
          {open ? <ChevronUp size={13} /> : <ChevronDown size={13} />} {c.records.length} source record{c.records.length === 1 ? '' : 's'}
        </button>
      </div>
      <div className="row small secondary">
        <span>Created {c.created_date ?? 'unknown'}</span>
        {c.latest_record_date !== c.created_date && <span>latest record {c.latest_record_date}</span>}
        <span>{c.violation_row_count} violation row(s), {c.project_row_count} project row(s)</span>
        {c.categories.length > 0 && <span>{c.categories.join(', ')}</span>}
        {c.service_request_ids.length > 0 && <span className="mono">SR {c.service_request_ids.join(', ')}</span>}
      </div>
      {expanded && <ViolationSummary c={c} />}
      {c.case_id && <CasePlanPanel key={c.plan?.id ?? 'none'} propertyId={propertyId} caseItem={c} aiMode={aiMode} />}
      {open && c.records.map((r) => <EvidenceRecordView key={r.evidence_id} record={r} />)}
    </div>
  );
}
