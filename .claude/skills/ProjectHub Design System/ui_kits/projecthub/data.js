/* global window */
// ProjectHub UI kit — mock data + small helpers.
// Domain facts mirror the real product (states, roles, ticket keys, agents).

const STATES = [
  { id: "backlog",     label: "Backlog",     color: "var(--state-backlog)" },
  { id: "to_do",       label: "To Do",       color: "var(--state-to_do)" },
  { id: "in_progress", label: "In Progress", color: "var(--state-in_progress)" },
  { id: "blocked",     label: "Blocked",     color: "var(--state-blocked)" },
  { id: "in_review",   label: "In Review",   color: "var(--state-in_review)" },
  { id: "in_test",     label: "In Test",     color: "var(--state-in_test)" },
  { id: "done",        label: "Done",        color: "var(--state-done)" },
];

const ROLE_COLOR = {
  admin: "var(--role-admin)", pm: "var(--role-pm)", architect: "var(--role-architect)",
  backend_dev: "var(--role-backend)", frontend_dev: "var(--role-frontend)",
  reviewer: "var(--role-reviewer)", qa: "var(--role-qa)", orchestrator: "var(--role-orchestrator)",
};

const TYPE_COLOR = {
  feature: "var(--lane-emerald)", bug: "var(--lane-rose)",
  task: "var(--lane-sky)", epic: "var(--lane-violet)",
};

const PRIORITY_COLOR = {
  low: "var(--text-muted)", medium: "var(--info)", high: "var(--warning)", urgent: "var(--danger)",
};

const BOARDS = [
  { key: "PH",    name: "ProjectHub",        desc: "Default ProjectHub board",   type: "web_app", states: 7, live: true,  open: 5 },
  { key: "BENCH", name: "Jarwis Bench",      desc: "Agent benchmark harness",    type: "web_app", states: 7, live: false, open: 12 },
  { key: "FN",    name: "Fruit Ninja 2",     desc: "Mobile game sequel",         type: "web_app", states: 7, live: true,  open: 8 },
  { key: "GXA",   name: "GameX Android",     desc: "Android client",             type: "web_app", states: 7, live: false, open: 3 },
  { key: "GXI",   name: "GameX iOS",         desc: "iOS client",                 type: "web_app", states: 7, live: false, open: 6 },
  { key: "KIM",   name: "Kims",              desc: "Internal CRM",               type: "web_app", states: 7, live: true,  open: 1 },
  { key: "SMK",   name: "Smoke Test Project", desc: "E2E smoke suite",           type: "web_app", states: 7, live: false, open: 0 },
];

const TICKETS = [
  { key: "PH-167", type: "task",    title: "Branch graph UX rework — SourceTree-style list + commit→diff + remove demo link", state: "backlog", priority: "high",   assignee: "jarwis-pm",       updated: "6m",  labels: ["git-integration", "phase:frontend", "rework"], phase: { agent: "jarwis-frontend", phase: "coding" } },
  { key: "PH-166", type: "bug",     title: "Fix webhook-after-sync double-write of git_commit_linked", state: "backlog", priority: "low",    assignee: "jarwis-backend",  updated: "6h",  labels: ["git-integration", "tech-debt"] },
  { key: "PH-165", type: "task",    title: "Git integration cleanup batch (G14 deferred)", state: "backlog", priority: "medium", assignee: "jarwis-architect", updated: "6h", labels: ["git-integration", "cleanup"] },
  { key: "PH-104", type: "epic",    title: "Workflow editor regression e2e suite for PH-96 epic", state: "backlog", priority: "medium", assignee: "jarwis-qa",       updated: "13d", labels: ["workflow", "e2e"] },
  { key: "PH-158", type: "feature", title: "Live agent-phase indicator on ticket cards", state: "backlog", priority: "high", assignee: "jarwis-frontend", updated: "2d", labels: ["ux", "live"] },
  { key: "PH-170", type: "feature", title: "⌘K command menu — global ticket + commit search", state: "to_do", priority: "medium", assignee: "jarwis-frontend", updated: "1h", labels: ["ux"] },
  { key: "PH-171", type: "task",    title: "Permissions matrix sticky first column", state: "to_do", priority: "low", assignee: "jarwis-frontend", updated: "3h", labels: ["settings"] },
  { key: "PH-169", type: "bug",     title: "Diff panel scroll jump on commit select", state: "in_progress", priority: "high", assignee: "jarwis-frontend", updated: "12m", labels: ["git-integration"], phase: { agent: "jarwis-frontend", phase: "coding" } },
  { key: "PH-162", type: "feature", title: "Board settings — workflow node editor", state: "in_review", priority: "medium", assignee: "jarwis-architect", updated: "7h", labels: ["settings", "workflow"] },
  { key: "PH-161", type: "task",    title: "Ticket-level live commits + diffs", state: "in_test", priority: "medium", assignee: "jarwis-qa", updated: "8h", labels: ["git-integration"], phase: { agent: "jarwis-qa", phase: "verifying" } },
  { key: "PH-149", type: "task",    title: "Switch all jarwis agents to opus-4-8", state: "done", priority: "low", assignee: "jarwis-pilot", updated: "37m", labels: ["agents"] },
  { key: "PH-163", type: "feature", title: "G14 a11y polish + permissions matrix", state: "done", priority: "medium", assignee: "jarwis-reviewer", updated: "6h", labels: ["a11y"] },
];

