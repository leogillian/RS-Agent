/**
 * MessageBubble — 单条消息气泡（用户 / 助手），含 Trace / Markdown / 图片。
 */
import React, { useCallback } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeRaw from "rehype-raw";
import type { Components } from "react-markdown";
import type { Message } from "./types";
import { TracePanel } from "./TracePanel";

interface MessageBubbleProps {
  message: Message;
  mdComponents: Components;
  formatTime: (iso?: string) => string;
  onImageClick: (url: string) => void;
}

export const MessageBubble: React.FC<MessageBubbleProps> = React.memo(
  ({ message: m, mdComponents, formatTime, onImageClick }) => {
    return (
      <div className={`msg msg-${m.role}`}>
        <div className="msg-meta">
          <span className="msg-role">{m.role === "user" ? "你" : "助手"}</span>
          <span className="msg-time">{formatTime(m.createdAt)}</span>
        </div>
        {m.role === "assistant" ? (
          <div className="msg-content">
            <TracePanel message={m} />
            <div className="msg-markdown">
              <ReactMarkdown
                remarkPlugins={[remarkGfm]}
                rehypePlugins={[rehypeRaw]}
                components={mdComponents}
              >
                {m.text || (m.streaming ? "正在思考中…" : "")}
              </ReactMarkdown>
            </div>
            {m.promptToUser && (
              <div className="msg-prompt">{m.promptToUser}</div>
            )}
            {m.images && m.images.length > 0 && (
              <div className="msg-images">
                {m.images.map((url, i) => (
                  <img
                    key={i}
                    src={url}
                    alt={`检索图 ${i + 1}`}
                    className="msg-img"
                    onClick={() => onImageClick(url)}
                    role="button"
                  />
                ))}
              </div>
            )}
          </div>
        ) : (
          <div className="msg-content">
            <pre className="msg-text">{m.text}</pre>
          </div>
        )}
      </div>
    );
  }
);

MessageBubble.displayName = "MessageBubble";
