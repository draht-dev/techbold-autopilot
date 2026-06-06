import { Link, Route, Routes } from "react-router-dom";
import TicketList from "./pages/TicketList";
import TicketDetail from "./pages/TicketDetail";
import Workspace from "./pages/Workspace";

export default function App() {
  return (
    <div className="app">
      <header className="shell-bar">
        <div className="title">
          <Link to="/">AI Service Desk Autopilot</Link>
        </div>
        <div className="meta" />
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
