/* global React, Icon, StatusPill, Avatar, KeyChip */
const { useState: useStateS, useEffect: useEffectS, useRef: useRefS } = React;

function TopBar({ view, onNav, onCmd, onBell, bellCount, status, theme, onTheme }) {
  return (
    <header className="topbar">
      <div className="wordmark">
        <span><span style={{ color: "var(--text-primary)" }}>Project</span><span style={{ color: "var(--accent)" }}>Hub</span></span>
      </div>
      <nav className="topnav">
        <a className={view === "boards" || view === "board" || view === "ticket" || view === "settings" ? "active" : ""} onClick={() => onNav("boards")}>Boards</a>
      </nav>
      <div className="spacer" />
      <div className="actions">
        {status && <StatusPill status={status} />}
        <button className="kbar" onClick={onCmd}>
          <Icon name="search" size={14} />
          <span>Search…</span>
          <span className="kbd">⌘K</span>
        </button>
        <button className="iconbtn" onClick={onBell} title="Notifications">
          <Icon name="bell" size={18} />
          {bellCount > 0 && <span className="bell-count">{bellCount}</span>}
        </button>
        <button className="iconbtn" title="Theme" onClick={onTheme}><Icon name={theme === "light" ? "sun" : "moon"} size={18} /></button>
        <Avatar name="Admin" />
      </div>
    </header>
  );
}

const CMD_ITEMS = [
  { group: "Tickets", icon: "ticket", label: "PH-167 · Branch graph UX rework", meta: "task" },
  { group: "Tickets", icon: "ticket", label: "PH-169 · Diff panel scroll jump", meta: "bug" },
  { group: "Tickets", icon: "ticket", label: "PH-170 · ⌘K command menu", meta: "feature" },
  { group: "Boards", icon: "layout-grid", label: "ProjectHub", meta: "PH" },
  { group: "Boards", icon: "layout-grid", label: "Jarwis Bench", meta: "BENCH" },
  { group: "Commits", icon: "git-commit", label: "6927b8de · Merge PH-167", meta: "main" },
  { group: "Actions", icon: "plus", label: "Create new ticket", meta: "C" },
  { group: "Actions", icon: "git-branch", label: "Open Branch Graph", meta: "" },
  { group: "Actions", icon: "settings", label: "Board settings", meta: "" },
];

function CommandMenu({ open, onClose }) {
  const [q, setQ] = useStateS("");
  const [sel, setSel] = useStateS(0);
  const inputRef = useRefS(null);
  const items = CMD_ITEMS.filter((i) => i.label.toLowerCase().includes(q.toLowerCase()) || i.group.toLowerCase().includes(q.toLowerCase()));
  useEffectS(() => { if (open) { setQ(""); setSel(0); setTimeout(() => inputRef.current?.focus(), 30); } }, [open]);
  useEffectS(() => {
    if (!open) return;
    const h = (e) => {
      if (e.key === "Escape") onClose();
      if (e.key === "ArrowDown") { e.preventDefault(); setSel((s) => Math.min(s + 1, items.length - 1)); }
      if (e.key === "ArrowUp") { e.preventDefault(); setSel((s) => Math.max(s - 1, 0)); }
      if (e.key === "Enter") onClose();
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [open, items.length]);
  if (!open) return null;
  const groups = [...new Set(items.map((i) => i.group))];
  let flatIdx = -1;
  return (
    <div className="scrim" onClick={onClose}>
      <div className="cmd" onClick={(e) => e.stopPropagation()}>
        <div className="cmd-input">
          <Icon name="search" size={18} style={{ color: "var(--text-muted)" }} />
          <input ref={inputRef} value={q} onChange={(e) => { setQ(e.target.value); setSel(0); }} placeholder="Search tickets, boards, commits…" />
          <span className="kbd" style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--text-muted)" }}>ESC</span>
        </div>
        <div className="cmd-list">
          {items.length === 0 && <div className="cmd-group">No results</div>}
          {groups.map((g) => (
            <div key={g}>
              <div className="cmd-group">{g}</div>
              {items.filter((i) => i.group === g).map((i) => {
                flatIdx++;
                const idx = flatIdx;
                return (
                  <div key={i.label} className={`cmd-item ${sel === idx ? "active" : ""}`} onMouseEnter={() => setSel(idx)} onClick={onClose}>
                    <span className="ic"><Icon name={i.icon} size={16} /></span>
                    <span>{i.label}</span>
                    {i.meta && <span className="meta">{i.meta}</span>}
                  </div>
                );
              })}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

function NotificationPanel({ open, onClose }) {
  if (!open) return null;
  const notes = [
    { icon: "git-merge", c: "var(--accent)", t: "PH-167 merged to main", s: "45s ago", fresh: true },
    { icon: "activity", c: "var(--success)", t: "jarwis-qa moved PH-161 → in_test", s: "8m ago", fresh: true },
    { icon: "message-square", c: "var(--text-secondary)", t: "jarwis-architect commented on PH-162", s: "1h ago" },
  ];
  return (
    <div className="scrim" style={{ alignItems: "flex-start", justifyContent: "flex-end", paddingTop: 60, paddingRight: 20, background: "transparent", backdropFilter: "none" }} onClick={onClose}>
      <div style={{ width: 340, background: "color-mix(in srgb, var(--bg-raised) 96%, transparent)", border: "1px solid var(--hairline-cyan)", borderRadius: 14, boxShadow: "var(--shadow-lg)", overflow: "hidden", animation: "ph-pop var(--dur-slow) var(--ease-out)" }} onClick={(e) => e.stopPropagation()}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "13px 15px", borderBottom: "1px solid var(--hairline)" }}>
          <strong style={{ fontSize: 13 }}>Notifications</strong>
          <span className="eyebrow">2 new</span>
        </div>
        {notes.map((n, i) => (
          <div key={i} style={{ display: "flex", gap: 11, padding: "12px 15px", borderBottom: "1px solid var(--hairline)", background: n.fresh ? "var(--accent-subtle)" : "transparent" }}>
            <span style={{ color: n.c, marginTop: 1 }}><Icon name={n.icon} size={16} /></span>
            <div style={{ flex: 1 }}>
              <div style={{ fontSize: 13, color: "var(--text-primary)" }}>{n.t}</div>
              <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 2, fontFamily: "var(--font-mono)" }}>{n.s}</div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function Toasts({ toasts }) {
  return (
    <div className="toast-wrap">
      {toasts.map((t) => (
        <div key={t.id} className="toast">
          <span className="ic"><Icon name="check-circle-2" size={17} /></span>
          <span style={{ color: "var(--text-primary)" }}>{t.msg}</span>
        </div>
      ))}
    </div>
  );
}

Object.assign(window, { TopBar, CommandMenu, NotificationPanel, Toasts });
