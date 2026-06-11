/* global React, ReactDOM, TopBar, CommandMenu, NotificationPanel, Toasts, BoardsPage, KanbanBoard, BranchGraphPage, TicketDetail, BoardSettings, Tabs, Button, Icon, StatusPill */
const { useState, useEffect } = React;

function NewTicketModal({ open, onClose, onCreate }) {
  if (!open) return null;
  return (
    <div className="scrim center" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head"><h3>New ticket</h3><button className="iconbtn" onClick={onClose}><Icon name="x" size={18} /></button></div>
        <div className="modal-body">
          <div className="field"><label>Title</label><input className="input" placeholder="Describe the work…" autoFocus /></div>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
            <div className="field"><label>Type</label><select className="select"><option>task</option><option>feature</option><option>bug</option><option>epic</option></select></div>
            <div className="field"><label>Priority</label><select className="select"><option>medium</option><option>low</option><option>high</option><option>urgent</option></select></div>
          </div>
          <div className="field"><label>Description</label><textarea className="textarea" rows="3" placeholder="Markdown supported…" /></div>
        </div>
        <div className="modal-foot">
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button variant="primary" onClick={() => { onCreate(); onClose(); }}>Create</Button>
        </div>
      </div>
    </div>
  );
}

function BoardDetail({ board, onBack, onOpenTicket, onSettings, onNewTicket }) {
  const [tab, setTab] = useState("kanban");
  return (
    <div className="page">
      <div className="container" style={{ maxWidth: 1600 }}>
        <a className="crumb" onClick={onBack}><Icon name="arrow-left" size={15} />Boards</a>
        <div className="pagehead" style={{ paddingBottom: 0 }}>
          <div>
            <h1>{board.name}</h1>
            <div className="sub">{board.desc}</div>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <StatusPill status="Live" />
            <Button variant="primary" icon="plus" onClick={onNewTicket}>New ticket</Button>
            <Button variant="secondary" icon="settings" onClick={onSettings}>Settings</Button>
          </div>
        </div>
        <div style={{ marginTop: 16 }}>
          <Tabs tabs={[{ id: "kanban", label: "Kanban", icon: "layout-grid" }, { id: "graph", label: "Branch Graph", icon: "git-branch" }]} active={tab} onChange={setTab} />
        </div>
      </div>
      {tab === "kanban" ? (
        <div className="container" style={{ maxWidth: 1600 }}><KanbanBoard onOpenTicket={onOpenTicket} /></div>
      ) : (
        <BranchGraphPage />
      )}
    </div>
  );
}

function App() {
  const [view, setView] = useState("boards"); // boards | board | ticket | settings
  const [board, setBoard] = useState(window.PH.BOARDS[0]);
  const [ticket, setTicket] = useState(null);
  const [cmdOpen, setCmdOpen] = useState(false);
  const [bellOpen, setBellOpen] = useState(false);
  const [newOpen, setNewOpen] = useState(false);
  const [toasts, setToasts] = useState([]);
  const [theme, setTheme] = useState("dark");

  const toggleTheme = () => setTheme((t) => {
    const next = t === "dark" ? "light" : "dark";
    document.documentElement.classList.toggle("light", next === "light");
    document.documentElement.classList.toggle("dark", next === "dark");
    return next;
  });

  const toast = (msg) => {
    const id = Date.now();
    setToasts((t) => [...t, { id, msg }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 2600);
  };

  useEffect(() => {
    const h = (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); setCmdOpen((o) => !o); }
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, []);

  const status = view === "boards" ? null : "Live";

  return (
    <div className="app">
      <TopBar view={view} status={status} bellCount={2} theme={theme} onTheme={toggleTheme}
        onNav={(v) => { setView(v); }}
        onCmd={() => setCmdOpen(true)}
        onBell={() => setBellOpen(true)} />

      {view === "boards" && <BoardsPage onOpenBoard={(b) => { setBoard(b); setView("board"); }} />}
      {view === "board" && <BoardDetail board={board} onBack={() => setView("boards")}
        onOpenTicket={(t) => { setTicket(t); setView("ticket"); }}
        onSettings={() => setView("settings")}
        onNewTicket={() => setNewOpen(true)} />}
      {view === "ticket" && ticket && <TicketDetail ticket={ticket} onBack={() => setView("board")} />}
      {view === "settings" && <BoardSettings onBack={() => setView("board")} onToast={toast} />}

      <CommandMenu open={cmdOpen} onClose={() => setCmdOpen(false)} />
      <NotificationPanel open={bellOpen} onClose={() => setBellOpen(false)} />
      <NewTicketModal open={newOpen} onClose={() => setNewOpen(false)} onCreate={() => toast("Ticket created")} />
      <Toasts toasts={toasts} />
    </div>
  );
}

ReactDOM.createRoot(document.getElementById("root")).render(<App />);
