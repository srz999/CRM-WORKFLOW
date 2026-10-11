export const PRIORITIES = ["URGENT", "MEDIUM", "LOW"];

export const STATUS_LABEL = {
  open: "Open",
  needs_review: "Needs review",
  in_progress: "In progress",
  waiting_on_customer: "Waiting on customer",
  resolved: "Resolved",
  closed: "Closed",
};

export function PriorityBadge({ priority }) {
  return <span className={`badge prio-${priority?.toLowerCase()}`}>{priority}</span>;
}

export function StatusBadge({ status }) {
  return <span className={`badge status status-${status}`}>{STATUS_LABEL[status] || status}</span>;
}

export function ReviewFlag({ show }) {
  return show ? <span className="badge review" title="Human review required">Review</span> : null;
}

export function fmtDate(iso) {
  if (!iso) return "—";
  return new Date(iso).toLocaleString(undefined, {
    month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
  });
}

export function ErrorBox({ error }) {
  return error ? <div className="alert error" role="alert">{error}</div> : null;
}
