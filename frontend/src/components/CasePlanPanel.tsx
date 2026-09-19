import { useState } from 'react';
import { CheckSquare, RefreshCw, Sparkles, Square } from 'lucide-react';
import { api, type CaseGroup, type CasePlan, type RunMode } from '../api/backendClient';
import { errorMessage, formatTime } from '../lib/format';
import { ModeBadge, Notice } from './ui';

interface Props {
  propertyId: number;
  caseItem: CaseGroup;
  aiMode: RunMode | null;
}

export function CasePlanPanel({ propertyId, caseItem, aiMode }: Props) {
  const [plan, setPlan] = useState<CasePlan | null>(caseItem.plan);
  const [busy, setBusy] = useState(false);
  const [savingStep, setSavingStep] = useState<string | null>(null);
  const [error, setError] = useState('');

  const closed = !caseItem.is_open;
  const generate = async (regenerate = false) => {
    if (regenerate && !window.confirm('Replace the saved steps for this case? The current list and its progress are archived.')) return;
    setBusy(true);
    setError('');
    try {
      setPlan(await api.generatePlan(propertyId, caseItem.case_id!, regenerate));
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  const toggle = async (stepId: string, completed: boolean) => {
    setSavingStep(stepId);
    setError('');
    try {
      setPlan(await api.updateStep(propertyId, stepId, completed ? 'completed' : 'pending'));
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setSavingStep(null);
    }
  };

  if (!plan) {
    const label = aiMode === 'deterministic_demo' ? 'Get resolution steps (demo mode)' : 'Get AI resolution steps';
    return (
      <div className="stack-sm">
        <div className="row">
          <button className="btn btn-primary btn-sm" disabled={closed || busy || !caseItem.case_id} onClick={() => generate()}
                  title={closed ? 'This case is closed in the source records, so no resolution steps are needed.' : undefined}>
            <Sparkles size={13} /> {busy ? (aiMode === 'live_model' ? 'Asking Featherless AI…' : 'Building checklist…') : label}
          </button>
          {closed && <span className="small muted">Case is closed in the source records.</span>}
        </div>
        {error && <Notice tone="error">{error}</Notice>}
      </div>
    );
  }

  const pct = plan.progress.total ? Math.round((plan.progress.done / plan.progress.total) * 100) : 0;
  return (
    <div className="record stack-sm">
      <div className="row-between">
        <div className="row">
          <span className="section-title" style={{ fontSize: '0.95rem' }}><Sparkles size={15} color="#93C5FD" /> Resolution steps</span>
          <ModeBadge mode={plan.mode} model={plan.model} />
          <span className="chip">{plan.progress.done}/{plan.progress.total} done</span>
        </div>
        {!closed && (
          <button className="btn btn-secondary btn-sm" disabled={busy} onClick={() => generate(true)}
                  title="Generate a new list and archive this one">
            <RefreshCw size={12} /> {busy ? 'Regenerating…' : 'Regenerate'}
          </button>
        )}
      </div>
      {closed && <Notice tone="info">This case is now closed in the source records. The saved steps are kept for reference.</Notice>}
      <p className="small secondary">{plan.summary}</p>
      <div style={{ height: 5, background: 'rgba(255,255,255,0.08)', borderRadius: 3, overflow: 'hidden' }}>
        <div style={{ width: `${pct}%`, height: '100%', background: pct === 100 ? '#10B981' : '#3B82F6', transition: 'width 0.3s' }} />
      </div>
      <ol className="stack-sm" style={{ listStyle: 'none' }}>
        {plan.steps.map((step) => (
          <li key={step.id} className="row" style={{ alignItems: 'flex-start', flexWrap: 'nowrap' }}>
            <button onClick={() => toggle(step.id, !step.done)} disabled={savingStep === step.id}
                    aria-label={step.done ? `Mark step ${step.position} not done` : `Mark step ${step.position} done`}
                    style={{ marginTop: 2, color: step.done ? '#34D399' : 'var(--text-secondary)' }}>
              {step.done ? <CheckSquare size={18} /> : <Square size={18} />}
            </button>
            <div className="stack-sm" style={{ gap: '0.2rem', opacity: step.done ? 0.6 : 1 }}>
              <span style={{ fontWeight: 700, textDecoration: step.done ? 'line-through' : 'none' }}>
                {step.position}. {step.title}
              </span>
              <span className="small secondary">{step.detail}</span>
              <span className="row small">
                {step.ordinance && <span className="chip">Ord. {step.ordinance}</span>}
                {step.evidence_ids.slice(0, 6).map((id) => <span key={id} className="chip">E{id}</span>)}
                {step.completed_at && <span className="muted">completed {formatTime(step.completed_at)}</span>}
              </span>
            </div>
          </li>
        ))}
      </ol>
      <span className="small muted">
        Saved {formatTime(plan.created_at)}. {plan.storage === 'account'
          ? 'Kept in your account: the same steps and ticks appear on any device you sign in from.'
          : 'Kept on this computer only (guest). Sign in to keep these steps and ticks in your account.'}
      </span>
      {error && <Notice tone="error">{error}</Notice>}
    </div>
  );
}
