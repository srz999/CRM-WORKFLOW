import { useEffect, useState } from "react";
import { api } from "../api.js";
import { ErrorBox, PriorityBadge, ReviewFlag } from "./ui.jsx";

export default function ConversationInput({ llmConfigured, onProcessed, onOpen }) {
  const [samples, setSamples] = useState([]);
  const [sampleId, setSampleId] = useState("");
  const [text, setText] = useState("");
  const [customerRef, setCustomerRef] = useState("");
  const [state, setState] = useState({ status: "idle" }); // idle | loading | success | error
  const [seedMsg, setSeedMsg] = useState("");

  useEffect(() => {
    api.samples().then(setSamples).catch((e) => setState({ status: "error", error: e.message }));
  }, []);

  const pickSample = (id) => {
    setSampleId(id);
    const s = samples.find((x) => x.sample_id === id);
    setText(s ? s.text : "");
    setCustomerRef(s?.customer_ref || "");
    setState({ status: "idle" });
  };

  const seed = async () => {
    setSeedMsg("Loading…");
    try {
      const r = await api.seedSamples();
      setSeedMsg(`Loaded ${r.conversations} sample conversations and ${r.customers} customers into the database.`);
    } catch (e) {
      setSeedMsg(`Failed: ${e.message}`);
    }
  };

  const submit = async (e) => {
    e.preventDefault();
    setState({ status: "loading" });
    try {
      const res = await api.submitConversation({
        text,
        customer_ref: customerRef.trim() || null,
        sample_id: sampleId || null,
      });
      setState({ status: res.run.status === "failed" ? "error" : "success", result: res,
                 error: res.run.status === "failed" ? res.run.error : null });
      onProcessed();
    } catch (err) {
      setState({ status: "error", error: err.message });
    }
  };

  const retry = async () => {
    const id = state.result?.conversation.id;
    setState({ status: "loading" });
    try {
      const res = await api.reprocess(id);
      setState({ status: res.run.status === "failed" ? "error" : "success", result: res, error: res.run.error });
      onProcessed();
    } catch (err) {
      setState({ status: "error", error: err.message });
    }
  };

  const result = state.result;
  const run = result?.run;

  return (
    <section className="card" aria-labelledby="input-h">
      <div className="card-head">
        <h2 id="input-h">Process a conversation</h2>
        <button type="button" className="btn ghost" onClick={seed}>Load samples into database</button>
      </div>
      {seedMsg && <div className="alert info">{seedMsg}</div>}
      {!llmConfigured && (
        <div className="alert warn">
          OPENAI_API_KEY is not set on the backend, so processing will fail safely. Add it to backend/.env and
          restart the API.
        </div>
      )}
      <form onSubmit={submit} className="form">
        <div className="row">
          <label className="field grow">
            <span>Sample conversation</span>
            <select value={sampleId} onChange={(e) => pickSample(e.target.value)}>
              <option value="">— Type your own or pick a sample —</option>
              {samples.map((s) => (
                <option key={s.sample_id} value={s.sample_id}>
                  {s.sample_id} · {s.title}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>Customer reference (optional)</span>
            <input value={customerRef} onChange={(e) => setCustomerRef(e.target.value)} placeholder="e.g. CUST-1001" />
          </label>
        </div>
        <label className="field">
          <span>Conversation text</span>
          <textarea
            rows={6}
            value={text}
            onChange={(e) => { setText(e.target.value); setSampleId(""); }}
            placeholder="Paste a customer conversation…"
            required
            minLength={10}
          />
        </label>
        <div className="row end">
          <button className="btn primary" disabled={state.status === "loading" || text.trim().length < 10}>
            {state.status === "loading" ? "Processing… (agents running)" : "Process conversation"}
          </button>
        </div>
      </form>

      <ErrorBox error={state.status === "error" ? state.error : null} />

      {run && (
        <div className="result">
          <div className={`alert ${run.status === "completed" ? "success" : run.status === "failed" ? "error" : "warn"}`}>
            Run #{run.id}: <strong>{run.status.replace("_", " ")}</strong> — {run.issues_found} issue(s),{" "}
            {run.tickets_created} ticket(s) created
            {run.tickets_updated > 0 && `, ${run.tickets_updated} already existed (no duplicates)`}
            {run.issues_failed > 0 && `, ${run.issues_failed} failed`}
            {run.status !== "completed" && run.status !== "failed" && (
              <button className="btn small" onClick={retry}>Retry failed issues</button>
            )}
            {run.status === "failed" && result.conversation.id && (
              <button className="btn small" onClick={retry}>Retry</button>
            )}
          </div>
          {result.conversation.issues.length > 0 && (
            <>
              <h3>Extracted issues → tickets</h3>
              <ol className="issues">
                {result.conversation.issues.map((i) => {
                  const t = result.conversation.tickets.find((x) => x.issue_id === i.id);
                  return (
                    <li key={i.id}>
                      <div className="issue-line">
                        <span>{i.summary}</span>
                        {t ? (
                          <button className="link" onClick={() => onOpen(t.id)}>
                            {t.ticket_number}
                          </button>
                        ) : (
                          <span className="badge status-failed">{i.processing_status}</span>
                        )}
                      </div>
                      <div className="muted small">
                        {i.customer_impact && <>Impact: {i.customer_impact} · </>}
                        {i.category ? `${i.category} / ${i.subcategory}` : "not classified"}{" "}
                        {i.recommended_priority && <PriorityBadge priority={i.recommended_priority} />}{" "}
                        <ReviewFlag show={i.review_status === "required"} />
                      </div>
                    </li>
                  );
                })}
              </ol>
            </>
          )}
        </div>
      )}
    </section>
  );
}
