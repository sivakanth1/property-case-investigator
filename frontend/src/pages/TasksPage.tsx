import { useCallback, useEffect, useState } from 'react';
import { CheckCircle2, ListChecks, MapPin, RotateCcw, Save } from 'lucide-react';
import { api, type PropertySummary, type Task, type TaskStatus } from '../api/backendClient';
import { Empty, EvidenceIds, Notice, PriorityBadge, StatusBadge } from '../components/ui';
import { ACTION_LABELS, errorMessage, formatTime } from '../lib/format';

const VERIFIED_NOTE = 'Current inspection completed; no further action needed.';
const STATUS_OPTIONS: { value: TaskStatus; label: string }[] = [
  { value: 'open', label: 'Open' },
  { value: 'in_progress', label: 'In progress' },
  { value: 'verified', label: 'Verified' },
  { value: 'dismissed', label: 'Dismissed' },
];

interface Props {
  propertyId?: number;
  onFilter: (propertyId?: number) => void;
  onOpenProperty: (propertyId: number) => void;
}

export function TasksPage({ propertyId, onFilter, onOpenProperty }: Props) {
  const [properties, setProperties] = useState<PropertySummary[]>([]);
  const [tasks, setTasks] = useState<Task[] | null>(null);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    try {
      const [p, t] = await Promise.all([api.properties(), api.tasks(propertyId)]);
      setProperties(p);
      setTasks(t);
      setError('');
    } catch (e) {
      setError(errorMessage(e));
    }
  }, [propertyId]);

  useEffect(() => {
    load();
  }, [load]);

  const byId = new Map(properties.map((p) => [p.id, p]));
  const replace = (t: Task) => setTasks((prev) => prev?.map((x) => (x.id === t.id ? t : x)) ?? null);
  const order: Record<string, number> = { open: 0, in_progress: 1, verified: 2, dismissed: 3 };
  const sorted = [...(tasks ?? [])].sort((a, b) => order[a.status] - order[b.status] || b.id - a.id);

  return (
    <div className="stack-lg">
      <div className="row-between">
        <div className="stack-sm">
          <h1 className="page-title row"><ListChecks size={24} color="#60A5FA" /> Verification tasks</h1>
          <p className="secondary">
            Tasks are internal work items created through the commit gate. Changing a status records feedback that the next
            investigation reads; it never changes the city's record.
          </p>
        </div>
        <select className="input" value={propertyId ?? ''} onChange={(e) => onFilter(e.target.value ? Number(e.target.value) : undefined)}
                aria-label="Filter by property">
          <option value="">All properties</option>
          {properties.map((p) => <option key={p.id} value={p.id}>{p.address} ({p.hcad})</option>)}
        </select>
      </div>
      {error && <Notice tone="error">{error}</Notice>}
      {tasks === null && !error && <Empty>Loading tasks…</Empty>}
      {tasks?.length === 0 && <Empty>No tasks yet. Run an investigation from the Properties page.</Empty>}
      {sorted.map((t) => (
        <TaskCard key={`${t.id}:${t.updated_at}`} task={t} property={byId.get(t.property_id)} onUpdated={replace} onOpenProperty={onOpenProperty} />
      ))}
    </div>
  );
}

function TaskCard({ task, property, onUpdated, onOpenProperty }: {
  task: Task; property?: PropertySummary; onUpdated: (t: Task) => void; onOpenProperty: (id: number) => void;
}) {
  const [status, setStatus] = useState<TaskStatus>(task.status);
  const [note, setNote] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  const submit = async (body: { status?: TaskStatus; note?: string }) => {
    setSaving(true);
    setError('');
    try {
      onUpdated(await api.updateTask(task.id, body));
      setNote('');
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setSaving(false);
    }
  };

  const save = () => {
    const body = { status: status !== task.status ? status : undefined, note: note.trim() || undefined };
    if (!body.status && !body.note) {
      setError('Change the status or write a note first.');
      return;
    }
    submit(body);
  };

  const closed = task.status === 'verified' || task.status === 'dismissed';
  return (
    <div className="glass-panel panel stack" style={{ borderLeft: `4px solid ${closed ? '#475569' : task.priority === 'high' ? '#F59E0B' : '#3B82F6'}` }}>
      <div className="row-between">
        <div className="row">
          <span className="chip">task #{task.id}</span>
          <span className="badge badge-outline">{ACTION_LABELS[task.action_type] ?? task.action_type}</span>
          <PriorityBadge priority={task.priority} />
          <StatusBadge status={task.status} />
        </div>
        {property && (
          <button className="link row" onClick={() => onOpenProperty(property.id)}>
            <MapPin size={12} /> {property.address}
          </button>
        )}
      </div>
      <div className="card-title">{task.title}</div>
      <p className="secondary small" style={{ lineHeight: 1.6 }}>{task.reason}</p>
      <div className="row small"><span className="muted">Cases:</span>{task.case_ids.map((c) => <span key={c} className="chip">{c}</span>)}</div>
      <EvidenceIds ids={task.evidence_ids} label="Source evidence" />

      <div className="record stack-sm">
        <div className="row">
          <label className="small muted" htmlFor={`status-${task.id}`}>Status</label>
          <select id={`status-${task.id}`} className="input small" value={status} onChange={(e) => setStatus(e.target.value as TaskStatus)}>
            {STATUS_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>
          {!closed && (
            <button className="btn btn-secondary btn-sm" onClick={() => { setStatus('verified'); setNote(VERIFIED_NOTE); }}>
              <CheckCircle2 size={13} /> Fill “inspection completed”
            </button>
          )}
          {closed && (
            <button className="btn btn-secondary btn-sm" disabled={saving}
                    onClick={() => submit({ status: 'open', note: note.trim() || undefined })}>
              <RotateCcw size={13} /> Reopen
            </button>
          )}
        </div>
        <textarea className="input" value={note} onChange={(e) => setNote(e.target.value)} maxLength={2000}
                  placeholder="Feedback for the next investigation, e.g. what an inspection found…" aria-label="Feedback note" />
        <div className="row">
          <button className="btn btn-primary btn-sm" disabled={saving} onClick={save}><Save size={13} /> {saving ? 'Saving…' : 'Save feedback'}</button>
          {error && <span className="small" style={{ color: '#FDA4AF' }}>{error}</span>}
        </div>
      </div>

      {task.feedback.length > 0 && (
        <div className="stack-sm">
          <span className="small muted">Feedback history</span>
          {task.feedback.map((f) => (
            <div key={f.id} className="row small">
              <span className="muted mono">{formatTime(f.created_at)}</span>
              <StatusBadge status={f.action === 'note' ? 'draft' : f.action === 'reopened' ? 'open' : f.action} />
              {f.note && <span className="secondary">{f.note}</span>}
            </div>
          ))}
        </div>
      )}
      <span className="small muted">Created {formatTime(task.created_at)} · updated {formatTime(task.updated_at)}
        {task.last_run_id ? ` · last touched by run #${task.last_run_id}` : ''}</span>
    </div>
  );
}
