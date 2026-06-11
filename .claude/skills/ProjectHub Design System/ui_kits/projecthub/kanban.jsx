/* global React, Icon, KeyChip, TypeChip, Priority, LabelChip, AgentPhase */
function TicketCard({ ticket, onOpen }) {
  return (
    <article className="ticket" onClick={onOpen}>
      <div className="t-top">
        <span className="t-key">{ticket.key}</span>
        <TypeChip type={ticket.type} />
      </div>
      <div className="t-title">{ticket.title}</div>
      {ticket.phase && <AgentPhase agent={ticket.phase.agent} phase={ticket.phase.phase} />}
      <div className="t-meta">
        <Priority level={ticket.priority} />
        <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
          <Icon name="wifi" size={12} />{ticket.updated} · {ticket.assignee.replace("jarwis-", "")}
        </span>
      </div>
      {ticket.labels.length > 0 && (
        <div className="t-labels">
          {ticket.labels.slice(0, 3).map((l) => <LabelChip key={l}>{l}</LabelChip>)}
        </div>
      )}
    </article>
  );
}

function KanbanColumn({ state, tickets, onOpenTicket }) {
  return (
    <div className="kcol">
      <div className="kcol-head" style={{ background: `linear-gradient(180deg, color-mix(in srgb, ${state.color} 7%, var(--bg-surface)), var(--bg-surface))` }}>
        <span className="nm"><i className="dot" style={{ background: state.color }} />{state.label}</span>
        <span className="kcol-count">{tickets.length}</span>
      </div>
      <div className="kcol-body">
        {tickets.length === 0 ? (
          <div className="kcol-empty">Empty</div>
        ) : (
          tickets.map((t) => <TicketCard key={t.key} ticket={t} onOpen={() => onOpenTicket(t)} />)
        )}
      </div>
    </div>
  );
}

function KanbanBoard({ onOpenTicket }) {
  return (
    <div className="kanban">
      {window.PH.STATES.map((s) => (
        <KanbanColumn key={s.id} state={s} tickets={window.PH.TICKETS.filter((t) => t.state === s.id)} onOpenTicket={onOpenTicket} />
      ))}
    </div>
  );
}

Object.assign(window, { TicketCard, KanbanColumn, KanbanBoard });
