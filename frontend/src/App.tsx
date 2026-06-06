import React, { useEffect, useState } from "react";
import { getMe } from "./api";
import type { Employee } from "./types";
import TicketList from "./components/TicketList";
import TicketDetail from "./components/TicketDetail";
import AgentWorkspace from "./components/AgentWorkspace";

type View = "list" | "detail" | "workspace";

export default function App() {
  const [view, setView] = useState<View>("list");
  const [selectedTicketId, setSelectedTicketId] = useState<number | null>(null);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [me, setMe] = useState<Employee | null>(null);

  useEffect(() => {
    getMe()
      .then(setMe)
      .catch(() => {
        // best-effort — don't block UI if /api/me fails
      });
  }, []);

  function handleSelectTicket(id: number) {
    setSelectedTicketId(id);
    setView("detail");
  }

  function handleSessionStarted(ticketId: number, sid: string) {
    setSelectedTicketId(ticketId);
    setSessionId(sid);
    setView("workspace");
  }

  function handleBackToList() {
    setView("list");
    setSelectedTicketId(null);
    setSessionId(null);
  }

  function handleBackToDetail() {
    setView("detail");
    setSessionId(null);
  }

  function handleSessionDone() {
    setView("list");
    setSelectedTicketId(null);
    setSessionId(null);
  }

  return (
    <div className="app">
      <header className="app-header">
        <div className="app-header-inner">
          <span className="app-title">AI Service Desk Autopilot</span>
          {me && (
            <span className="app-user">
              {`${me.firstname} ${me.lastname}`}{me.teamname ? ` · ${me.teamname}` : ""}
            </span>
          )}
        </div>
      </header>

      <main className="app-main">
        {view === "list" && (
          <TicketList onSelectTicket={handleSelectTicket} />
        )}

        {view === "detail" && selectedTicketId !== null && (
          <TicketDetail
            ticketId={selectedTicketId}
            onBack={handleBackToList}
            onSessionStarted={handleSessionStarted}
          />
        )}

        {view === "workspace" && selectedTicketId !== null && sessionId !== null && (
          <AgentWorkspace
            ticketId={selectedTicketId}
            sessionId={sessionId}
            onBack={handleBackToDetail}
            onDone={handleSessionDone}
          />
        )}
      </main>
    </div>
  );
}
