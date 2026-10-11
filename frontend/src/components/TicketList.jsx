import { useEffect, useState } from "react";
import { api } from "../api.js";
import { ErrorBox, PRIORITIES, PriorityBadge, ReviewFlag, STATUS_LABEL, StatusBadge, fmtDate } from "./ui.jsx";

export default function TicketList({ meta, refreshKey, onOpen }) {
  const [filters, setFilters] = useState({ priority: "", category: "", status: "", q: "" });
  const [query, setQuery] = useState("");
  const [tickets, setTickets] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  // debounce search input
  useEffect(() => {
    const t = setTimeout(() => setFilters((f) => ({ ...f, q: query })), 250);
    return () => clearTimeout(t);
  }, [query]);

  useEffect(() => {
    setLoading(true);
    api.tickets(filters)
      .then((r) => { setTickets(r); setError(""); })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [filters, refreshKey]);

  const set = (k) => (e) => setFilters((f) => ({ ...f, [k]: e.target.value }));
  const anyFilter = filters.priority || filters.category || filters.status || query;

  return (
    <section className="card" aria-labelledby="tickets-h">
      <div className="card-head">
        <h2 id="tickets-h">Tickets</h2>
        <span className="muted small">{loading ? "Loading…" : `${tickets.length} shown`}</span>
      </div>
      <div className="filters">
        <input
          type="search"
          placeholder="Search ticket ID, customer ref or summary"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          aria-label="Search tickets"
        />
        <select value={filters.priority} onChange={set("priority")} aria-label="Filter by priority">
          <option value="">All priorities</option>
          {PRIORITIES.map((p) => <option key={p} value={p}>{p}</option>)}
        </select>
        <select value={filters.category} onChange={set("category")} aria-label="Filter by category">
          <option value="">All categories</option>
          {(meta?.categories || []).map((c) => <option key={c.key} value={c.key}>{c.label}</option>)}
        </select>
        <select value={filters.status} onChange={set("status")} aria-label="Filter by status">
          <option value="">All statuses</option>
          {(meta?.statuses || []).map((s) => <option key={s} value={s}>{STATUS_LABEL[s]}</option>)}
        </select>
        {anyFilter && (
          <button className="btn ghost" onClick={() => { setQuery(""); setFilters({ priority: "", category: "", status: "", q: "" }); }}>
            Clear
          </button>
        )}
      </div>
      <ErrorBox error={error} />
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Ticket</th><th>Customer</th><th>Summary</th><th>Category</th><th>Priority</th>
              <th>Team</th><th>Status</th><th>Created</th>
            </tr>
          </thead>
          <tbody>
            {tickets.map((t) => (
              <tr key={t.id} onClick={() => onOpen(t.id)} tabIndex={0}
                  onKeyDown={(e) => e.key === "Enter" && onOpen(t.id)}>
                <td className="mono">{t.ticket_number}</td>
                <td className="mono">{t.customer_ref || <span className="muted">unknown</span>}</td>
                <td className="summary">{t.summary}</td>
                <td>{t.category_label}</td>
                <td><PriorityBadge priority={t.priority} /></td>
                <td>{t.team}</td>
                <td><StatusBadge status={t.status} /> <ReviewFlag show={t.review_required} /></td>
                <td className="nowrap">{fmtDate(t.created_at)}</td>
              </tr>
            ))}
            {!loading && tickets.length === 0 && (
              <tr><td colSpan={8} className="muted center pad">
                {anyFilter ? "No tickets match these filters." : "No tickets yet. Process a conversation above."}
              </td></tr>
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}
