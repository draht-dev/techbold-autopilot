import { Link, Route, Routes } from "react-router-dom";
import { ConversationProvider } from "@elevenlabs/react";
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
          {/* ConversationProvider must wrap Workspace so useConversation works
              inside it. Each Workspace mount gets its own provider instance. */}
          <Route
            path="/runs/:runId"
            element={
              <ConversationProvider>
                <Workspace />
              </ConversationProvider>
            }
          />
        </Routes>
      </div>
    </div>
  );
}
