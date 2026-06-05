/**
 * components/git barrel export — PH-159 (G10), PH-160 (G11), PH-161 (G12)
 * PH-167: removed xyflow-specific exports (buildNodesAndEdges, COL_W, CommitNodeData)
 *          BranchGraph is now a self-contained 3-pane SourceTree-style component.
 */

export { BranchGraph } from "./BranchGraph";
export { BranchPanel } from "./BranchPanel";
export { BranchLegend } from "./BranchLegend";
export { CommitNode } from "./CommitNode";
export { TicketCommits } from "./TicketCommits";
export {
  assignLanes,
  laneColor,
  computeMaxLane,
  LANE_W,
  ROW_H,
} from "./branchGraphLayout";
