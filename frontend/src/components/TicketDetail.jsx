import { useEffect, useState } from "react";
import { api } from "../api.js";
import { ErrorBox, PriorityBadge, ReviewFlag, STATUS_LABEL, StatusBadge, fmtDate } from "./ui.jsx";

// Mirrors the backend's allowed transitions (the backend enforces them).
const NEXT = {
  open: ["in_progress", "waiting_on_customer", "resolved", "closed", "needs_review"],
  needs_review: ["open", "closed"],
  in_progress: ["open", "waiting_on_customer", "resolved"],
  waiting_on_customer: ["in_progress", "resolved", "closed"],
  resolved: ["closed", "open"],
  closed: ["open"],
};

export default function TicketDetail({ ticketId, meta, onClose, onChanged }) {
  const [t, setT] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState("");
  const [team, setTeam] = useState("");
  const [note, setNote] = useState("");
  const [actor, setActor] = useState("support-agent");

  useEffect(() => {
    setT(null);
    setError("");
    api.ticket(ticketId).then((d) => { setT(d); setTeam(d.team); setStatus(""); }).catch((e) => setError(e.message));
  }, [ticketId]);

  useEffect(() => {
    const onKey = (e) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const act = async (fn) => {
    setBusy(true);
    setError("");
    try {
      const d = await fn();
      setT(d); setTeam(d.team); setStatus(""); setNote("");
      onChanged();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  const save = (e) => {
    e.preventDefault();
    const body = { actor, note: note || null };
    if (status) body.status = status;
    if (team !== t.team) body.team = team;
    if (!body.status && !body.team) return setError("Choose a new status or team first.");
    act(() => api.updateTicket(t.id, body));
  };

  const issue = t?.issue;

  return (
    <div className="drawer-backdrop" onClick={onClose}>
      <aside className="drawer" role="dialog" aria-modal="true" aria-label="Ticket details" onClick={(e) => e.stopPropagation()}>
        <div className="drawer-head">
          <div>
            <div className="mono muted">{t?.ticket_number || "Loading…"}</div>
            <h2>{t?.title}</h2>
          </div>
          <button className="btn ghost" onClick={onClose} aria-label="Close">✕</button>
        </div>
        <ErrorBox error={error} />
        {t && (
          <div className="drawer-body">
            <div className="chips">
              <PriorityBadge priority={t.priority} />
              <StatusBadge status={t.status} />
              <ReviewFlag show={t.review_required} />
              <span className="badge plain">{t.team}</span>
              {t.due_at && <span className="badge plain">SLA {t.sla_hours}h · due {fmtDate(t.due_at)}</span>}
            </div>

            {t.review_required && (
              <div className="alert warn">
                <strong>Human review required.</strong>
                <ul>{issue.review_reasons.map((r, i) => <li key={i}>{r}</li>)}</ul>
                <button className="btn small" disabled={busy}
                        onClick={() => act(() => api.completeReview(t.id, { actor, note: note || null }))}>
                  Mark review complete
                </button>
              </div>
            )}

            <h3>Recommended next action</h3>
            <p>{t.recommended_action}</p>
            {t.requires_human_approval && (
              <p className="alert info small">Requires human approval: {t.approval_reason || "policy"}. The system has not
                performed this action.</p>
            )}

            <h3>Extracted issue</h3>
            <dl className="kv">
              <dt>Summary</dt><dd>{issue.summary}</dd>
              <dt>Type</dt><dd>{issue.issue_type}</dd>
              <dt>Classification</dt>
              <dd>{t.category_label} / {issue.subcategory}
                {issue.classification_confidence != null && (
                  <span className="muted"> · confidence {Math.round(issue.classification_confidence * 100)}%</span>
                )}
              </dd>
              <dt>Customer</dt>
              <dd>{t.customer ? `${t.customer.reference}${t.customer.name ? ` · ${t.customer.name}` : ""}` : "Not identified"}</dd>
              {issue.customer_impact && (<><dt>Impact</dt><dd>{issue.customer_impact}</dd></>)}
              {issue.requested_action && (<><dt>Customer request</dt><dd>{issue.requested_action}</dd></>)}
              {issue.missing_information.length > 0 && (
                <><dt>Missing info</dt><dd>{issue.missing_information.join(", ")}</dd></>
              )}
            </dl>
            <h4>Supporting evidence</h4>
            {issue.evidence.length ? (
              issue.evidence.map((q, i) => <blockquote key={i}>“{q}”</blockquote>)
            ) : (
              <p className="muted">No verified evidence.</p>
            )}

            <h3>Priority decision</h3>
            <p className="small">
              Agent proposed: <strong>{issue.llm_priority || "—"}</strong> → final: <PriorityBadge priority={t.priority} />
            </p>
            <ul className="factors">
              {issue.priority_factors.map((f, i) => <li key={i}>{f}</li>)}
            </ul>

            <h3>Original conversation</h3>
            <p className="muted small">Conversation #{t.conversation_id}</p>
            <pre className="convo">{t.conversation_text}</pre>

            <h3>Update ticket</h3>
            <form className="form" onSubmit={save}>
              <div className="row">
                <label className="field grow">
                  <span>Status</span>
                  <select value={status} onChange={(e) => setStatus(e.target.value)}>
                    <option value="">Keep: {STATUS_LABEL[t.status]}</option>
                    {(NEXT[t.status] || []).map((s) => <option key={s} value={s}>{STATUS_LABEL[s]}</option>)}
                  </select>
                </label>
                <label className="field grow">
                  <span>Assigned team</span>
                  <select value={team} onChange={(e) => setTeam(e.target.value)}>
                    {(meta?.teams || []).map((x) => <option key={x} value={x}>{x}</option>)}
                  </select>
                </label>
              </div>
              <div className="row">
                <label className="field grow">
                  <span>Note (optional)</span>
                  <input value={note} onChange={(e) => setNote(e.target.value)} maxLength={1000} />
                </label>
                <label className="field">
                  <span>Your name</span>
                  <input value={actor} onChange={(e) => setActor(e.target.value)} required maxLength={100} />
                </label>
              </div>
              <div className="row end">
                <button className="btn primary" disabled={busy}>Save changes</button>
              </div>
            </form>

            <h3>Event history</h3>
            <ol className="events">
              {t.events.map((e) => (
                <li key={e.id}>
                  <span className="muted small nowrap">{fmtDate(e.created_at)}</span>
                  <span>{e.description}</span>
                  <span className="muted small">by {e.actor}</span>
                </li>
              ))}
            </ol>
          </div>
        )}
      </aside>
    </div>
  );
}
