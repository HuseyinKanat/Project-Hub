import { ArrowRight } from "lucide-react";
import type { WorkflowState } from "@/types/api";

interface WorkflowTransition {
  from: string;
  to: string;
  allowed_roles?: string[];
}

interface WorkflowTransitionGraphProps {
  states: WorkflowState[];
  transitions: WorkflowTransition[];
}

export function WorkflowTransitionGraph({ states, transitions }: WorkflowTransitionGraphProps) {
  if (states.length === 0) {
    return (
      <div className="rounded-lg border border-dashed border-slate-300 bg-slate-50 p-8 text-center">
        <p className="text-slate-500">No states to display transitions</p>
      </div>
    );
  }

  // Build adjacency list
  const transitionMap = new Map<string, string[]>();
  transitions.forEach(({ from, to }) => {
    if (!transitionMap.has(from)) {
      transitionMap.set(from, []);
    }
    transitionMap.get(from)!.push(to);
  });

  return (
    <div className="overflow-x-auto">
      <div className="min-w-[600px]">
        {/* States as columns */}
        <div className="flex items-start gap-4">
          {states.map((state) => {
            const outgoing = transitionMap.get(state.name) || [];
            const hasTransitions = outgoing.length > 0;

            return (
              <div
                key={state.name}
                className="flex flex-1 flex-col items-center"
              >
                {/* State box */}
                <div
                  className="w-full rounded-lg border-2 p-3 text-center"
                  style={{ borderColor: state.color || "#94a3b8" }}
                >
                  <div
                    className="mx-auto mb-2 h-4 w-4 rounded-full"
                    style={{ backgroundColor: state.color || "#94a3b8" }}
                  />
                  <span className="block text-sm font-medium text-slate-900">
                    {state.name}
                  </span>
                  {state.is_initial && (
                    <span className="mt-1 block text-[10px] text-green-600">Initial</span>
                  )}
                  {state.is_terminal && (
                    <span className="mt-1 block text-[10px] text-slate-500">Terminal</span>
                  )}
                </div>

                {/* Transitions from this state */}
                {hasTransitions && (
                  <div className="mt-3 flex flex-col items-center gap-1">
                    <ArrowRight className="h-4 w-4 rotate-90 text-slate-400" />
                    <div className="flex flex-col items-center gap-1">
                      {outgoing.map((targetName) => (
                        <div
                          key={`${state.name}->${targetName}`}
                          className="rounded bg-slate-100 px-2 py-0.5 text-[10px] text-slate-600"
                        >
                          → {targetName}
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {/* No transitions indicator */}
                {!hasTransitions && !state.is_terminal && (
                  <div className="mt-2 text-[10px] text-slate-400">No outgoing</div>
                )}
              </div>
            );
          })}
        </div>

        {/* Legend */}
        <div className="mt-6 flex flex-wrap items-center gap-4 border-t border-slate-200 pt-4 text-xs text-slate-500">
          <div className="flex items-center gap-1">
            <div className="h-3 w-3 rounded-full bg-green-500" />
            <span>Initial state</span>
          </div>
          <div className="flex items-center gap-1">
            <div className="h-3 w-3 rounded-full border-2 border-slate-400 bg-white" />
            <span>Terminal state</span>
          </div>
          <div className="flex items-center gap-1">
            <ArrowRight className="h-3 w-3" />
            <span>Allowed transition</span>
          </div>
        </div>
      </div>
    </div>
  );
}
