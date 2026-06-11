/* global React, Icon, RoleChip, Checkbox, StatusPill, Tabs, Button */
function WorkflowStates() {
  return (
    <div className="panel">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 4 }}>
        <div>
          <h3 style={{ fontSize: 15, margin: 0 }}>Workflow States</h3>
          <p style={{ margin: "4px 0 0", fontSize: 12 }}>Drag to reorder states. States define the columns on your kanban board.</p>
        </div>
        <Button variant="primary" size="sm" icon="plus">Add State</Button>
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 8, marginTop: 14 }}>
        {window.PH.STATES.map((s, i) => (
          <div key={s.id} className="state-row">
            <Icon name="grip-vertical" size={15} style={{ color: "var(--text-muted)" }} />
            <i className="dot" style={{ background: s.color, width: 12, height: 12 }} />
            <span className="mono" style={{ fontSize: 13, color: "var(--text-primary)" }}>{s.id}</span>
            {i === 0 && <span className="tag-init">initial</span>}
            {s.id === "done" && <span className="tag-init" style={{ color: "var(--success)", background: "var(--success-soft)" }}>terminal</span>}
            <span style={{ marginLeft: "auto", fontSize: 11, color: "var(--text-muted)" }}>{window.PH.TICKETS.filter((t) => t.state === s.id).length || "No"} tickets</span>
            <button className="iconbtn" style={{ width: 28, height: 28 }}><Icon name="trash-2" size={14} /></button>
          </div>
        ))}
      </div>
    </div>
  );
}

