import { PRIORITIES, PriorityBadge, ReviewFlag } from "./ui.jsx";

function Stat({ label, value, tone }) {
  return (
    <div className={`stat ${tone || ""}`}>
      <div className="stat-value">{value ?? "–"}</div>
      <div className="stat-label">{label}</div>
    </div>
  );
}

export default function Overview({ stats, onOpen }) {
  const s = stats || { by_priority: {}, grouped_by_priority: {} };
  return (
    <section className="card" aria-labelledby="overview-h">
      <h2 id="overview-h">Overview</h2>
      <div className="stats">
        <Stat label="Total tickets" value={s.total} />
        <Stat label="Urgent" value={s.by_priority.URGENT} tone="t-urgent" />
        <Stat label="Medium" value={s.by_priority.MEDIUM} tone="t-medium" />
        <Stat label="Low" value={s.by_priority.LOW} tone="t-low" />
        <Stat label="Open" value={s.open} />
        <Stat label="Resolved / closed" value={s.resolved} />
        <Stat label="Needs review" value={s.needs_review} tone="t-review" />
      </div>
      <div className="lanes">
        {PRIORITIES.map((p) => {
          const items = s.grouped_by_priority[p] || [];
          return (
            <div key={p} className="lane">
              <div className="lane-head">
                <PriorityBadge priority={p} />
                <span className="muted">{s.by_priority[p] ?? 0} total · open shown</span>
              </div>
              {items.length === 0 && <div className="muted small pad">No open tickets</div>}
              {items.map((t) => (
                <button key={t.id} className="lane-item" onClick={() => onOpen(t.id)}>
                  <span className="mono small">{t.ticket_number}</span>
                  <span className="lane-title">{t.title}</span>
                  <span className="muted small">
                    {t.team} <ReviewFlag show={t.review_required} />
                  </span>
                </button>
              ))}
            </div>
          );
        })}
      </div>
    </section>
  );
}
