/**
 * App — RS-Agent 主页面组件（P1-2 拆分后仅负责全局状态与顶层布局）。
 */
import React, { useCallback, useEffect, useRef, useState } from "react";
import type {
  AgentResponse,
  ConversationDetail,
  ConversationSummary,
  PayloadType,
  TraceStep,
} from "./api";
import {
  callAgent,
  callAgentStream,
  fetchConversationDetail,
  fetchConversations,
  uploadImages,
} from "./api";
import type { Message } from "./components/types";
import { ChatPanel } from "./components/ChatPanel";
import { HistoryDrawer } from "./components/HistoryDrawer";
import { Lightbox } from "./components/Lightbox";
import { useMdComponents } from "./components/useMdComponents";

export const App: React.FC = () => {
  // -- state ---------------------------------------------------------------
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [currentConversationId, setCurrentConversationId] = useState<string | null>(null);
  const [input, setInput] = useState("");
  const [messages, setMessages] = useState<Message[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [selectedFiles, setSelectedFiles] = useState<File[]>([]);
  const [lightboxUrl, setLightboxUrl] = useState<string | null>(null);
  const [mermaidSvg, setMermaidSvg] = useState<string | null>(null);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [abortable, setAbortable] = useState(false);
  const [stopRequested, setStopRequested] = useState(false);
  const [justSent, setJustSent] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const msgIdRef = useRef(1);

  // -- markdown components hook --------------------------------------------
  const mdComponents = useMdComponents(setLightboxUrl, setMermaidSvg);

  // -- message helpers -----------------------------------------------------
  const appendMessage = useCallback((msg: Omit<Message, "id">) => {
    const id = msgIdRef.current++;
    setMessages((prev) => [...prev, { ...msg, id, createdAt: msg.createdAt ?? new Date().toISOString() }]);
    return id;
  }, []);

  const updateMessageById = useCallback((id: number, patch: Partial<Message>) => {
    setMessages((prev) => prev.map((m) => (m.id === id ? { ...m, ...patch } : m)));
  }, []);

  // -- conversations -------------------------------------------------------
  const loadConversations = useCallback(async () => {
    try { setConversations(await fetchConversations(10, 0)); } catch (e: any) { console.error("加载会话列表失败", e); }
  }, []);

  useEffect(() => { void loadConversations(); }, [loadConversations]);

  const loadConversationDetail = useCallback(async (convId: string) => {
    try {
      abortRef.current?.abort();
      const detail: ConversationDetail = await fetchConversationDetail(convId);
      setCurrentConversationId(convId);
      setHistoryOpen(false);
      let pendingTrace: TraceStep[] | null = null;
      const msgs: Message[] = [];
      for (const m of detail.messages) {
        const pt = (m.payload_type as PayloadType) || undefined;
        if (pt === "TRACE") { try { const p = JSON.parse(m.content); if (Array.isArray(p)) pendingTrace = p; } catch { /* */ } continue; }
        const msg: Message = { id: msgs.length + 1, role: m.role === "user" ? "user" : "assistant", text: m.content, payloadType: pt, createdAt: m.created_at };
        if (msg.role === "assistant" && pendingTrace) { msg.traceSteps = pendingTrace; pendingTrace = null; }
        msgs.push(msg);
      }
      setMessages(msgs);
      msgIdRef.current = msgs.length + 1;
      setSessionId(detail.intent === "ORCH_FLOW" ? detail.id : null);
    } catch (e: any) { setError(e?.message || String(e)); }
  }, []);

  const handleNewConversation = useCallback(() => {
    abortRef.current?.abort();
    setCurrentConversationId(null); setSessionId(null); setMessages([]); msgIdRef.current = 1;
    setInput(""); setError(null); setSelectedFiles([]); setHistoryOpen(false);
    void loadConversations();
  }, [loadConversations]);

  // -- time formatting -----------------------------------------------------
  const formatTime = useCallback((iso?: string) => {
    if (!iso) return "";
    try { return new Date(iso).toLocaleTimeString("zh-CN", { hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit" }); } catch { return ""; }
  }, []);

  // -- send ----------------------------------------------------------------
  const handleSend = useCallback(async () => {
    const text = input.trim();
    if (!text || loading) return;
    setError(null); setInput("");
    appendMessage({ role: "user", text });
    setJustSent(true); setStopRequested(false); setLoading(true);
    try {
      let imageIds: string[] = [];
      if (selectedFiles.length > 0) { imageIds = await uploadImages(selectedFiles); setSelectedFiles([]); }
      const phId = appendMessage({ role: "assistant", text: "", payloadType: "INFO", traceSteps: [], streaming: true });
      const ctrl = new AbortController(); abortRef.current = ctrl; setAbortable(true);

      const finalize = (resp: AgentResponse) => {
        if (resp.sessionId != null) setSessionId(resp.sessionId);
        const c = resp.content || {};
        let t = "", imgs: string[] | undefined, prompt: string | undefined;
        switch (resp.payloadType) {
          case "KB_ANSWER": t = typeof c["markdown"] === "string" ? c["markdown"] as string : "[KB 查询完成]"; imgs = Array.isArray(c["images"]) ? c["images"] as string[] : undefined; break;
          case "OPEN_QUESTIONS": t = Array.isArray(c["questions"]) ? `我有一些问题需要你补充说明：\n${(c["questions"] as string[]).map((q, i) => `${i+1}. ${q}`).join("\n")}\n\n你可以一次性回答。` : "请补充说明你的需求。"; break;
          case "DRAFT": t = typeof c["markdown"] === "string" ? c["markdown"] as string : "[已生成草稿]"; prompt = typeof c["prompt_to_user"] === "string" ? c["prompt_to_user"] as string : undefined; break;
          case "FINAL_DOC": t = typeof c["markdown"] === "string" ? c["markdown"] as string : "[已生成最终文档]"; break;
          default: t = typeof c["message"] === "string" ? c["message"] as string : "[完成]"; break;
        }
        updateMessageById(phId, { text: t, payloadType: resp.payloadType, images: imgs, promptToUser: prompt, streaming: false });
      };

      const addTrace = (step: TraceStep) => {
        setMessages((prev) => prev.map((m) => m.id !== phId ? m : { ...m, traceSteps: [...(m.traceSteps || []), step] }));
      };

      try {
        const resp = await callAgentStream(
          { sessionId, text, imageIds: imageIds.length > 0 ? imageIds : undefined },
          { onTrace: addTrace, onFinal: finalize, onError: (msg) => setError(msg) },
          { signal: ctrl.signal },
        );
        finalize(resp);
      } catch (e: any) {
        if (e?.name === "AbortError" || String(e?.message || "").toLowerCase().includes("abort")) {
          addTrace({ ts: new Date().toISOString(), phase: "INTENT", title: "已停止生成", detail: "用户手动停止", level: "warn" });
          updateMessageById(phId, { text: "已停止生成。你可以继续输入新的指令。", payloadType: "INFO", streaming: false });
          return;
        }
        console.warn("流式请求失败，回退到非流式 /api/agent", e);
        finalize(await callAgent({ sessionId, text, imageIds: imageIds.length > 0 ? imageIds : undefined }));
      } finally { abortRef.current = null; setAbortable(false); setStopRequested(false); }
      void loadConversations();
    } catch (e: any) { setError(e?.message || String(e)); } finally { setLoading(false); }
  }, [input, loading, sessionId, selectedFiles, appendMessage, updateMessageById, loadConversations]);

  const handleStop = useCallback(() => { if (!abortRef.current) return; setStopRequested(true); abortRef.current?.abort(); }, []);

  // -- keyboard shortcuts --------------------------------------------------
  useEffect(() => {
    if (!lightboxUrl && !mermaidSvg) return;
    const h = (e: KeyboardEvent) => { if (e.key === "Escape") { setLightboxUrl(null); setMermaidSvg(null); } };
    document.addEventListener("keydown", h); return () => document.removeEventListener("keydown", h);
  }, [lightboxUrl, mermaidSvg]);

  useEffect(() => {
    if (!abortable || lightboxUrl) return;
    const h = (e: KeyboardEvent) => { if (e.key === "Escape") { e.preventDefault(); handleStop(); } };
    document.addEventListener("keydown", h); return () => document.removeEventListener("keydown", h);
  }, [abortable, lightboxUrl, handleStop]);

  // -- lightbox close ------------------------------------------------------
  const closeLightbox = useCallback(() => { setLightboxUrl(null); setMermaidSvg(null); }, []);

  // -- render --------------------------------------------------------------
  return (
    <div className="app-root">
      <Lightbox imageUrl={lightboxUrl} mermaidSvg={mermaidSvg} onClose={closeLightbox} />
      <header className="app-header">
        <div className="header-left">
          <h1>RS-Agent</h1>
          <span className="tagline">支持业务查询 & 需求分析</span>
        </div>
        <div className="header-actions">
          <button type="button" className="sidebar-new" onClick={handleNewConversation}>新对话</button>
          <button type="button" className="btn-icon" onClick={() => setHistoryOpen(true)} title="历史会话" aria-label="打开历史">≡</button>
        </div>
      </header>
      <main className="app-main">
        <HistoryDrawer open={historyOpen} conversations={conversations} currentConversationId={currentConversationId} onClose={() => setHistoryOpen(false)} onSelect={(id) => void loadConversationDetail(id)} />
        <aside className="rail">
          <button type="button" className="btn-icon" onClick={() => setHistoryOpen(true)} title="历史会话" aria-label="打开历史">≡</button>
        </aside>
        <ChatPanel
          messages={messages} mdComponents={mdComponents} formatTime={formatTime} error={error}
          input={input} loading={loading} abortable={abortable} stopRequested={stopRequested}
          selectedFiles={selectedFiles} onInputChange={setInput} onSend={handleSend} onStop={handleStop}
          onFilesChange={setSelectedFiles} onImageClick={setLightboxUrl}
          justSent={justSent} onJustSentConsumed={() => setJustSent(false)}
        />
      </main>
    </div>
  );
};
