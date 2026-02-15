/**
 * InputArea — 底部输入区：文本框 + 上传图片 + 停止 / 发送按钮。
 */
import React from "react";

interface InputAreaProps {
  input: string;
  loading: boolean;
  abortable: boolean;
  stopRequested: boolean;
  selectedFiles: File[];
  onInputChange: (value: string) => void;
  onSend: () => void;
  onStop: () => void;
  onFilesChange: (files: File[]) => void;
}

export const InputArea: React.FC<InputAreaProps> = React.memo(
  ({
    input,
    loading,
    abortable,
    stopRequested,
    selectedFiles,
    onInputChange,
    onSend,
    onStop,
    onFilesChange,
  }) => {
    const handleKeyDown: React.KeyboardEventHandler<HTMLTextAreaElement> = (e) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        onSend();
      }
    };

    return (
      <div className="input-area">
        <div className="input-wrap">
          <span className="input-prompt">&gt;</span>
          <textarea
            value={input}
            onChange={(e) => onInputChange(e.target.value)}
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
              onChange={(e) =>
                onFilesChange([...selectedFiles, ...Array.from(e.target.files || [])])
              }
            />
          </label>
          {selectedFiles.length > 0 && (
            <span className="selected-files">已选 {selectedFiles.length} 张</span>
          )}
          <button type="button" className="print-btn" onClick={() => window.print()}>
            导出/打印
          </button>
          {abortable && (
            <button type="button" className="stop-btn" onClick={onStop} disabled={stopRequested}>
              {stopRequested ? "停止中..." : "停止"}
            </button>
          )}
          <button type="button" className="send-btn" onClick={onSend} disabled={loading}>
            {loading ? "发送中..." : "发送"}
          </button>
        </div>
      </div>
    );
  }
);

InputArea.displayName = "InputArea";
