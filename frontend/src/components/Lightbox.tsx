/**
 * Lightbox — 图片 / Mermaid 全尺寸预览灯箱。
 */
import React from "react";

interface LightboxProps {
  imageUrl: string | null;
  mermaidSvg: string | null;
  onClose: () => void;
}

export const Lightbox: React.FC<LightboxProps> = React.memo(
  ({ imageUrl, mermaidSvg, onClose }) => {
    if (!imageUrl && !mermaidSvg) return null;

    if (imageUrl) {
      return (
        <div
          className="lightbox-overlay"
          onClick={onClose}
          role="button"
          aria-label="关闭预览"
        >
          <button
            type="button"
            className="lightbox-close"
            onClick={(e) => {
              e.stopPropagation();
              onClose();
            }}
            aria-label="关闭"
          >
            ×
          </button>
          <span className="lightbox-img-wrap" onClick={(e) => e.stopPropagation()}>
            <img src={imageUrl} alt="放大预览" />
          </span>
        </div>
      );
    }

    return (
      <div
        className="lightbox-overlay lightbox-mermaid"
        onClick={onClose}
        role="button"
        aria-label="关闭流程图预览"
      >
        <button
          type="button"
          className="lightbox-close"
          onClick={(e) => {
            e.stopPropagation();
            onClose();
          }}
          aria-label="关闭"
        >
          ×
        </button>
        <span
          className="lightbox-mermaid-wrap"
          onClick={(e) => e.stopPropagation()}
          dangerouslySetInnerHTML={{ __html: mermaidSvg! }}
        />
      </div>
    );
  }
);

Lightbox.displayName = "Lightbox";
