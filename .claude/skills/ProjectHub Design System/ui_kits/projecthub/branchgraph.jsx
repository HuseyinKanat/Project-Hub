/* global React, Icon, KeyChip */
const LANE_W = 15, GUT_PAD = 12, ROW_H = 44;

// Precompute, for each lane, the first/last row it appears on (for branch/merge curves).
function laneRanges(commits) {
  const r = {};
  commits.forEach((c, i) => {
    if (!(c.lane in r)) r[c.lane] = { first: i, last: i };
    else r[c.lane].last = i;
  });
  return r;
}

function Gutter({ i, commit, ranges, maxLane }) {
  const cx = GUT_PAD + commit.lane * LANE_W;
  const lane0x = GUT_PAD;
  const W = GUT_PAD + maxLane * LANE_W + GUT_PAD;
  const mid = ROW_H / 2;
  const color = window.PH.LANES[commit.lane % window.PH.LANES.length];
  const lines = [];

  // pass-through lines for every active lane (faint), excluding this commit's lane endpoints
  Object.entries(ranges).forEach(([laneStr, rng]) => {
    const lane = +laneStr;
    if (i < rng.first || i > rng.last) return;
    const x = GUT_PAD + lane * LANE_W;
    const lc = window.PH.LANES[lane % window.PH.LANES.length];
    const isFirst = i === rng.first && lane > 0;
    const isMerge = i === rng.last && lane > 0 && commit.lane === lane && commit.mergeInto !== undefined;
    if (isFirst) {
      // branch out: curve from lane0 at top → this lane at mid, then down
      lines.push(<path key={`f${lane}`} d={`M ${lane0x} 0 C ${lane0x} ${mid * 0.7}, ${x} ${mid * 0.5}, ${x} ${mid}`} stroke={lc} strokeWidth="2" fill="none" opacity="0.9" />);
      if (i < rng.last) lines.push(<line key={`fb${lane}`} x1={x} y1={mid} x2={x} y2={ROW_H} stroke={lc} strokeWidth="2" />);
    } else if (isMerge) {
      // merge in: vertical from top to mid, then curve to lane0 at bottom
      lines.push(<line key={`mt${lane}`} x1={x} y1="0" x2={x} y2={mid} stroke={lc} strokeWidth="2" />);
      lines.push(<path key={`mc${lane}`} d={`M ${x} ${mid} C ${x} ${mid * 1.5}, ${lane0x} ${mid * 1.3}, ${lane0x} ${ROW_H}`} stroke={lc} strokeWidth="2" fill="none" opacity="0.9" strokeDasharray="0" />);
    } else {
      lines.push(<line key={`p${lane}`} x1={x} y1="0" x2={x} y2={ROW_H} stroke={lc} strokeWidth="2" opacity={lane === commit.lane ? 1 : 0.55} />);
    }
  });

  return (
    <svg width={W} height={ROW_H} style={{ flexShrink: 0 }}>
      {lines}
      <circle cx={cx} cy={mid} r={commit.merge ? 6 : 5} fill={commit.fresh ? "var(--bg-base)" : color} stroke={color} strokeWidth={commit.merge || commit.fresh ? 2.5 : 0}
        style={commit.fresh ? { filter: "drop-shadow(0 0 5px var(--accent))" } : {}} />
    </svg>
  );
}

function CommitRow({ commit, i, ranges, maxLane, selected, compact, onSelect }) {
  return (
    <div className={`bg-row ${selected ? "sel" : ""} ${commit.fresh ? "fresh" : ""} ${compact ? "compact" : ""}`} onClick={onSelect}>
      <Gutter i={i} commit={commit} ranges={ranges} maxLane={maxLane} />
      <span className="bg-sha mono">{commit.sha}</span>
      <span className="bg-msg">
        <span className="bg-msg-text">{commit.msg}</span>
        {commit.refs && commit.refs.map((r) => <span key={r} className="bg-ref mono">{r}</span>)}
      </span>
      {commit.ticket && <span className="key-chip" style={{ fontSize: 10 }}>{commit.ticket}</span>}
      <span className="bg-author">{commit.author}</span>
      <span className="bg-time mono">{commit.time}</span>
    </div>
  );
}