const MEMBERS = [
  { actor: "Admin",            role: "admin",        type: "Human", perms: 1 },
  { actor: "jarwis-pilot",     role: "admin",        type: "Agent", perms: 1 },
  { actor: "jarwis-pm",        role: "pm",           type: "Agent", perms: 8 },
  { actor: "jarwis-architect", role: "architect",    type: "Agent", perms: 7 },
  { actor: "jarwis-qa",        role: "qa",           type: "Agent", perms: 10 },
  { actor: "jarwis-backend",   role: "backend_dev",  type: "Agent", perms: 7 },
  { actor: "jarwis-frontend",  role: "frontend_dev", type: "Agent", perms: 7 },
  { actor: "jarwis-reviewer",  role: "reviewer",     type: "Agent", perms: 6 },
];

const TRANSITIONS = [
  { from: "backlog", to: "to_do", roles: [] },
  { from: "to_do", to: "in_progress", roles: ["pm"] },
  { from: "in_progress", to: "blocked", roles: ["pm"] },
  { from: "blocked", to: "in_progress", roles: ["pm"] },
  { from: "in_progress", to: "in_review", roles: ["pm"] },
  { from: "in_review", to: "in_progress", roles: ["pm", "reviewer"] },
  { from: "in_review", to: "in_test", roles: ["pm", "qa"] },
  { from: "in_test", to: "in_progress", roles: ["pm", "qa"] },
  { from: "*", to: "done", roles: ["pm", "admin"] },
  { from: "in_test", to: "done", roles: ["pm", "qa", "admin"] },
];
const MATRIX_ROLES = ["pm", "qa", "admin", "reviewer", "architect", "backend_dev", "frontend_dev", "orchestrator"];

