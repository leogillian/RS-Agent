import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeRaw from "rehype-raw";
import type { Components } from "react-markdown";
import { MermaidBlock } from "./MermaidBlock";
import type {
  AgentResponse,
  ConversationDetail,
  ConversationSummary,
  PayloadType,
  TraceStep
} from "./api";
import { callAgent, callAgentStream, fetchConversationDetail, fetchConversations, uploadImages } from "./api";

type Role = "user" | "assistant";

interface Message {
  id: number;
  role: Role;
  text: string;
  payloadType?: PayloadType;
  images?: string[];
  promptToUser?: string;
  traceSteps?: TraceStep[];
  streaming?: boolean;
  createdAt?: string; // ISO 或后端返回的 created_at，用于展示时间
}

function getHeadingText(children: React.ReactNode) {
  const parts: string[] = [];
  React.Children.forEach(children, (child) => {
    if (typeof child === "string") parts.push(child);
    else if (Array.isArray(child)) parts.push(child.join(""));
  });
  return parts.join("").trim();
}

export const App: React.FC = () => {
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [currentConversationId, setCurrentConversationId] = useState<string | null>(null);
  const [input, setInput] = useState("");
  const [messages, setMessages] = useState<Message[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [loadingConvs, setLoadingConvs] = useState(false);
  const [selectedFiles, setSelectedFiles] = useState<File[]>([]);
  const [lightboxUrl, setLightboxUrl] = useState<string | null>(null);
  const [mermaidLightboxSvg, setMermaidLightboxSvg] = useState<string | null>(null);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [showJumpToBottom, setShowJumpToBottom] = useState(false);
  const [userScrolledUp, setUserScrolledUp] = useState(false);
  const [abortable, setAbortable] = useState(false);
  const [stopRequested, setStopRequested] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const messagesRef = useRef<HTMLDivElement | null>(null);
  const messagesEndRef = useRef<HTMLDivElement | null>(null);
  const msgIdRef = useRef(1);
  const atBottomRef = useRef(true);
  const justSentRef = useRef(false);

  const appendMessage = (msg: Omit<Message, "id">) => {
    const id = msgIdRef.current++;
    setMessages((prev) => [
      ...prev,
      {
        ...msg,
        id,
        createdAt: msg.createdAt ?? new Date().toISOString(),
      },
    ]);
    return id;
  };

  const updateMessageById = (id: number, patch: Partial<Message>) => {
    setMessages((prev) => prev.map((m) => (m.id === id ? { ...m, ...patch } : m)));
  };

  const loadConversations = async () => {
    try {
      setLoadingConvs(true);
      const data = await fetchConversations(10, 0);
      setConversations(data);
    } catch (e: any) {
      console.error("加载会话列表失败", e);
    } finally {
      setLoadingConvs(false);
    }
  };

  useEffect(() => {
    void loadConversations();
  }, []);

  useEffect(() => {
    if (!lightboxUrl && !mermaidLightboxSvg) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setLightboxUrl(null);
        setMermaidLightboxSvg(null);
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [lightboxUrl, mermaidLightboxSvg]);

  const loadConversationDetail = async (convId: string) => {
    try {
      // 切换会话时中断当前生成，避免后台仍在写入导致 UI 混乱
      abortRef.current?.abort();
      const detail: ConversationDetail = await fetchConversationDetail(convId);
      setCurrentConversationId(convId);
      setHistoryOpen(false);
      let pendingTrace: TraceStep[] | null = null;
      const msgs: Message[] = [];
      for (const m of detail.messages) {
        const payloadType = (m.payload_type as PayloadType) || undefined;
        if (payloadType === "TRACE") {
          try {
            const parsed = JSON.parse(m.content);
            if (Array.isArray(parsed)) {
              pendingTrace = parsed as TraceStep[];
            }
          } catch {
            // ignore
          }
          continue;
        }
        const msg: Message = {
          id: msgs.length + 1,
          role: m.role === "user" ? "user" : "assistant",
          text: m.content,
          payloadType,
          createdAt: m.created_at,
        };
        if (msg.role === "assistant" && pendingTrace) {
          msg.traceSteps = pendingTrace;
          pendingTrace = null;
        }
        msgs.push(msg);
      }
      setMessages(msgs);
      msgIdRef.current = msgs.length + 1;
      setSessionId(detail.intent === "ORCH_FLOW" ? detail.id : null);
      // 加载历史后滚到底部，并重算位置
      requestAnimationFrame(() => {
        scrollToBottom("auto");
        recomputeAtBottom();
      });
    } catch (e: any) {
      setError(e?.message || String(e));
    }
  };

  const handleNewConversation = () => {
    abortRef.current?.abort();
    setCurrentConversationId(null);
    setSessionId(null);
    setMessages([]);
    msgIdRef.current = 1;
    setInput("");
    setError(null);
    setSelectedFiles([]);
    setHistoryOpen(false);
    void loadConversations();
    requestAnimationFrame(() => {
      scrollToBottom("auto");
      recomputeAtBottom();
    });
  };

  const formatMessageTime = useCallback((iso?: string) => {
    if (!iso) return "";
    try {
      const d = new Date(iso);
      return d.toLocaleTimeString("zh-CN", { hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit" });
    } catch {
      return "";
    }
  }, []);

  const handleSend = async () => {
    const text = input.trim();
    if (!text || loading) return;
    setError(null);
    setInput("");
    appendMessage({ role: "user", text });
    justSentRef.current = true;
    setStopRequested(false);

    setLoading(true);
    try {
      let imageIds: string[] = [];
      if (selectedFiles.length > 0) {
        imageIds = await uploadImages(selectedFiles);
        setSelectedFiles([]);
      }
      // 先插入占位助手消息，用于流式更新 trace / final
      const placeholderId = appendMessage({
        role: "assistant",
        text: "",
        payloadType: "INFO",
        traceSteps: [],
        streaming: true,
      });

      const ctrl = new AbortController();
      abortRef.current = ctrl;
      setAbortable(true);

      const finalizeAssistantMessage = (resp: AgentResponse) => {
        if (resp.sessionId !== undefined && resp.sessionId !== null) {
          setSessionId(resp.sessionId);
        }
        const content = resp.content || {};
        let assistantText = "";
        let assistantImages: string[] | undefined;
        let assistantPromptToUser: string | undefined;

        switch (resp.payloadType) {
          case "KB_ANSWER":
            assistantText = typeof content["markdown"] === "string" ? (content["markdown"] as string) : "[KB 查询完成]";
            assistantImages = Array.isArray(content["images"]) ? (content["images"] as string[]) : undefined;
            break;
          case "OPEN_QUESTIONS":
            if (Array.isArray(content["questions"])) {
              const qs = (content["questions"] as string[]).map((q, i) => `${i + 1}. ${q}`).join("\n");
              assistantText = `我有一些问题需要你补充说明：\n${qs}\n\n你可以一次性回答。`;
            } else {
              assistantText = "请补充说明你的需求。";
            }
            break;
          case "DRAFT":
            assistantText = typeof content["markdown"] === "string" ? (content["markdown"] as string) : "[已生成草稿]";
            assistantPromptToUser = typeof content["prompt_to_user"] === "string" ? (content["prompt_to_user"] as string) : undefined;
            break;
          case "FINAL_DOC":
            assistantText = typeof content["markdown"] === "string" ? (content["markdown"] as string) : "[已生成最终文档]";
            break;
          default:
            assistantText = typeof content["message"] === "string" ? (content["message"] as string) : "[完成]";
            break;
        }

        updateMessageById(placeholderId, {
          text: assistantText,
          payloadType: resp.payloadType,
          images: assistantImages,
          promptToUser: assistantPromptToUser,
          streaming: false,
        });
      };

      const appendTraceStep = (step: TraceStep) => {
        setMessages((prev) =>
          prev.map((m) => {
            if (m.id !== placeholderId) return m;
            const next = [...(m.traceSteps || []), step];
            return { ...m, traceSteps: next };
          })
        );
      };

      // 优先走流式接口；若出错，回退到非流式接口（保证可用）
      try {
        const resp = await callAgentStream(
          { sessionId, text, imageIds: imageIds.length > 0 ? imageIds : undefined },
          {
            onTrace: appendTraceStep,
            onFinal: finalizeAssistantMessage,
            onError: (msg) => setError(msg),
          },
          { signal: ctrl.signal }
        );
        // 部分浏览器/代理下 final 可能只在返回值可见，兜底再 finalize 一次
        finalizeAssistantMessage(resp);
      } catch (e: any) {
        if (e?.name === "AbortError" || String(e?.message || "").toLowerCase().includes("abort")) {
          const now = new Date().toISOString();
          appendTraceStep({
            ts: now,
            phase: "INTENT",
            title: "已停止生成",
            detail: "用户手动停止",
            level: "warn",
          });
          updateMessageById(placeholderId, {
            text: "已停止生成。你可以继续输入新的指令。",
            payloadType: "INFO",
            streaming: false,
          });
          return;
        }
        console.warn("流式请求失败，回退到非流式 /api/agent", e);
        const resp: AgentResponse = await callAgent({ sessionId, text, imageIds: imageIds.length > 0 ? imageIds : undefined });
        finalizeAssistantMessage(resp);
      } finally {
        abortRef.current = null;
        setAbortable(false);
        setStopRequested(false);
      }
      void loadConversations();
    } catch (e: any) {
      setError(e?.message || String(e));
    } finally {
      setLoading(false);
    }
  };

  const handleStop = () => {
    if (!abortRef.current) return;
    setStopRequested(true);
    abortRef.current?.abort();
  };

  const handleKeyDown: React.KeyboardEventHandler<HTMLTextAreaElement> = (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const mdComponents: Components = useMemo(() => {
    return {
      code({ inline, className, children, ...props }) {
        const match = /language-(\w+)/.exec(className || "");
        const lang = match?.[1];
        const code = String(children).replace(/\n$/, "");
        if (lang === "mermaid" && !inline) {
          return <MermaidBlock code={code} onExpand={setMermaidLightboxSvg} />;
        }
        return (
          <code className={className} {...props}>
            {children}
          </code>
        );
      },
      h2({ children, ...props }) {
        const text = getHeadingText(children);
        const className =
          text.includes("待澄清项记录") ? "md-heading-clarifications" :
          text.includes("风险与回滚要点") ? "md-heading-risk" : undefined;
        return (
          <h2 className={className} {...props}>
            {children}
          </h2>
        );
      },
      h3({ children, ...props }) {
        const text = getHeadingText(children);
        const className = text.includes("风险与回滚要点") ? "md-heading-risk" : undefined;
        return (
          <h3 className={className} {...props}>
            {children}
          </h3>
        );
      },
      img({ src, alt, ...props }) {
        if (!src) return <img {...props} />;
        const fullSrc =
          src.startsWith("http") || src.startsWith("data:")
            ? src
            : `${window.location.origin}${src.startsWith("/") ? "" : "/"}${src}`;
        return (
          <img
            src={fullSrc}
            alt={alt || "附图"}
            className="msg-markdown-img"
            onClick={() => setLightboxUrl(fullSrc)}
            role="button"
            title="点击查看大图"
            {...props}
          />
        );
      },
      a({ href, children, ...props }) {
        const isImage =
          href &&
          (/\.(png|jpe?g|gif|webp)$/i.test(href) || /\/api\/kb-images\/[^"?#]+\.(png|jpe?g|gif|webp)/i.test(href));
        const isPdf = href && /\.pdf$/i.test(href);
        if (isImage) {
          const fullHref = href.startsWith("http") ? href : `${window.location.origin}${href.startsWith("/") ? "" : "/"}${href}`;
          return (
            <a
              href={href}
              {...props}
              onClick={(e) => {
                e.preventDefault();
                setLightboxUrl(fullHref);
              }}
            >
              {children}
            </a>
          );
        }
        if (isPdf) {
          return <a href={href} {...props} target="_blank" rel="noopener noreferrer">{children}</a>;
        }
        return <a href={href} {...props}>{children}</a>;
      },
    } as Components;
  }, [setLightboxUrl, setMermaidLightboxSvg]);

  const scrollToBottom = (behavior: ScrollBehavior = "smooth") => {
    messagesEndRef.current?.scrollIntoView({ behavior });
  };

  const recomputeAtBottom = () => {
    const el = messagesRef.current;
    if (!el) return;
    const threshold = 80; // px
    const distance = el.scrollHeight - el.scrollTop - el.clientHeight;
    const atBottom = distance < threshold;
    atBottomRef.current = atBottom;
    setUserScrolledUp(!atBottom);
    if (atBottom) {
      setShowJumpToBottom(false);
    }
  };

  // 聪明自动滚动：仅当用户在底部附近/或刚发送消息时才滚动；否则显示“回到底部”
  useEffect(() => {
    if (justSentRef.current) {
      justSentRef.current = false;
      requestAnimationFrame(() => scrollToBottom("smooth"));
      return;
    }
    if (atBottomRef.current) {
      requestAnimationFrame(() => scrollToBottom("smooth"));
    } else {
      setShowJumpToBottom(true);
    }
  }, [messages]);

  // 容器高度/内容变化会影响 atBottom 判断，这里在每次 messages 变化后重算一次
  useEffect(() => {
    requestAnimationFrame(() => recomputeAtBottom());
  }, [messages.length]);

  // Esc 停止生成（lightbox 打开时 Esc 由 lightbox 处理）
  useEffect(() => {
    if (!abortable || lightboxUrl) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        handleStop();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [abortable, lightboxUrl]);

  const renderTracePanel = useCallback((m: Message) => {
    const steps = m.traceSteps || [];
    if (!m.streaming && steps.length === 0) return null;
    return (
      <details className="trace-panel">
        <summary>
          思考过程（{steps.length}）{m.streaming ? " · 进行中" : ""}
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
  }, []);

  const renderedMessages = useMemo(() => {
    return messages.map((m) => (
      <div key={m.id} className={`msg msg-${m.role}`}>
        <div className="msg-meta">
          <span className="msg-role">{m.role === "user" ? "你" : "助手"}</span>
          <span className="msg-time">{formatMessageTime(m.createdAt)}</span>
        </div>
        {m.role === "assistant" ? (
          <div className="msg-content">
            {renderTracePanel(m)}
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
                    onClick={() => setLightboxUrl(url)}
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
    ));
  }, [messages, formatMessageTime, renderTracePanel, mdComponents, setLightboxUrl]);

  return (
    <div className="app-root">
      {lightboxUrl && (
        <div
          className="lightbox-overlay"
          onClick={() => setLightboxUrl(null)}
          role="button"
          aria-label="关闭预览"
        >
          <button
            type="button"
            className="lightbox-close"
            onClick={(e) => {
              e.stopPropagation();
              setLightboxUrl(null);
            }}
            aria-label="关闭"
          >
            ×
          </button>
          <span className="lightbox-img-wrap" onClick={(e) => e.stopPropagation()}>
            <img src={lightboxUrl} alt="放大预览" />
          </span>
        </div>
      )}
      {mermaidLightboxSvg && (
        <div
          className="lightbox-overlay lightbox-mermaid"
          onClick={() => setMermaidLightboxSvg(null)}
          role="button"
          aria-label="关闭流程图预览"
        >
          <button
            type="button"
            className="lightbox-close"
            onClick={(e) => {
              e.stopPropagation();
              setMermaidLightboxSvg(null);
            }}
            aria-label="关闭"
          >
            ×
          </button>
          <span
            className="lightbox-mermaid-wrap"
            onClick={(e) => e.stopPropagation()}
            dangerouslySetInnerHTML={{ __html: mermaidLightboxSvg }}
          />
        </div>
      )}
      <header className="app-header">
        <div className="header-left">
          <h1>RS-Agent</h1>
          <span className="tagline">支持业务查询 & 需求分析</span>
        </div>
        <div className="header-actions">
          <button type="button" className="sidebar-new" onClick={handleNewConversation}>
            新对话
          </button>
          <button
            type="button"
            className="btn-icon"
            onClick={() => setHistoryOpen(true)}
            title="历史会话"
            aria-label="打开历史"
          >
            ≡
          </button>
        </div>
      </header>
      <main className="app-main">
        {historyOpen && (
          <>
            <div
              className="history-drawer-backdrop"
              onClick={() => setHistoryOpen(false)}
              aria-hidden
            />
            <div className="history-drawer history-drawer-open" role="dialog" aria-label="历史会话">
              <div className="drawer-header">
              <span>历史会话</span>
              <button
                type="button"
                className="drawer-close"
                onClick={() => setHistoryOpen(false)}
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
                  onClick={() => void loadConversationDetail(c.id)}
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
        )}
        <aside className="rail">
          <button
            type="button"
            className="btn-icon"
            onClick={() => setHistoryOpen(true)}
            title="历史会话"
            aria-label="打开历史"
          >
            ≡
          </button>
        </aside>
        <section className="chat-panel">
          <div
            className="messages"
            ref={messagesRef}
            onScroll={recomputeAtBottom}
          >
            {renderedMessages}
            {messages.length === 0 && (
              <div className="placeholder">
                业务查询输入【查询知识库】，需求分析输入【系统改动点】
              </div>
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
          <div className="input-area">
            <div className="input-wrap">
              <span className="input-prompt">&gt;</span>
              <textarea
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={handleKeyDown}
                placeholder="输入指令，如【查询知识库】或【系统改动点】 · Enter 发送"
              />
            </div>
            <div className="input-actions">
              <label className="upload-btn">
                <span>上传图片</span>
                <input
                  type="file"
                  accept="image/*"
                  multiple
                  onChange={(e) => setSelectedFiles((prev) => [...prev, ...Array.from(e.target.files || [])])}
                />
              </label>
              {selectedFiles.length > 0 && (
                <span className="selected-files">已选 {selectedFiles.length} 张</span>
              )}
              <button type="button" className="print-btn" onClick={() => window.print()}>
                导出/打印
              </button>
              {abortable && (
                <button type="button" className="stop-btn" onClick={handleStop} disabled={stopRequested}>
                  {stopRequested ? "停止中..." : "停止"}
                </button>
              )}
              <button type="button" className="send-btn" onClick={handleSend} disabled={loading}>
                {loading ? "发送中..." : "发送"}
              </button>
            </div>
          </div>
        </section>
      </main>
    </div>
  );
};

