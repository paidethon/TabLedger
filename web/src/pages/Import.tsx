import { useCallback, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { FileSpreadsheet, FileText, Lock, UploadCloud } from "lucide-react";
import { api } from "@/api";
import { Button, Card, ErrorNote, Input } from "@/components/ui";
import { cn } from "@/lib/cn";

interface StagedFile {
  id: string;
  name: string;
  size: number;
  file: File;
  needsPassword: boolean;
}

const SUFFIX_LABELS: Record<string, string> = {
  zip: "压缩账单",
  csv: "支付宝 CSV",
  xlsx: "微信 XLSX",
  pdf: "银行 PDF",
};

export function ImportPage() {
  const [files, setFiles] = useState<StagedFile[]>([]);
  const [password, setPassword] = useState("");
  const [dragging, setDragging] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const navigate = useNavigate();

  const addFiles = useCallback((incoming: FileList | null) => {
    if (!incoming) return;
    setError("");
    const next: StagedFile[] = [];
    for (const file of Array.from(incoming)) {
      const suffix = file.name.split(".").pop()?.toLowerCase() ?? "";
      if (!["zip", "csv", "xlsx", "pdf"].includes(suffix)) {
        setError(`不支持的文件类型：${file.name}`);
        continue;
      }
      next.push({
        id: `${file.name}-${file.size}-${crypto.randomUUID()}`,
        name: file.name,
        size: file.size,
        file,
        needsPassword: suffix === "zip" || suffix === "pdf",
      });
    }
    setFiles((prev) => [...prev, ...next].slice(0, 12));
  }, []);

  const removeFile = (id: string) => setFiles((prev) => prev.filter((f) => f.id !== id));

  const submit = async () => {
    if (files.length === 0) return;
    setBusy(true);
    setError("");
    try {
      const form = new FormData();
      for (const staged of files) form.append("files", staged.file, staged.name);
      const passwords = password ? [password] : [];
      form.append("passwords", JSON.stringify(passwords));
      const job = await api.postForm<{ id: string }>("/api/v1/jobs", form);
      navigate(`/jobs/${job.id}`);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "上传失败");
      setBusy(false);
    }
  };

  return (
    <div className="mx-auto max-w-2xl space-y-6">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">导入新账单</h1>
        <p className="text-sm text-lumi-muted">
          支持支付宝 ZIP/CSV、微信 ZIP/XLSX、工商银行 PDF、中国银行 PDF，来源自动识别
        </p>
      </div>

      <div
        role="button"
        tabIndex={0}
        aria-label="拖入或点击选择账单文件"
        onClick={() => inputRef.current?.click()}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") inputRef.current?.click();
        }}
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          addFiles(e.dataTransfer.files);
        }}
        className={cn(
          "flex cursor-pointer flex-col items-center gap-3 rounded-card border-2 border-dashed px-6 py-12 text-center transition-colors duration-150",
          dragging ? "border-lumi-accent bg-lumi-accent-soft/60" : "border-lumi-line bg-lumi-surface hover:border-lumi-accent/60",
        )}
      >
        <UploadCloud className="h-10 w-10 text-lumi-accent" aria-hidden />
        <div className="font-medium">拖入账单文件，或点击选择</div>
        <div className="text-xs text-lumi-muted">一次最多 12 个文件；加密账单稍后输入密码</div>
        <input
          ref={inputRef}
          type="file"
          multiple
          accept=".zip,.csv,.xlsx,.pdf"
          className="hidden"
          onChange={(e) => {
            addFiles(e.target.files);
            e.target.value = "";
          }}
        />
      </div>

      {files.length > 0 ? (
        <Card className="divide-y divide-lumi-line">
          {files.map((staged) => (
            <div key={staged.id} className="flex items-center gap-3 px-4 py-3">
              {staged.name.endsWith(".pdf") ? (
                <FileText className="h-5 w-5 shrink-0 text-lumi-muted" aria-hidden />
              ) : (
                <FileSpreadsheet className="h-5 w-5 shrink-0 text-lumi-muted" aria-hidden />
              )}
              <div className="min-w-0 flex-1">
                <div className="truncate text-sm font-medium">{staged.name}</div>
                <div className="flex items-center gap-2 text-xs text-lumi-muted">
                  <span>{SUFFIX_LABELS[staged.name.split(".").pop() ?? ""]}</span>
                  <span>·</span>
                  <span>{(staged.size / 1024).toFixed(1)} KB</span>
                  {staged.needsPassword ? (
                    <span className="inline-flex items-center gap-1 text-lumi-warn">
                      <Lock className="h-3 w-3" aria-hidden /> 需要密码
                    </span>
                  ) : null}
                </div>
              </div>
              <button
                type="button"
                onClick={() => removeFile(staged.id)}
                className="rounded-lg px-2 py-1 text-xs text-lumi-muted hover:bg-lumi-accent-soft/60 hover:text-lumi-ink"
              >
                移除
              </button>
            </div>
          ))}
        </Card>
      ) : null}

      <Card className="space-y-3 p-4">
        <label className="block">
          <span className="mb-1.5 flex items-center gap-1.5 text-sm font-medium">
            <Lock className="h-4 w-4 text-lumi-warn" aria-hidden />
            通用密码（可选）
          </span>
          <Input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="若有密码截图，可先填一个通用密码，脚本会自动尝试"
            autoComplete="off"
          />
        </label>
        <p className="text-xs text-lumi-muted">
          密码只在本机内存中使用，不会写入数据库或日志；多个不同密码的账单建议逐个上传处理。
        </p>
      </Card>

      {error ? <ErrorNote message={error} /> : null}

      <div className="flex justify-end gap-2">
        <Button variant="secondary" onClick={() => setFiles([])} disabled={busy}>
          清空
        </Button>
        <Button onClick={submit} disabled={files.length === 0 || busy}>
          {busy ? "上传中…" : `开始处理（${files.length} 个文件）`}
        </Button>
      </div>
    </div>
  );
}
