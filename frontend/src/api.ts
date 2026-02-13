export type PayloadType = "KB_ANSWER" | "OPEN_QUESTIONS" | "DRAFT" | "FINAL_DOC" | "INFO" | "TRACE";

export type TracePhase =
  | "INTENT"
  | "KB"
  | "COLLECT"
  | "BUILD_DRAFT"
  | "CONFIRM"
  | "DEFEND"
  | "EDITOR";

export interface TraceStep {
  ts: string;
  phase: TracePhase | string;
  title: string;
  detail?: string;
  level?: "info" | "warn" | "error" | string;
}

export interface AgentRequest {
  sessionId?: string | null;
  text: string;
  imageIds?: string[];
}

export interface AgentResponse {
  sessionId?: string | null;
  intent: "KB_QUERY" | "ORCH_FLOW";
  payloadType: PayloadType;
  content: Record<string, unknown>;
}

export interface ConversationSummary {
  id: string;
  intent: string;
  status: string;
  created_at: string;
  updated_at: string;
  first_user_text?: string;
}

export interface ConversationDetail extends ConversationSummary {
  messages: {
    role: "user" | "assistant" | string;
    payload_type: PayloadType | string;
    content: string;
    created_at: string;
  }[];
}

export async function callAgent(req: AgentRequest): Promise<AgentResponse> {
  const res = await fetch("/api/agent", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req)
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || `HTTP ${res.status}`);
  }
  return (await res.json()) as AgentResponse;
}

type StreamEvent =
  | { event: "trace"; data: TraceStep }
  | { event: "final"; data: AgentResponse }
  | { event: "error"; data: { message?: string } }
  | { event: string; data: any };

function parseSseChunk(block: string): StreamEvent | null {
  const lines = block
    .split("\n")
    .map((l) => l.trimEnd())
    .filter((l) => l.length > 0);
  if (lines.length === 0) return null;

  let eventName = "message";
  const dataLines: string[] = [];
  for (const ln of lines) {
    if (ln.startsWith("event:")) {
      eventName = ln.slice("event:".length).trim() || "message";
    } else if (ln.startsWith("data:")) {
      dataLines.push(ln.slice("data:".length).trim());
    }
  }
  const dataStr = dataLines.join("\n").trim();
  let data: any = dataStr;
  if (dataStr) {
    try {
      data = JSON.parse(dataStr);
    } catch {
      // keep raw string
    }
  }
  return { event: eventName, data } as StreamEvent;
}

export async function callAgentStream(
  req: AgentRequest,
  handlers: {
    onTrace?: (step: TraceStep) => void;
    onFinal?: (resp: AgentResponse) => void;
    onError?: (message: string) => void;
  } = {},
  options: {
    signal?: AbortSignal;
  } = {}
): Promise<AgentResponse> {
  const res = await fetch("/api/agent/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
    signal: options.signal,
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || `HTTP ${res.status}`);
  }
  if (!res.body) {
    throw new Error("浏览器不支持 ReadableStream");
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buf = "";
  let finalResp: AgentResponse | null = null;

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    // 兼容 \r\n 换行
    buf = buf.replace(/\r\n/g, "\n");
    const parts = buf.split("\n\n");
    buf = parts.pop() || "";
    for (const block of parts) {
      const evt = parseSseChunk(block);
      if (!evt) continue;
      if (evt.event === "trace") {
        handlers.onTrace?.(evt.data as TraceStep);
      } else if (evt.event === "final") {
        finalResp = evt.data as AgentResponse;
        handlers.onFinal?.(finalResp);
      } else if (evt.event === "error") {
        const msg = (evt.data && (evt.data as any).message) ? String((evt.data as any).message) : "流式请求失败";
        handlers.onError?.(msg);
        throw new Error(msg);
      }
    }
  }

  // 末尾可能还有残留（不含 \n\n 时忽略即可）
  if (!finalResp) {
    throw new Error("未收到 final 事件");
  }
  return finalResp;
}

export async function uploadImages(files: File[]): Promise<string[]> {
  if (files.length === 0) return [];
  const form = new FormData();
  files.forEach((f) => form.append("files", f));
  const res = await fetch("/api/upload", {
    method: "POST",
    body: form
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || `HTTP ${res.status}`);
  }
  const data = (await res.json()) as { imageIds?: string[] };
  return data.imageIds ?? [];
}

export async function fetchConversations(
  limit = 20,
  offset = 0
): Promise<ConversationSummary[]> {
  const res = await fetch(`/api/conversations?limit=${limit}&offset=${offset}`);
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || `HTTP ${res.status}`);
  }
  return (await res.json()) as ConversationSummary[];
}

export async function fetchConversationDetail(
  id: string
): Promise<ConversationDetail> {
  const res = await fetch(`/api/conversations/${encodeURIComponent(id)}`);
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || `HTTP ${res.status}`);
  }
  return (await res.json()) as ConversationDetail;
}


