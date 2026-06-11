/* global React, Icon, KeyChip, TypeChip, RoleChip, LabelChip, Priority, StatePill, Avatar, Tabs, AgentPhase */
function StateControl({ state }) {
  const [open, setOpen] = React.useState(false);
  const allowed = window.PH.TRANSITIONS.filter((t) => t.from === state || t.from === "*").map((t) => t.to);
  return (
    <div style={{ position: "relative" }}>
      <button className="btn btn-secondary btn-sm" onClick={() => setOpen(!open)} style={{ gap: 8 }}>
        <StatePill state={state} />
        <Icon name="chevron-down" size={14} />
      </button>
      {open && (
        <div className="menu" onMouseLeave={() => setOpen(false)}>
          <div className="menu-label">Move to →</div>
          {allowed.map((to) => {
            const s = window.PH.STATES.find((x) => x.id === to);
            return (
              <div key={to} className="menu-item" onClick={() => setOpen(false)}>
                <i className="dot dot-sm" style={{ background: s.color }} />
                <span className="mono">{to}</span>
                {(to === "in_review" || to === "in_test") && <span className="menu-hint">req fields</span>}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

function Field({ title, required, action, children }) {
  return (
    <section className="panel" style={{ padding: 0 }}>
      <div className="field-head">
        <span className="field-title">{title}{required && <span style={{ color: "var(--warning)" }}> *</span>}</span>
        <button className="btn btn-ghost btn-sm">{action || "Edit"}</button>
      </div>
      <div className="field-body">{children}</div>
    </section>
  );
}

function SideRow({ label, children }) {
  return (
    <div className="side-row">
      <span className="side-label">{label}</span>
      <span className="side-val">{children}</span>
    </div>
  );
}

function TicketDetail({ ticket, onBack }) {
  const [tab, setTab] = React.useState("all");
  const activity = [
    { who: "jarwis-pm", role: "pm", t: "[HANDOFF pm→architect] Branch graph UX rework (user-rejected G10 @xyflow card graph). Scope: replace node-card graph with SourceTree vertical commit list.", time: "9:43:21 AM" },
    { who: "jarwis-architect", role: "architect", t: "[HANDOFF architect] Spec net, hız önceliği. Frontend: technical_depth kendin doldur + implement.", time: "9:44:37 AM" },
    { who: "jarwis-frontend", role: "frontend_dev", t: "[HANDOFF frontend→reviewer] 2 commits (1 feat + 1 empty live-sync test), branch graph rework browser-verified.", time: "10:16:18 AM" },
  ];
  return (
    <div className="page">
      <div className="container" style={{ maxWidth: 1280 }}>
        <a className="crumb" onClick={onBack}><Icon name="arrow-left" size={15} />ProjectHub</a>
        <div className="td-head">
          <div className="td-head-main">
            <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 8 }}>
              <KeyChip>{ticket.key}</KeyChip>
              <TypeChip type={ticket.type} />
              {ticket.phase && <AgentPhase agent={ticket.phase.agent} phase={ticket.phase.phase} />}
            </div>
            <h1 style={{ fontSize: 24, fontWeight: 600, letterSpacing: "-0.015em", lineHeight: 1.25 }}>{ticket.title}</h1>
          </div>
          <StateControl state={ticket.state} />
        </div>

        <div className="td-grid">
          <div className="td-col">
            <Field title="Description">
              <p style={{ margin: 0 }}>Replace the rejected G10 <code style={{ color: "var(--accent)" }}>@xyflow/react</code> node-card graph with a SourceTree-style 3-pane Branch Graph. Newest commit on top (committed_at DESC), SVG lane gutter, branch click → filter, commit click → diff.</p>
            </Field>
            <Field title="Acceptance Criteria">
              <ul className="ac-list">
                <li>GIVEN board "Branch Graph", WHEN open, THEN commits render as a dense vertical list with a colored lane gutter.</li>
                <li>GIVEN a branch row, WHEN clicked, THEN the list filters to that branch's commits.</li>
                <li>GIVEN a commit row, WHEN clicked, THEN the diff panel mounts and shows the unified diff.</li>
                <li>GIVEN a live commit (refresh hook), THEN it appears at the TOP with a one-shot cyan glow.</li>
              </ul>
            </Field>
            <Field title="Technical Depth" required>
              <div className="mono" style={{ fontSize: 12, color: "var(--text-secondary)", lineHeight: 1.7 }}>
                <div style={{ color: "var(--text-primary)", fontWeight: 600, marginBottom: 6 }}>Implementation — Branch Graph SourceTree UX (PH-167)</div>
                BranchGraph.tsx: 3-pane [BranchSidebar | commit list + SVG gutter | DiffPanel].<br />
                branchGraphLayout.ts: assignLanes() two-pass O(N+E), laneColor(lane).<br />
                Rows: ROW_H=44px, short_sha mono · message · ref chips · ticket_key · author · time.
              </div>
            </Field>

            <section className="panel" style={{ padding: 0 }}>
              <div style={{ padding: "4px 16px 0" }}>
                <Tabs tabs={[{ id: "all", label: "All", count: 28 }, { id: "comments", label: "Comments", count: 3 }, { id: "history", label: "History", count: 33 }, { id: "git", label: "Git", count: 2 }]} active={tab} onChange={setTab} />
              </div>
              <div className="field-body" style={{ display: "flex", flexDirection: "column", gap: 14 }}>
                {activity.map((a, i) => (
                  <div key={i} className="activity-item">
                    <Avatar name={a.who} size="sm" />
                    <div style={{ flex: 1 }}>
                      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4 }}>
                        <span className="mono" style={{ fontSize: 12, color: "var(--text-primary)" }}>{a.who}</span>
                        <RoleChip role={a.role} />
                        <span className="mono" style={{ fontSize: 11, color: "var(--text-muted)", marginLeft: "auto" }}>{a.time}</span>
                      </div>
                      <div style={{ fontSize: 12.5, color: "var(--text-secondary)", lineHeight: 1.5 }}>{a.t}</div>
                    </div>
                  </div>
                ))}
                <div className="composer">
                  <input className="input" placeholder="Write a comment… (Markdown supported)" />
                  <button className="btn btn-primary btn-sm"><Icon name="send" size={14} />Send</button>
                </div>
              </div>
            </section>
          </div>

          <aside className="td-side">
            <div className="panel" style={{ padding: 16, display: "flex", flexDirection: "column", gap: 0 }}>
              <SideRow label="Priority"><Priority level={ticket.priority} /></SideRow>
              <SideRow label="Reporter"><span className="mono" style={{ fontSize: 12 }}>jarwis-pm</span></SideRow>
              <SideRow label="Assignee"><span style={{ display: "inline-flex", alignItems: "center", gap: 7 }}><Avatar name={ticket.assignee} size="sm" /><span className="mono" style={{ fontSize: 12 }}>{ticket.assignee}</span></span></SideRow>
              <SideRow label="Labels"><span style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>{ticket.labels.map((l) => <LabelChip key={l}>{l}</LabelChip>)}</span></SideRow>
              <SideRow label="Created"><span className="mono" style={{ fontSize: 12, color: "var(--text-muted)" }}>6/5/2026, 9:43</span></SideRow>
              <div className="side-row" style={{ borderBottom: "none" }}>
                <span className="side-label">Branch</span>
                <button className="branch-row mono"><Icon name="git-branch" size={13} />ph-167-branch-graph-ux-rework</button>
              </div>
            </div>
          </aside>
        </div>
      </div>
    </div>
  );
}

Object.assign(window, { TicketDetail });
