/**
 * components/git barrel export — PH-159 (G10), PH-160 (G11), PH-161 (G12)
 * PH-167: removed xyflow-specific exports (buildNodesAndEdges, COL_W, CommitNodeData);
 *          BranchGraph is now a self-contained 3-pane SourceTree-style component.
 * PH-165: removed dead exports — BranchPanel, BranchLegend, CommitNode (replaced by the
 *          PH-167 SourceTree rework; BranchGraph renders its own sidebar + diff panel)
 *          and the unused branchGraphLayout re-exports (consumers import it directly).
 * PH-224: added RepoSwitcher (branch-view multi-repo selector).
 */

export { BranchGraph } from "./BranchGraph";
export { RepoSwitcher } from "./RepoSwitcher";
export { TicketCommits } from "./TicketCommits";
