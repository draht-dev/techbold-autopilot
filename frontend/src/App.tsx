import { useEffect, useState } from "react";
import { Link, Route, Routes } from "react-router-dom";
import { api, Employee } from "./api/client";
import TicketList from "./pages/TicketList";
import TicketDetail from "./pages/TicketDetail";
import Workspace from "./pages/Workspace";

export default function App() {
  const [me, setMe] = useState<Employee | null>(null);

  useEffect(() => {
    api.getMe().then(setMe).catch(() => setMe(null));
  }, []);

  return (
    <div className="app">
      <header className="shell-bar">
        <div className="title">
          <Link to="/">AI Service Desk Autopilot</Link>
        </div>
        <div className="meta">
          {me ? (
            <span>
              {me.firstname} {me.lastname} · {me.teamname}
            </span>
          ) : (
            <span>Not connected to ERP</span>
          )}
        </div>
      </header>
      <div className="content">
        <Routes>
          <Route path="/" element={<TicketList />} />
          <Route path="/tickets/:id" element={<TicketDetail />} />
          <Route path="/runs/:runId" element={<Workspace />} />
        </Routes>
      </div>
    </div>
  );
}
