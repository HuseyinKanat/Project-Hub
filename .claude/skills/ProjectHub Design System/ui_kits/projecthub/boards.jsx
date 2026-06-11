/* global React, Icon, KeyChip, LabelChip */
function BoardCard({ board, onOpen }) {
  return (
    <div className="card board-card" onClick={onOpen}>
      <span className="beam" />
      <div className="row1">
        <span className="board-key">{board.key}</span>
        <Icon name="arrow-right" size={18} style={{ color: "var(--text-muted)" }} />
      </div>
      <h3>{board.name}</h3>
      <div className="desc">{board.desc}</div>
      <div className="meta">
        <span className="label-chip mono">{board.type}</span>
        <span className="label-chip mono">{board.states} states</span>
        {board.open > 0 && <span className="label-chip mono">{board.open} open</span>}
        {board.live && <span style={{ marginLeft: "auto", display: "inline-flex", alignItems: "center", gap: 6, fontSize: 11, color: "var(--success)" }}><i className="dot dot-sm pulse" style={{ background: "var(--success)" }} />Live</span>}
      </div>
    </div>
  );
}

function BoardsPage({ onOpenBoard }) {
  return (
    <div className="page">
      <div className="container">
        <div className="pagehead">
          <div>
            <h1>Boards</h1>
            <div className="sub">7 boards · {window.PH.BOARDS.reduce((a, b) => a + b.open, 0)} open tickets</div>
          </div>
          <button className="btn btn-primary"><Icon name="plus" size={15} />New board</button>
        </div>
        <div className="boards-grid">
          {window.PH.BOARDS.map((b) => <BoardCard key={b.key} board={b} onOpen={() => onOpenBoard(b)} />)}
        </div>
      </div>
    </div>
  );
}

Object.assign(window, { BoardsPage, BoardCard });
