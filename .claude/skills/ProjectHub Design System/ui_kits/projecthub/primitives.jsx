/* global React */
// ProjectHub UI kit — primitives. Exports to window for cross-file use.
const { useState, useEffect, useRef } = React;

// ---- Lucide icon (React-safe: span owned by React, SVG injected manually) ----
function Icon({ name, size = 16, stroke = 2, className = "", style = {} }) {
  const ref = useRef(null);
  useEffect(() => {
    const host = ref.current;
    if (!host || !window.lucide) return;
    const pascal = name.split("-").map((s) => s[0].toUpperCase() + s.slice(1)).join("");
    const node = window.lucide.icons?.[pascal];
    host.innerHTML = "";
    if (node && window.lucide.createElement) {
      const svg = window.lucide.createElement(node);
      svg.setAttribute("width", size);
      svg.setAttribute("height", size);
      svg.setAttribute("stroke-width", stroke);
      host.appendChild(svg);
    }
  }, [name, size, stroke]);
  return <span ref={ref} className={className} style={{ display: "inline-flex", width: size, height: size, ...style }} />;
}

function Button({ variant = "secondary", size, icon, children, className = "", ...rest }) {
  return (
    <button className={`btn btn-${variant} ${size === "sm" ? "btn-sm" : ""} ${className}`} {...rest}>
      {icon && <Icon name={icon} size={size === "sm" ? 14 : 15} />}
      {children}
    </button>
  );
}

const KeyChip = ({ children }) => <span className="key-chip">{children}</span>;

function TypeChip({ type }) {
  const c = window.PH.TYPE_COLOR[type] || "var(--text-muted)";
  return <span className="type-chip" style={{ color: c, background: `color-mix(in srgb, ${c} 16%, transparent)` }}>{type}</span>;
}

function RoleChip({ role }) {
  const c = window.PH.ROLE_COLOR[role] || "var(--text-muted)";
  return <span className="role-chip" style={{ color: c, background: `color-mix(in srgb, ${c} 14%, transparent)` }}>{role}</span>;
}

const LabelChip = ({ children }) => <span className="label-chip">{children}</span>;

function Priority({ level }) {
  const c = window.PH.PRIORITY_COLOR[level] || "var(--text-muted)";
  return <span className="priority"><i className="dot" style={{ background: c }} />{level}</span>;
}

function StatusPill({ status }) {
  const map = {
    Live: ["var(--success)", "var(--success-soft)", true],
    Connecting: ["var(--warning)", "var(--warning-soft)", true],
    Off: ["var(--danger)", "var(--danger-soft)", false],
  };
  const [c, bg, pulse] = map[status] || map.Off;
  return (
    <span className="status-pill" style={{ color: c, background: bg, border: `1px solid color-mix(in srgb, ${c} 30%, transparent)` }}>
      <i className={`dot dot-sm ${pulse ? "pulse" : ""}`} style={{ background: c }} />{status}
    </span>
  );
}

function AgentPhase({ agent, phase }) {
  return <span className="t-phase"><i className="dot dot-sm pulse" style={{ background: "var(--accent)" }} />{agent} · {phase}</span>;
}

function Avatar({ name, size = "" }) {
  const initials = name.replace(/^jarwis-/, "").slice(0, 2).toUpperCase();
  return <span className={`avatar ${size}`} title={name}>{initials}</span>;
}

function StatePill({ state }) {
  const s = window.PH.STATES.find((x) => x.id === state) || { color: "var(--text-muted)", id: state };
  return (
    <span className="status-pill" style={{ fontFamily: "var(--font-mono)", color: s.color, background: `color-mix(in srgb, ${s.color} 12%, transparent)`, border: `1px solid color-mix(in srgb, ${s.color} 34%, transparent)` }}>
      <i className="dot dot-sm" style={{ background: s.color }} />{state}
    </span>
  );
}

function Tabs({ tabs, active, onChange }) {
  return (
    <div className="tabs">
      {tabs.map((t) => {
        const id = typeof t === "string" ? t : t.id;
        const label = typeof t === "string" ? t : t.label;
        const count = typeof t === "object" ? t.count : undefined;
        const icon = typeof t === "object" ? t.icon : undefined;
        return (
          <button key={id} className={`tab ${active === id ? "active" : ""}`} onClick={() => onChange(id)}>
            {icon && <Icon name={icon} size={15} />}
            {label}
            {count !== undefined && <span className="tab-count">{count}</span>}
            {active === id && <span className="underline" />}
          </button>
        );
      })}
    </div>
  );
}

const Checkbox = ({ on, onClick }) => (
  <span className={`checkbox ${on ? "on" : ""}`} onClick={onClick}>{on && <Icon name="check" size={13} stroke={3} />}</span>
);

Object.assign(window, { Icon, Button, KeyChip, TypeChip, RoleChip, LabelChip, Priority, StatusPill, AgentPhase, Avatar, StatePill, Tabs, Checkbox });