// Branch-graph commits. lane index → lane palette color. firstParentLane for merge curves.
const LANES = ["var(--lane-cyan)", "var(--lane-emerald)", "var(--lane-amber)", "var(--lane-rose)", "var(--lane-violet)", "var(--lane-sky)"];
const COMMITS = [
  { sha: "6927b8de", lane: 0, merge: true,  msg: "Merge PH-167: Branch graph UX rework (SourceTree-style)", refs: ["main"], ticket: "PH-167", author: "Hüseyin Kanat", time: "45s ago", fresh: true,  files: 17, add: 1020, del: 306 },
  { sha: "a89af24e", lane: 1, msg: "feat(PH-167): rework Branch Graph to SourceTree vertical list", ticket: "PH-167", author: "Hüseyin Kanat", time: "11m ago", files: 12, add: 540, del: 120 },
  { sha: "bdabf6a6", lane: 1, msg: "test(PH-167): live graph top-insert verify", ticket: "PH-167", author: "Hüseyin Kanat", time: "17m ago", files: 3, add: 88, del: 4 },
  { sha: "a67a20c6", lane: 1, mergeInto: 0, msg: "chore(PH-149): revert all jarwis agent model bumps", ticket: "PH-149", author: "Hüseyin Kanat", time: "37m ago", files: 6, add: 12, del: 12 },
  { sha: "aa80997a", lane: 0, msg: "chore(PH-149): switch all jarwis agents to opus-4-8", ticket: "PH-149", author: "Hüseyin Kanat", time: "50m ago", files: 6, add: 6, del: 6 },
  { sha: "8200381b", lane: 0, merge: true, msg: "Merge PH-163: G14 a11y polish + permissions matrix", refs: [], ticket: "PH-163", author: "Hüseyin Kanat", time: "6h ago", files: 9, add: 210, del: 40 },
  { sha: "7c7e006d", lane: 2, msg: "test(PH-161): g12 qa live verify", ticket: "PH-161", author: "Hüseyin Kanat", time: "6h ago", files: 2, add: 30, del: 2 },
  { sha: "00427c2c", lane: 2, msg: "test(PH-159): qa graph live", ticket: "PH-159", author: "Hüseyin Kanat", time: "6h ago", files: 4, add: 60, del: 8 },
  { sha: "000219a5", lane: 2, mergeInto: 0, msg: "feat(PH-163): G14 a11y polish + done states", ticket: "PH-163", author: "Hüseyin Kanat", time: "6h ago", files: 7, add: 140, del: 24 },
  { sha: "b9f6ccd7", lane: 0, merge: true, msg: "Merge PH-162: G13 board settings workflow editor", refs: [], ticket: "PH-162", author: "Hüseyin Kanat", time: "7h ago", files: 14, add: 420, del: 96 },
  { sha: "e184424c", lane: 3, msg: "test(PH-162): mode B verify — board settings", ticket: "PH-162", author: "Hüseyin Kanat", time: "7h ago", files: 3, add: 70, del: 6 },
  { sha: "1288523b", lane: 3, msg: "feat(PH-162): frontend F1-F11 board settings", ticket: "PH-162", author: "Hüseyin Kanat", time: "7h ago", files: 8, add: 260, del: 50 },
  { sha: "6797e2fe", lane: 3, mergeInto: 0, msg: "feat(PH-162): bearer alt-auth on /git endpoints", ticket: "PH-162", author: "Hüseyin Kanat", time: "7h ago", files: 5, add: 90, del: 40 },
  { sha: "a888338c", lane: 0, merge: true, msg: "Merge PH-161: G12 ticket commits + diffs", refs: [], ticket: "PH-161", author: "Hüseyin Kanat", time: "8h ago", files: 11, add: 300, del: 70 },
  { sha: "0d5ff457", lane: 5, msg: "test(PH-161): g12 qa live verify", ticket: "PH-161", author: "Hüseyin Kanat", time: "8h ago", files: 2, add: 24, del: 2 },
  { sha: "32f5d3d1", lane: 5, msg: "feat(PH-161): interleaved ticket timeline", ticket: "PH-161", author: "Hüseyin Kanat", time: "8h ago", files: 6, add: 180, del: 30 },
  { sha: "c6bcc6cc", lane: 5, mergeInto: 0, msg: "feat(PH-161): G12 ticket commits + diff panel", ticket: "PH-161", author: "Hüseyin Kanat", time: "8h ago", files: 9, add: 240, del: 44 },
];

const BRANCHES = [
  { name: "All", lane: -1, count: 17, all: true },
  { name: "main", lane: 0, head: true, count: 17 },
];

// Sample unified diff for the selected commit's panel.
const DIFF_FILES = [
  { path: ".claude/agents/architect.md", status: "M", add: 1, del: 1, hunks: [
    { header: "@@ -2,7 +2,7 @@", lines: [
      { type: "ctx", o: 2, n: 2, t: "name: architect" },
      { type: "ctx", o: 3, n: 3, t: "description: Software Architect" },
      { type: "ctx", o: 4, n: 4, t: "tools: Read, Glob, Write, Bash, mcp" },
      { type: "del", o: 5, t: "model: claude-sonnet-4-6" },
      { type: "add", n: 5, t: "model: claude-opus-4-8" },
      { type: "ctx", o: 6, n: 6, t: "---" },
    ]},
  ]},
  { path: ".claude/agents/backend.md", status: "M", add: 1, del: 1, hunks: [
    { header: "@@ -2,7 +2,7 @@", lines: [
      { type: "ctx", o: 2, n: 2, t: "name: backend" },
      { type: "ctx", o: 3, n: 3, t: "description: Backend Developer" },
      { type: "del", o: 4, t: "model: claude-sonnet-4-6" },
      { type: "add", n: 4, t: "model: claude-opus-4-8" },
      { type: "ctx", o: 5, n: 5, t: "---" },
    ]},
  ]},
  { path: "frontend/src/components/git/BranchGraph.tsx", status: "M", add: 540, del: 120, hunks: [
    { header: "@@ -1,12 +1,9 @@", lines: [
      { type: "del", o: 1, t: "import ReactFlow, { Node, Edge } from 'reactflow';" },
      { type: "add", n: 1, t: "// xyflow removed (PH-167) — SVG lane gutter rows" },
      { type: "ctx", o: 2, n: 2, t: "import { assignLanes, laneColor } from './branchGraphLayout';" },
      { type: "add", n: 3, t: "const ROW_H = 36; const LANE_W = 16;" },
    ]},
  ]},
];

window.PH = { STATES, ROLE_COLOR, TYPE_COLOR, PRIORITY_COLOR, BOARDS, TICKETS, MEMBERS, TRANSITIONS, MATRIX_ROLES, LANES, COMMITS, BRANCHES, DIFF_FILES };
