export const ACTION_LABELS: Record<string, string> = {
  verify_current_condition: 'Verify current condition',
  review_case_history: 'Review case history',
  reconcile_records: 'Reconcile records',
};

export function formatTime(iso?: string | null): string {
  return iso ? new Date(iso).toLocaleString() : '—';
}

export function errorMessage(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}
