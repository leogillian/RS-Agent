/**
 * ChatPanel — 聊天区域：消息列表 + 滚动控制 + 底部输入栏。
 */
import React, { useEffect, useMemo, useRef, useState } from "react";
import type { Components } from "react-markdown";
import type { Message } from "./types";
import { MessageBubble } from "./MessageBubble";
import { InputArea } from "./InputArea";

interface ChatPanelProps {
  messages: Message[];
  mdComponents: Components;
  formatTime: (iso?: string) => string;
  error: string | null;
  input: string;
  loading: boolean;
  abortable: boolean;
  stopRequested: boolean;
  selectedFiles: File[];
  onInputChange: (value: string) => void;
  onSend: () => void;
  onStop: () => void;
  onFilesChange: (files: File[]) => void;
  onImageClick: (url: string) => void;
  /** 由父组件设置，当发送新消息后触发一次滚动到底部 */
  justSent: boolean;
  onJustSentConsumed: () => void;
}

export const ChatPanel: React.FC<ChatPanelProps> = ({
  messages,
  mdComponents,
  formatTime,
  error,
  input,
  loading,
  abortable,
  stopRequested,
  selectedFiles,
  onInputChange,
  onSend,
  onStop,
  onFilesChange,
  onImageClick,
  justSent,
  onJustSentConsumed,
}) => {
  const messagesRef = useRef<HTMLDivElement | null>(null);
  const messagesEndRef = useRef<HTMLDivElement | null>(null);
  const atBottomRef = useRef(true);
  const [showJumpToBottom, setShowJumpToBottom] = useState(false);
  const [userScrolledUp, setUserScrolledUp] = useState(false);

  const scrollToBottom = (behavior: ScrollBehavior = "smooth") => {
    messagesEndRef.current?.scrollIntoView({ behavior });
  };

  const recomputeAtBottom = () => {
    const el = messagesRef.current;
    if (!el) return;
    const distance = el.scrollHeight - el.scrollTop - el.clientHeight;
    const atBottom = distance < 80;
    atBottomRef.current = atBottom;
    setUserScrolledUp(!atBottom);
    if (atBottom) setShowJumpToBottom(false);
  };

  useEffect(() => {
    if (justSent) {
      onJustSentConsumed();
      requestAnimationFrame(() => scrollToBottom("smooth"));
      return;
    }
    if (atBottomRef.current) requestAnimationFrame(() => scrollToBottom("smooth"));
    else setShowJumpToBottom(true);
  }, [messages]);

  useEffect(() => {
    requestAnimationFrame(() => recomputeAtBottom());
  }, [messages.length]);

  const renderedMessages = useMemo(() => {
    return messages.map((m) => (
      <MessageBubble
        key={m.id}
        message={m}
        mdComponents={mdComponents}
        formatTime={formatTime}
        onImageClick={onImageClick}
      />
    ));
  }, [messages, formatTime, mdComponents, onImageClick]);

  return (
    <section className="chat-panel">
      <div className="messages" ref={messagesRef} onScroll={recomputeAtBottom}>
        {renderedMessages}
        {messages.length === 0 && (
          <div className="placeholder">业务查询输入【查询知识库】，需求分析输入【系统改动点】</div>
        )}
        <div ref={messagesEndRef} />
      </div>
      {showJumpToBottom && (
        <button
          type="button"
          className="jump-to-bottom"
          onClick={() => {
            scrollToBottom("smooth");
            setShowJumpToBottom(false);
            atBottomRef.current = true;
            setUserScrolledUp(false);
          }}
          title="回到底部"
          aria-label="回到底部"
        >
          {userScrolledUp ? "有新消息 · 回到底部" : "回到底部"}
        </button>
      )}
      {error && <div className="error">错误：{error}</div>}
      <InputArea
        input={input}
        loading={loading}
        abortable={abortable}
        stopRequested={stopRequested}
        selectedFiles={selectedFiles}
        onInputChange={onInputChange}
        onSend={onSend}
        onStop={onStop}
        onFilesChange={onFilesChange}
      />
    </section>
  );
};