function WorkflowEditor() {
  const nodes = [
    { id: "backlog", x: 30, y: 20 }, { id: "to_do", x: 220, y: 20 }, { id: "in_progress", x: 410, y: 20 },
    { id: "blocked", x: 30, y: 130 }, { id: "in_review", x: 220, y: 130 }, { id: "in_test", x: 410, y: 130 },
    { id: "done", x: 220, y: 240 },
  ];
  const pos = Object.fromEntries(nodes.map((n) => [n.id, n]));
  const edges = [["backlog", "to_do"], ["to_do", "in_progress"], ["in_progress", "in_review"], ["in_progress", "blocked"], ["in_review", "in_test"], ["in_test", "done"], ["in_review", "in_progress"]];
  const NW = 150, NH = 52;
  return (
    <div className="panel">
      <h3 style={{ fontSize: 15, margin: 0 }}>Visual Workflow Editor</h3>
      <p style={{ margin: "4px 0 14px", fontSize: 12 }}>Click states and transitions to edit properties.</p>
      <div className="wf-canvas">
        <svg className="wf-edges" width="600" height="320">
          {edges.map(([a, b], i) => {
            const A = pos[a], B = pos[b];
            const x1 = A.x + NW / 2, y1 = A.y + NH, x2 = B.x + NW / 2, y2 = B.y;
            return <path key={i} d={`M ${x1} ${y1} C ${x1} ${(y1 + y2) / 2}, ${x2} ${(y1 + y2) / 2}, ${x2} ${y2}`} stroke="var(--hairline-strong)" strokeWidth="1.5" fill="none" markerEnd="url(#arr)" />;
          })}
          <defs><marker id="arr" markerWidth="8" markerHeight="8" refX="6" refY="3" orient="auto"><path d="M0 0 L6 3 L0 6 Z" fill="var(--text-muted)" /></marker></defs>
        </svg>
        {nodes.map((n) => {
          const s = window.PH.STATES.find((x) => x.id === n.id);
          const active = n.id === "in_review";
          return (
            <div key={n.id} className={`wf-node ${active ? "active" : ""}`} style={{ left: n.x, top: n.y, width: NW, height: NH }}>
              <i className="dot" style={{ background: s.color, position: "absolute", top: -4, left: -4, border: "2px solid var(--bg-surface)" }} />
              <span className="mono" style={{ fontSize: 13, color: "var(--text-primary)", fontWeight: 500 }}>{n.id}</span>
              <span style={{ fontSize: 10, color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: ".04em" }}>{n.id === "backlog" ? "initial" : n.id === "done" ? "terminal" : active ? "selected" : "state"}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function PermissionsMatrix() {
  const [grid, setGrid] = React.useState(() =>
    window.PH.TRANSITIONS.map((t) => window.PH.MATRIX_ROLES.map((r) => t.roles.includes(r)))
  );
  const toggle = (ti, ri) => setGrid((g) => g.map((row, i) => i === ti ? row.map((c, j) => j === ri ? !c : c) : row));
  return (
    <div className="panel" style={{ padding: 0, overflow: "hidden" }}>
      <div style={{ padding: 16 }}>
        <h3 style={{ fontSize: 15, margin: 0 }}>Permissions Matrix</h3>
        <p style={{ margin: "4px 0 0", fontSize: 12 }}>Each row is a workflow transition; each column is a board role. Check a cell to allow that role to perform the transition. Empty rows allow all roles.</p>
      </div>
      <div style={{ overflowX: "auto" }}>
        <table className="tbl matrix">
          <thead>
            <tr>
              <th>Transition</th>
              {window.PH.MATRIX_ROLES.map((r) => <th key={r}>{r.replace("_dev", "").replace("orchestrator", "orch")}</th>)}
            </tr>
          </thead>
          <tbody>
            {window.PH.TRANSITIONS.map((t, ti) => (
              <tr key={ti}>
                <td>
                  <span className="mono">{t.from} → {t.to}</span>
                  {t.roles.length === 0 && <span className="anyrole">Any role</span>}
                </td>
                {window.PH.MATRIX_ROLES.map((r, ri) => (
                  <td key={r}><span className={`cell-cb ${grid[ti][ri] ? "on" : ""}`} onClick={() => toggle(ti, ri)}>{grid[ti][ri] && <Icon name="check" size={12} stroke={3} />}</span></td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function MembersTab({ onAdd }) {
  return (
    <div className="panel">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
        <div>
          <h3 style={{ fontSize: 15, margin: 0 }}>Board Members</h3>
          <p style={{ margin: "4px 0 0", fontSize: 12 }}>Manage which actors have access to this board and their assigned roles.</p>
        </div>
        <Button variant="primary" size="sm" icon="user-plus" onClick={onAdd}>Add Member</Button>
      </div>
      <div style={{ fontSize: 12, color: "var(--text-muted)", margin: "14px 0 6px" }}>{window.PH.MEMBERS.length} members</div>
      <table className="tbl" style={{ marginTop: 2 }}>
        <thead><tr><th>Actor</th><th>Role</th><th>Type</th><th>Permissions</th><th></th></tr></thead>
        <tbody>
          {window.PH.MEMBERS.map((m) => (
            <tr key={m.actor}>
              <td><span className="mono" style={{ color: "var(--text-primary)" }}>{m.actor}</span></td>
              <td><select className="select" style={{ width: 150, padding: "6px 9px" }} defaultValue={m.role}><option>{m.role}</option></select></td>
              <td><span className="tag-type" style={{ color: m.type === "Human" ? "var(--success)" : "var(--accent)", background: m.type === "Human" ? "var(--success-soft)" : "var(--accent-soft)" }}>{m.type}</span></td>
              <td className="mono" style={{ fontSize: 12 }}>{m.perms} {m.perms === 1 ? "perm" : "perms"}</td>
              <td><button className="iconbtn" style={{ width: 28, height: 28 }}><Icon name="trash-2" size={14} /></button></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function RepositoryTab() {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <div className="panel">
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 16 }}>
          <h3 style={{ fontSize: 15, margin: 0 }}>Repository Connection</h3>
          <StatusPill status="Live" />
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14 }}>
          <div className="field"><label>Remote URL</label><input className="input mono" defaultValue="git@github.com:jarwis/project-hub.git" /></div>
          <div className="field"><label>Default branch</label><input className="input mono" defaultValue="main" /></div>
          <div className="field"><label>Webhook secret</label><input className="input mono" type="password" defaultValue="whsec_8f2a91c4d7e3" /></div>
          <div className="field"><label>Last sync</label><input className="input mono" defaultValue="45s ago — 17 commits" disabled /></div>
        </div>
        <div style={{ display: "flex", gap: 10, marginTop: 16 }}>
          <Button variant="primary" size="sm" icon="refresh-cw">Fetch & sync</Button>
          <Button variant="secondary" size="sm" icon="rotate-cw">Rotate secret</Button>
          <Button variant="danger" size="sm" icon="unlink" className="" style={{ marginLeft: "auto" }}>Detach</Button>
        </div>
      </div>
    </div>
  );
}

function AddMemberModal({ open, onClose, onAdd }) {
  if (!open) return null;
  return (
    <div className="scrim center" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head"><h3>Add Member</h3><button className="iconbtn" onClick={onClose}><Icon name="x" size={18} /></button></div>
        <div className="modal-body">
          <div className="field"><label>Actor</label><input className="input mono" placeholder="jarwis-…" autoFocus /></div>
          <div className="field"><label>Role</label><select className="select">{["admin", "pm", "architect", "backend_dev", "frontend_dev", "reviewer", "qa", "orchestrator"].map((r) => <option key={r}>{r}</option>)}</select></div>
          <div className="field"><label>Type</label><select className="select"><option>Agent</option><option>Human</option></select></div>
        </div>
        <div className="modal-foot">
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button variant="primary" onClick={() => { onAdd(); onClose(); }}>Add Member</Button>
        </div>
      </div>
    </div>
  );
}

function BoardSettings({ onBack, onToast }) {
  const [tab, setTab] = React.useState("workflow");
  const [addOpen, setAddOpen] = React.useState(false);
  return (
    <div className="page">
      <div className="container" style={{ maxWidth: 1180 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 16, padding: "24px 0 4px" }}>
          <a className="crumb" onClick={onBack}><Icon name="arrow-left" size={15} />Back to Board</a>
          <h1 style={{ fontSize: 26, margin: 0 }}>Board Settings</h1>
          <span className="board-key" style={{ fontSize: 12 }}>PH</span>
        </div>
        <div style={{ marginTop: 14 }}>
          <Tabs tabs={[{ id: "general", label: "General", icon: "settings" }, { id: "workflow", label: "Workflow", icon: "git-branch" }, { id: "members", label: "Members", icon: "users" }, { id: "repository", label: "Repository", icon: "git-merge" }]} active={tab} onChange={setTab} />
        </div>
        <div style={{ padding: "20px 0 48px", display: "flex", flexDirection: "column", gap: 16 }}>
          {tab === "general" && <div className="panel"><h3 style={{ fontSize: 15, margin: "0 0 4px" }}>General</h3><p style={{ fontSize: 12, margin: "0 0 16px" }}>Board name, key, and description.</p><div style={{ display: "grid", gridTemplateColumns: "1fr 120px", gap: 14 }}><div className="field"><label>Board name</label><input className="input" defaultValue="ProjectHub" /></div><div className="field"><label>Key</label><input className="input mono" defaultValue="PH" /></div><div className="field" style={{ gridColumn: "1/3" }}><label>Description</label><input className="input" defaultValue="Default ProjectHub board" /></div></div></div>}
          {tab === "workflow" && <React.Fragment><WorkflowStates /><WorkflowEditor /><PermissionsMatrix /></React.Fragment>}
          {tab === "members" && <MembersTab onAdd={() => setAddOpen(true)} />}
          {tab === "repository" && <RepositoryTab />}
        </div>
      </div>
      <AddMemberModal open={addOpen} onClose={() => setAddOpen(false)} onAdd={() => onToast("Member added")} />
    </div>
  );
}

Object.assign(window, { BoardSettings });
