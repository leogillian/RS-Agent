import React, { useEffect, useRef, useState } from "react";

interface MermaidBlockProps {
  code: string;
  onExpand?: (svg: string) => void;
}

const INVALID_CODE = new Set(["", "undefined", "null"]);

function isInvalidMermaidCode(raw: string | undefined): boolean {
  if (raw == null) return true;
  const s = String(raw).trim();
  return !s || INVALID_CODE.has(s.toLowerCase());
}

let mermaidModPromise: Promise<any> | null = null;
let mermaidInitialized = false;
const svgCache = new Map<string, string>();
const MAX_CACHE = 50;

function hashCode(s: string): string {
  // djb2-ish
  let h = 5381;
  for (let i = 0; i < s.length; i++) {
    h = ((h << 5) + h) ^ s.charCodeAt(i);
  }
  // unsigned -> base36
  return (h >>> 0).toString(36);
}

function getCachedSvg(code: string): string | undefined {
  const key = String(code || "");
  return svgCache.get(key);
}

function putCachedSvg(code: string, svg: string) {
  const key = String(code || "");
  if (!key) return;
  if (svgCache.has(key)) return;
  svgCache.set(key, svg);
  // 简单 LRU：超限时删除最早插入
  if (svgCache.size > MAX_CACHE) {
    const firstKey = svgCache.keys().next().value as string | undefined;
    if (firstKey) svgCache.delete(firstKey);
  }
}

/** 渲染 Mermaid 流程图，用于 ReactMarkdown 的 code 组件 */
export const MermaidBlock: React.FC<MermaidBlockProps> = ({ code, onExpand }) => {
  const containerRef = useRef<HTMLDivElement>(null);
  const [error, setError] = useState<string | null>(null);
  const [svg, setSvg] = useState<string>("");
  const cachedSvg = !isInvalidMermaidCode(code) ? (getCachedSvg(code) || "") : "";

  useEffect(() => {
    if (isInvalidMermaidCode(code)) return;
    let cancelled = false;
    const run = async () => {
      try {
        // 命中缓存：避免重渲染时闪烁/重复计算
        const cached = getCachedSvg(code);
        if (cached) {
          if (!cancelled) {
            setSvg(cached);
            setError(null);
          }
          return;
        }

        if (!mermaidModPromise) {
          mermaidModPromise = import("mermaid").then((m) => (m as any).default || m);
        }
        const mermaid = await mermaidModPromise;
        if (!mermaidInitialized) {
          mermaid.initialize({ startOnLoad: false, theme: "neutral" });
          mermaidInitialized = true;
        }
        const id = "mermaid-" + hashCode(code);
        const { svg: result } = await mermaid.render(id, code);
        if (!cancelled) {
          setSvg(result);
          setError(null);
          putCachedSvg(code, result);
        }
      } catch (e) {
        if (!cancelled) {
          setError(String(e));
          setSvg("");
        }
      }
    };
    void run();
    return () => {
      cancelled = true;
    };
  }, [code]);

  if (isInvalidMermaidCode(code)) {
    return (
      <div className="mermaid-fallback" ref={containerRef}>
        <pre>暂无流程图</pre>
      </div>
    );
  }
  if (error) {
    return (
      <div className="mermaid-error">
        <pre className="mermaid-fallback">{code}</pre>
        <span className="mermaid-error-msg">Mermaid 渲染失败：{error}</span>
      </div>
    );
  }
  // 若组件因父级重渲染被重建，优先直接用缓存渲染，避免“渲染中…”闪一下
  if (!svg && cachedSvg) {
    const content = (
      <div
        className="mermaid-container mermaid-thumbnail"
        ref={containerRef}
        dangerouslySetInnerHTML={{ __html: cachedSvg }}
      />
    );
    return onExpand ? (
      <div
        className="mermaid-thumbnail-wrapper"
        onClick={() => onExpand(cachedSvg)}
        role="button"
        title="点击查看大图"
      >
        {content}
        <span className="mermaid-expand-hint">点击放大</span>
      </div>
    ) : (
      <div className="mermaid-container" ref={containerRef} dangerouslySetInnerHTML={{ __html: cachedSvg }} />
    );
  }
  if (!svg) {
    return <div className="mermaid-loading" ref={containerRef}>渲染中…</div>;
  }
  const content = (
    <div
      className="mermaid-container mermaid-thumbnail"
      ref={containerRef}
      dangerouslySetInnerHTML={{ __html: svg }}
    />
  );
  return onExpand ? (
    <div
      className="mermaid-thumbnail-wrapper"
      onClick={() => onExpand(svg)}
      role="button"
      title="点击查看大图"
    >
      {content}
      <span className="mermaid-expand-hint">点击放大</span>
    </div>
  ) : (
    <div className="mermaid-container" ref={containerRef} dangerouslySetInnerHTML={{ __html: svg }} />
  );
};
