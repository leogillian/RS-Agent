/**
 * TracePanel — 展示执行轨迹（思考过程）的折叠面板。
 */
import React from "react";
import type { Message } from "./types";

interface TracePanelProps {
  message: Message;
}

export const TracePanel: React.FC<TracePanelProps> = React.memo(({ message }) => {
  const steps = message.traceSteps || [];
  if (!message.streaming && steps.length === 0) return null;
  return (
    <details className="trace-panel">
      <summary>
        思考过程（{steps.length}）{message.streaming ? " · 进行中" : ""}
      </summary>
      <div className="trace-list">
        {steps.length === 0 ? (
          <div className="trace-empty">正在生成执行轨迹…</div>
        ) : (
          steps.map((s, idx) => (
            <div key={idx} className={`trace-item trace-${String(s.level || "info")}`}>
              <span className="trace-ts">{s.ts}</span>
              <span className="trace-phase">{String(s.phase || "")}</span>
              <span className="trace-title">{s.title}</span>
              {s.detail ? <span className="trace-detail">{s.detail}</span> : null}
            </div>
          ))
        )}
      </div>
    </details>
  );
});

TracePanel.displayName = "TracePanel";
