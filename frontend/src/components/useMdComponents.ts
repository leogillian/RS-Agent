/**
 * useMdComponents — ReactMarkdown 的 components 配置 hook。
 *
 * 提取自 App.tsx，将 code / h2 / h3 / img / a 的自定义渲染逻辑集中管理。
 */
import React, { useMemo } from "react";
import type { Components } from "react-markdown";
import { MermaidBlock } from "../MermaidBlock";

function getHeadingText(children: React.ReactNode) {
  const parts: string[] = [];
  React.Children.forEach(children, (child) => {
    if (typeof child === "string") parts.push(child);
    else if (Array.isArray(child)) parts.push(child.join(""));
  });
  return parts.join("").trim();
}

export function useMdComponents(
  onImageClick: (url: string) => void,
  onMermaidExpand: (svg: string) => void,
): Components {
  return useMemo(() => ({
    code({ inline, className, children, ...props }) {
      const match = /language-(\w+)/.exec(className || "");
      const lang = match?.[1];
      const code = String(children).replace(/\n$/, "");
      if (lang === "mermaid" && !inline) {
        return React.createElement(MermaidBlock, { code, onExpand: onMermaidExpand });
      }
      return React.createElement("code", { className, ...props }, children);
    },
    h2({ children, ...props }) {
      const text = getHeadingText(children);
      const cn =
        text.includes("待澄清项记录") ? "md-heading-clarifications" :
        text.includes("风险与回滚要点") ? "md-heading-risk" : undefined;
      return React.createElement("h2", { className: cn, ...props }, children);
    },
    h3({ children, ...props }) {
      const text = getHeadingText(children);
      const cn = text.includes("风险与回滚要点") ? "md-heading-risk" : undefined;
      return React.createElement("h3", { className: cn, ...props }, children);
    },
    img({ src, alt, ...props }) {
      if (!src) return React.createElement("img", props);
      const fullSrc =
        src.startsWith("http") || src.startsWith("data:")
          ? src
          : `${window.location.origin}${src.startsWith("/") ? "" : "/"}${src}`;
      return React.createElement("img", {
        src: fullSrc,
        alt: alt || "附图",
        className: "msg-markdown-img",
        onClick: () => onImageClick(fullSrc),
        role: "button",
        title: "点击查看大图",
        ...props,
      });
    },
    a({ href, children, ...props }) {
      const isImage =
        href &&
        (/\.(png|jpe?g|gif|webp)$/i.test(href) ||
          /\/api\/kb-images\/[^"?#]+\.(png|jpe?g|gif|webp)/i.test(href));
      const isPdf = href && /\.pdf$/i.test(href);
      if (isImage) {
        const fullHref = href.startsWith("http")
          ? href
          : `${window.location.origin}${href.startsWith("/") ? "" : "/"}${href}`;
        return React.createElement(
          "a",
          { href, ...props, onClick: (e: React.MouseEvent) => { e.preventDefault(); onImageClick(fullHref); } },
          children,
        );
      }
      if (isPdf) {
        return React.createElement("a", { href, ...props, target: "_blank", rel: "noopener noreferrer" }, children);
      }
      return React.createElement("a", { href, ...props }, children);
    },
  } as Components), [onImageClick, onMermaidExpand]);
}
