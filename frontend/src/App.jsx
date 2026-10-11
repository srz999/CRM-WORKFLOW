import { useCallback, useEffect, useState } from "react";
import { api } from "./api.js";
import ConversationInput from "./components/ConversationInput.jsx";
import Overview from "./components/Overview.jsx";
import TicketDetail from "./components/TicketDetail.jsx";
import TicketList from "./components/TicketList.jsx";
import { ErrorBox } from "./components/ui.jsx";

export default function App() {
  const [meta, setMeta] = useState(null);
  const [stats, setStats] = useState(null);
  const [error, setError] = useState("");
  const [refreshKey, setRefreshKey] = useState(0);
  const [openId, setOpenId] = useState(null);

  useEffect(() => {
    api.meta().then(setMeta).catch((e) => setError(e.message));
  }, []);

  useEffect(() => {
    api.stats().then((s) => { setStats(s); setError(""); }).catch((e) => setError(e.message));
  }, [refreshKey]);

  const refresh = useCallback(() => setRefreshKey((k) => k + 1), []);
  const close = useCallback(() => setOpenId(null), []);

  return (
    <>
      <header className="topbar">
        <div className="brand">
          <span className="logo" aria-hidden>◆</span>
          <span>CRM Workflow</span>
          <span className="muted small hide-sm">multi-agent ticket triage</span>
        </div>
        <div className="topbar-right">
          {meta && (
            <span className={`pill ${meta.llm_configured ? "ok" : "bad"}`}>
              {meta.llm_configured ? `LLM: ${meta.model}` : "LLM not configured"}
            </span>
          )}
          <button className="btn ghost" onClick={refresh}>Refresh</button>
        </div>
      </header>
      <main className="container">
        <ErrorBox error={error} />
        <Overview stats={stats} onOpen={setOpenId} />
        <ConversationInput llmConfigured={meta?.llm_configured ?? true} onProcessed={refresh} onOpen={setOpenId} />
        <TicketList meta={meta} refreshKey={refreshKey} onOpen={setOpenId} />
      </main>
      {openId && <TicketDetail ticketId={openId} meta={meta} onClose={close} onChanged={refresh} />}
    </>
  );
}
