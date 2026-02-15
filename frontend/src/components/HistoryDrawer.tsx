/**
 * HistoryDrawer — 历史会话抽屉侧栏。
 */
import React from "react";
import type { ConversationSummary } from "../api";

interface HistoryDrawerProps {
  open: boolean;
  conversations: ConversationSummary[];
  currentConversationId: string | null;
  onClose: () => void;
  onSelect: (id: string) => void;
}

export const HistoryDrawer: React.FC<HistoryDrawerProps> = React.memo(
  ({ open, conversations, currentConversationId, onClose, onSelect }) => {
    if (!open) return null;
    return (
      <>
        <div
          className="history-drawer-backdrop"
          onClick={onClose}
          aria-hidden
        />
        <div className="history-drawer history-drawer-open" role="dialog" aria-label="历史会话">
          <div className="drawer-header">
            <span>历史会话</span>
            <button
              type="button"
              className="drawer-close"
              onClick={onClose}
              aria-label="关闭"
            >
              ×
            </button>
          </div>
          <div className="sidebar-list">
            {conversations.map((c) => (
              <button
                key={c.id}
                className={
                  "sidebar-item" +
                  (currentConversationId === c.id ? " sidebar-item-active" : "")
                }
                onClick={() => onSelect(c.id)}
              >
                <div className="sidebar-item-title">
                  {c.first_user_text || (c.intent === "KB_QUERY" ? "知识库查询" : "需求分析")}
                </div>
                <div className="sidebar-item-meta">
                  {c.status} · {new Date(c.created_at).toLocaleString()}
                </div>
              </button>
            ))}
            {conversations.length === 0 && (
              <div className="sidebar-empty">暂无历史会话</div>
            )}
          </div>
        </div>
      </>
    );
  }
);

HistoryDrawer.displayName = "HistoryDrawer";