function DiffPanel({ commit, onClose }) {
  return (
    <aside className="diff-panel">
      <div className="diff-head">
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <span className="mono" style={{ fontSize: 11, color: "var(--text-muted)" }}>{commit.sha}2773</span>
          <button className="iconbtn" style={{ width: 28, height: 28 }} onClick={onClose}><Icon name="x" size={16} /></button>
        </div>
        <div className="diff-title">{commit.msg}</div>
        <div className="diff-stat">
          <strong style={{ color: "var(--text-primary)" }}>{commit.files}</strong> files changed
          <span className="add mono">+{commit.add}</span><span className="del mono">−{commit.del}</span>
        </div>
      </div>
      <div className="diff-body">
        {window.PH.DIFF_FILES.map((f) => (
          <div key={f.path} className="diff-file">
            <div className="diff-file-head">
              <Icon name="chevron-down" size={14} style={{ color: "var(--text-muted)" }} />
              <span className="diff-status">{f.status}</span>
              <span className="mono diff-path">{f.path}</span>
              <span className="add mono" style={{ marginLeft: "auto", fontSize: 11 }}>+{f.add}</span>
              <span className="del mono" style={{ fontSize: 11 }}>−{f.del}</span>
            </div>
            {f.hunks.map((h, hi) => (
              <div key={hi} className="diff-hunk">
                <div className="diff-hunk-head mono">{h.header}</div>
                {h.lines.map((ln, li) => (
                  <div key={li} className={`diff-line ${ln.type}`}>
                    <span className="diff-ln mono">{ln.o ?? ""}</span>
                    <span className="diff-ln mono">{ln.n ?? ""}</span>
                    <span className="diff-sign mono">{ln.type === "add" ? "+" : ln.type === "del" ? "−" : " "}</span>
                    <span className="diff-code mono">{ln.t}</span>
                  </div>
                ))}
              </div>
            ))}
          </div>
        ))}
      </div>
    </aside>
  );
}

function BranchGraphPage() {
  const commits = window.PH.COMMITS;
  const ranges = laneRanges(commits);
  const maxLane = Math.max(...commits.map((c) => c.lane));
  const [selected, setSelected] = React.useState("6927b8de");
  const [branch, setBranch] = React.useState("All");
  const selCommit = commits.find((c) => c.sha === selected);
  return (
    <div className={`bg-wrap ${selCommit ? "" : "no-diff"}`}>
      <aside className="bg-sidebar">
        <div className="eyebrow" style={{ padding: "4px 6px 10px" }}>Branches</div>
        {window.PH.BRANCHES.map((b) => (
          <div key={b.name} className={`bg-branch ${branch === b.name ? "sel" : ""}`} onClick={() => setBranch(b.name)}>
            <i className="dot" style={{ background: b.all ? "var(--text-muted)" : window.PH.LANES[b.lane] }} />
            <span style={{ flex: 1 }}>{b.name}</span>
            {b.head && <span className="head-tag mono">HEAD</span>}
            <span className="mono" style={{ fontSize: 11, color: "var(--text-muted)" }}>{b.count}</span>
          </div>
        ))}
        <div className="bg-toolbar">
          <label className="bg-toggle"><span className="checkbox on"><Icon name="check" size={12} stroke={3} /></span>Ticketed only</label>
        </div>
      </aside>

      <div className="bg-list">
        <div className={`bg-list-head ${selCommit ? "compact" : ""}`}>
          <span style={{ width: GUT_PAD + maxLane * LANE_W + GUT_PAD }} />
          <span className="bg-sha eyebrow">SHA</span>
          <span className="bg-msg eyebrow">Message</span>
          <span className="bg-author eyebrow">Author</span>
          <span className="bg-time eyebrow">Time</span>
        </div>
        <div className="bg-rows">
          {commits.map((c, i) => (
            <CommitRow key={c.sha} commit={c} i={i} ranges={ranges} maxLane={maxLane} selected={selected === c.sha} compact={!!selCommit} onSelect={() => setSelected(c.sha)} />
          ))}
        </div>
      </div>

      {selCommit && <DiffPanel commit={selCommit} onClose={() => setSelected(null)} />}
    </div>
  );
}

Object.assign(window, { BranchGraphPage });
