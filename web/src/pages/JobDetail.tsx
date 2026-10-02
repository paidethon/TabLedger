import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { ArrowLeft, Download, RefreshCw, RotateCcw } from "lucide-react";
import { useState } from "react";
import { api, SOURCE_LABEL, type Job, type MatchExplain, type Transaction, type UnmatchedKey } from "@/api";
import { Badge, Button, Card, ErrorNote, Input, Select, Spinner } from "@/components/ui";
import { StatusBadge } from "@/pages/Overview";
import { cn, formatDelta, money } from "@/lib/cn";

export function JobDetailPage() {
  const { jobId } = useParams<{ jobId: string }>();
  const queryClient = useQueryClient();
  const { data: job, error } = useQuery({
    queryKey: ["job", jobId],
    queryFn: () => api.get<Job>(`/api/v1/jobs/${jobId}`),
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status && !["needs_review", "done", "failed", "interrupted"].includes(status) ? 1500 : false;
    },
  });

  if (error) return <ErrorNote message={error instanceof Error ? error.message : "加载失败"} />;
  if (!job) {
    return (
      <div className="flex justify-center py-20">
        <Spinner />
      </div>
    );
  }

  const active = ["queued", "extracting", "reconciling", "classifying", "exporting"].includes(job.status);
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["job", jobId] });

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <Link to="/jobs" className="rounded-lg p-1.5 text-lumi-muted hover:bg-lumi-accent-soft/60" aria-label="返回任务列表">
            <ArrowLeft className="h-5 w-5" />
          </Link>
          <div>
            <h1 className="text-xl font-semibold tracking-tight">{job.title}</h1>
            <p className="text-xs text-lumi-muted">任务 {job.id}</p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <StatusBadge status={job.status} />
          {["failed", "interrupted"].includes(job.status) ? (
            <Button size="sm" variant="secondary" onClick={rerun}>
              <RotateCcw className="h-3.5 w-3.5" aria-hidden />
              重新运行
            </Button>
          ) : null}
        </div>
      </div>

      {job.status === "failed" ? (
        <ErrorNote message={job.error || "处理失败"} />
      ) : null}

      {active ? <ProcessingCard job={job} /> : null}

      {job.status === "needs_review" || job.status === "done" ? (
        <ReviewSections job={job} />
      ) : null}
    </div>
  );

  async function rerun() {
    await api.post(`/api/v1/jobs/${jobId}/rerun`);
    refresh();
  }
}

function ProcessingCard({ job }: { job: Job }) {
  const steps = [
    { key: "extracting", label: "提取与校验" },
    { key: "reconciling", label: "对账与去重" },
    { key: "classifying", label: "自动分类" },
  ];
  const currentIndex = steps.findIndex((s) => s.key === job.status);
  return (
    <Card className="p-5">
      <ol className="flex flex-col gap-3 sm:flex-row sm:items-center sm:gap-6">
        {steps.map((step, index) => {
          const done = currentIndex > index || job.status === "needs_review";
          const current = currentIndex === index;
          return (
            <li key={step.key} className="flex items-center gap-2 text-sm">
              <span
                className={cn(
                  "flex h-5 w-5 items-center justify-center rounded-full border text-xs",
                  done
                    ? "border-lumi-good bg-lumi-good/10 text-lumi-good"
                    : current
                      ? "border-lumi-accent bg-lumi-accent-soft text-lumi-accent"
                      : "border-lumi-line text-lumi-muted",
                )}
                aria-hidden
              >
                {index + 1}
              </span>
              <span className={current ? "font-medium" : "text-lumi-muted"}>{step.label}</span>
              {current ? <Spinner className="h-3.5 w-3.5 border-lumi-accent" /> : null}
            </li>
          );
        })}
      </ol>
      {job.files ? (
        <ul className="mt-4 space-y-2 border-t border-lumi-line pt-4 text-sm">
          {job.files.map((f) => (
            <li key={f.id} className="flex flex-wrap items-center gap-2">
              <span className="font-medium">{f.name}</span>
              {f.state === "parsed" ? (
                <Badge tone="good">已解析 {f.source}</Badge>
              ) : f.state === "failed" ? (
                <Badge tone="bad">失败：{f.error}</Badge>
              ) : (
                <Badge>{f.state}</Badge>
              )}
            </li>
          ))}
        </ul>
      ) : null}
    </Card>
  );
}

function ReviewSections({ job }: { job: Job }) {
  const [state, setState] = useState("pending");
  return (
    <>
      <UnmatchedCard jobId={job.id} />
      <TransactionsCard jobId={job.id} state={state} onStateChange={setState} />
      <MatchesCard jobId={job.id} />
      {job.status === "needs_review" ? <ExportCard jobId={job.id} /> : <DoneSummary job={job} />}
    </>
  );
}

function UnmatchedCard({ jobId }: { jobId: string }) {
  const { data, isLoading } = useQuery({
    queryKey: ["unmatched", jobId],
    queryFn: () => api.get<UnmatchedKey[]>(`/api/v1/jobs/${jobId}/unmatched`),
  });
  if (isLoading) return <Spinner />;
  if (!data || data.length === 0) {
    return (
      <Card className="p-4">
        <div className="flex items-center gap-2 text-sm">
          <Badge tone="good">分类完成</Badge>
          <span className="text-lumi-muted">没有待分类的商户</span>
        </div>
      </Card>
    );
  }
  return (
    <Card>
      <div className="border-b border-lumi-line px-4 py-3 text-sm font-medium">
        待分类 {data.length} 项
      </div>
      <ul className="divide-y divide-lumi-line text-sm">
        {data.map((slot) => (
          <UnmatchedRow key={`${slot.direction}|${slot.merchant}`} jobId={jobId} slot={slot} />
        ))}
      </ul>
    </Card>
  );
}

function UnmatchedRow({ jobId, slot }: { jobId: string; slot: UnmatchedKey }) {
  const [category, setCategory] = useState("");
  const [subcategory, setSubcategory] = useState("");
  const [saveRule, setSaveRule] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const queryClient = useQueryClient();
  const isIncome = slot.direction === "收入";
  const { data: taxonomy } = useQuery({
    queryKey: ["taxonomy"],
    queryFn: () => api.get<{ expense: Record<string, string[]>; income: string[] }>("/api/v1/settings/taxonomy"),
  });

  const submit = async () => {
    setBusy(true);
    setError("");
    try {
      await api.post(`/api/v1/jobs/${jobId}/classify`, {
        record_uid: "",
        merchant: slot.merchant,
        direction: slot.direction,
        category,
        subcategory: isIncome ? category : subcategory || category,
        save_rule: saveRule,
      });
      setCategory("");
      setSubcategory("");
      queryClient.invalidateQueries({ queryKey: ["unmatched", jobId] });
      queryClient.invalidateQueries({ queryKey: ["transactions", jobId] });
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "分类失败");
    } finally {
      setBusy(false);
    }
  };

  const subs = isIncome ? [] : (taxonomy?.expense[category] ?? []);
  const incomeCategories = taxonomy?.income ?? [];

  return (
    <li className="flex flex-col gap-2 px-4 py-3 lg:flex-row lg:items-center">
      <div className="min-w-0 flex-1">
        <div className="font-medium">{slot.merchant}</div>
        <div className="text-xs text-lumi-muted">
          {slot.direction} · {slot.count} 笔 · ¥{money(slot.amount)}
          {slot.items.length ? ` · 如：${slot.items.join("、")}` : ""}
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        {isIncome ? (
          <Select aria-label="收入类别" value={category} onChange={(e) => setCategory(e.target.value)} className="w-40">
            <option value="">收入类别…</option>
            {incomeCategories.map((key) => (
              <option key={key} value={key}>
                {key}
              </option>
            ))}
          </Select>
        ) : (
          <>
            <Select
              aria-label="类别"
              value={category}
              onChange={(e) => {
                setCategory(e.target.value);
                setSubcategory("");
              }}
              className="w-32"
            >
              <option value="">类别…</option>
              {taxonomy
                ? Object.keys(taxonomy.expense).map((key) => (
                    <option key={key} value={key}>
                      {key}
                    </option>
                  ))
                : null}
            </Select>
            <Select
              aria-label="子类"
              value={subcategory}
              onChange={(e) => setSubcategory(e.target.value)}
              className="w-32"
              disabled={!category}
            >
              <option value="">子类…</option>
              {subs.map((sub) => (
                <option key={sub} value={sub}>
                  {sub}
                </option>
              ))}
            </Select>
          </>
        )}
        <label className="flex items-center gap-1.5 text-xs text-lumi-muted">
          <input type="checkbox" checked={saveRule} onChange={(e) => setSaveRule(e.target.checked)} className="accent-lumi-accent" />
          记住规则
        </label>
        <Button size="sm" onClick={submit} disabled={!category || busy}>
          确定
        </Button>
      </div>
      {error ? <div className="w-full text-xs text-lumi-bad">{error}</div> : null}
    </li>
  );
}

function TransactionsCard({
  jobId,
  state,
  onStateChange,
}: {
  jobId: string;
  state: string;
  onStateChange: (value: string) => void;
}) {
  const [search, setSearch] = useState("");
  const { data, isLoading, error } = useQuery({
    queryKey: ["transactions", jobId, state, search],
    queryFn: () =>
      api.get<{ total: number; items: Transaction[] }>(
        `/api/v1/jobs/${jobId}/transactions?state=${encodeURIComponent(state)}&search=${encodeURIComponent(search)}&limit=300`,
      ),
  });

  return (
    <Card>
      <div className="flex flex-wrap items-center gap-2 border-b border-lumi-line px-4 py-3">
        <div className="text-sm font-medium">交易记录</div>
        {data ? <Badge>{data.total}</Badge> : null}
        <div className="ml-auto flex flex-wrap items-center gap-2">
          <Input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="搜索商户…"
            className="w-40"
            aria-label="搜索商户"
          />
          <Select value={state} onChange={(e) => onStateChange(e.target.value)} className="w-28" aria-label="筛选视图">
            <option value="all">全部</option>
            <option value="pending">待分类</option>
            <option value="review">需确认</option>
            <option value="refund">退款</option>
            <option value="expense">支出</option>
            <option value="income">收入</option>
            <option value="rule">规则命中</option>
            <option value="ai">AI 分类</option>
            <option value="manual">人工修改</option>
          </Select>
        </div>
      </div>
      {error ? <div className="p-4"><ErrorNote message={error instanceof Error ? error.message : "加载失败"} /></div> : null}
      {isLoading ? (
        <div className="flex justify-center py-10">
          <Spinner />
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[720px] text-sm">
            <thead>
              <tr className="border-b border-lumi-line text-left text-xs text-lumi-muted">
                <th className="px-4 py-2 font-medium">时间</th>
                <th className="px-4 py-2 font-medium">来源</th>
                <th className="px-4 py-2 font-medium">商户 / 商品</th>
                <th className="px-4 py-2 text-right font-medium">金额</th>
                <th className="px-4 py-2 font-medium">账户</th>
                <th className="px-4 py-2 font-medium">处置</th>
                <th className="px-4 py-2 font-medium">分类</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-lumi-line">
              {(data?.items ?? []).map((tx) => (
                <TransactionRow key={tx.record_uid} tx={tx} jobId={jobId} />
              ))}
            </tbody>
          </table>
          {(data?.items ?? []).length === 0 ? (
            <div className="py-10 text-center text-sm text-lumi-muted">没有匹配的记录</div>
          ) : null}
        </div>
      )}
    </Card>
  );
}

const DISPOSITION_TONE: Record<string, "good" | "warn" | "bad" | "neutral" | "accent"> = {
  "导入-支出": "good",
  "导入-银行直连支出": "good",
  "导入-部分退款净额": "good",
  "导入-提现手续费": "good",
  "排除-全额退款原单": "accent",
  "保留-跨期退款收入": "warn",
  "暂缓-疑似平台重复": "warn",
  "影子重复-平台记录优先": "neutral",
  "排除-内部资金搬运": "neutral",
  "排除-收入": "neutral",
  "排除-退款入账": "neutral",
  "排除-交易关闭": "neutral",
  "排除-零金额优惠": "neutral",
};

function TransactionRow({ tx, jobId }: { tx: Transaction; jobId: string }) {
  const [editing, setEditing] = useState(false);
  const [category, setCategory] = useState(tx.category);
  const [subcategory, setSubcategory] = useState(tx.subcategory);
  const [saveRule, setSaveRule] = useState(false);
  const queryClient = useQueryClient();
  const { data: taxonomy } = useQuery({
    queryKey: ["taxonomy"],
    queryFn: () => api.get<{ expense: Record<string, string[]>; income: string[] }>("/api/v1/settings/taxonomy"),
  });
  const direction = ["导入-支出", "导入-提现手续费", "导入-部分退款净额", "导入-银行直连支出", "排除-全额退款原单"].includes(
    tx.disposition,
  )
    ? "支出"
    : "收入";
  const isIncome = direction === "收入";
  const editable = ["导入-支出", "导入-银行直连支出", "导入-部分退款净额", "导入-提现手续费", "排除-全额退款原单", "保留-跨期退款收入", "排除-收入", "排除-退款入账"].includes(tx.disposition);

  const save = async () => {
    await api.post(`/api/v1/jobs/${jobId}/classify`, {
      record_uid: tx.record_uid,
      merchant: tx.merchant,
      direction,
      category,
      subcategory: isIncome ? category : subcategory || category,
      save_rule: saveRule,
    });
    setEditing(false);
    queryClient.invalidateQueries({ queryKey: ["unmatched", jobId] });
    queryClient.invalidateQueries({ queryKey: ["transactions", jobId] });
  };

  return (
    <tr className="align-top hover:bg-lumi-accent-soft/30">
      <td className="whitespace-nowrap px-4 py-2 tabular-nums text-lumi-muted">{tx.datetime}</td>
      <td className="px-4 py-2">{SOURCE_LABEL[tx.source] ?? tx.source}</td>
      <td className="max-w-[240px] px-4 py-2">
        <div className="truncate font-medium">{tx.merchant}</div>
        {tx.item ? <div className="truncate text-xs text-lumi-muted">{tx.item}</div> : null}
        {tx.refund_original_uid ? <Badge tone="accent">退款</Badge> : null}
        {tx.review_reasons.length ? <Badge tone="warn">待确认</Badge> : null}
      </td>
      <td className="whitespace-nowrap px-4 py-2 text-right tabular-nums">
        {tx.disposition === "导入-提现手续费" ? (
          <span className="text-lumi-muted">净 {money(tx.net_amount)}</span>
        ) : (
          money(tx.amount)
        )}
      </td>
      <td className="px-4 py-2 text-xs text-lumi-muted">{tx.account || "—"}</td>
      <td className="px-4 py-2">
        <Badge tone={DISPOSITION_TONE[tx.disposition] ?? "neutral"}>{tx.disposition}</Badge>
      </td>
      <td className="px-4 py-2">
        {editing ? (
          <div className="flex flex-wrap items-center gap-1.5">
            {isIncome ? (
              <Select value={category} onChange={(e) => setCategory(e.target.value)} className="w-32 text-xs" aria-label="收入类别">
                <option value="">收入类别…</option>
                {(taxonomy?.income ?? []).map((key) => (
                  <option key={key} value={key}>
                    {key}
                  </option>
                ))}
              </Select>
            ) : (
              <>
                <Select value={category} onChange={(e) => { setCategory(e.target.value); setSubcategory(""); }} className="w-28 text-xs" aria-label="类别">
                  <option value="">类别…</option>
                  {taxonomy
                    ? Object.keys(taxonomy.expense).map((key) => (
                        <option key={key} value={key}>
                          {key}
                        </option>
                      ))
                    : null}
                </Select>
                <Select value={subcategory} onChange={(e) => setSubcategory(e.target.value)} className="w-28 text-xs" aria-label="子类">
                  <option value="">子类…</option>
                  {(taxonomy?.expense[category] ?? []).map((sub) => (
                    <option key={sub} value={sub}>
                      {sub}
                    </option>
                  ))}
                </Select>
              </>
            )}
            <label className="flex items-center gap-1 text-[11px] text-lumi-muted">
              <input type="checkbox" checked={saveRule} onChange={(e) => setSaveRule(e.target.checked)} className="accent-lumi-accent" />
              记住
            </label>
            <Button size="sm" onClick={save} disabled={!category}>
              保存
            </Button>
          </div>
        ) : tx.category ? (
          <button
            type="button"
            onClick={() => editable && setEditing(true)}
            className={cn("text-left text-xs", editable && "cursor-pointer hover:text-lumi-accent")}
          >
            <span className="font-medium">{tx.category}</span>
            {tx.subcategory ? <span className="text-lumi-muted"> / {tx.subcategory}</span> : null}
            <div className="text-[11px] text-lumi-muted">{tx.basis}</div>
          </button>
        ) : (
          <button
            type="button"
            onClick={() => editable && setEditing(true)}
            className="inline-flex items-center gap-1 text-xs text-lumi-warn hover:text-lumi-accent"
          >
            待分类
          </button>
        )}
      </td>
    </tr>
  );
}

function MatchesCard({ jobId }: { jobId: string }) {
  const { data } = useQuery({
    queryKey: ["matches", jobId],
    queryFn: () => api.get<MatchExplain[]>(`/api/v1/jobs/${jobId}/matches`),
  });
  if (!data || data.length === 0) return null;
  return (
    <Card>
      <div className="border-b border-lumi-line px-4 py-3 text-sm font-medium">
        对账说明 <Badge>{data.length}</Badge>
      </div>
      <ul className="divide-y divide-lumi-line text-sm">
        {data.map((m) => (
          <li key={m.match_id} className="px-4 py-3">
            <div className="flex flex-wrap items-center gap-2">
              <Badge tone={m.confidence === "high" ? "good" : m.confidence === "medium" ? "warn" : "neutral"}>
                {m.match_type}
              </Badge>
              <span className="text-xs text-lumi-muted">
                {m.amount_cents != null ? `¥${money(m.amount_cents / 100)}` : ""}
                {m.time_delta_seconds != null ? ` · 时间差 ${formatDelta(m.time_delta_seconds)}` : ""}
              </span>
            </div>
            <p className="mt-1 text-xs text-lumi-muted">{m.reason}</p>
          </li>
        ))}
      </ul>
    </Card>
  );
}

function ExportCard({ jobId }: { jobId: string }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<{ file: string; stats: Record<string, number> } | null>(null);
  const queryClient = useQueryClient();

  const run = async () => {
    setBusy(true);
    setError("");
    try {
      const response = await api.post<{ file: string; stats: Record<string, number> }>(`/api/v1/jobs/${jobId}/export`);
      setResult(response);
      queryClient.invalidateQueries({ queryKey: ["job", jobId] });
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "导出失败");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card className="space-y-3 p-5">
      <div className="flex items-center gap-2 text-sm font-medium">
        <Download className="h-4 w-4" aria-hidden />
        导出一木记账文件
      </div>
      {error ? <ErrorNote message={error} /> : null}
      {result ? (
        <div className="space-y-2 text-sm">
          <div className="flex flex-wrap items-center gap-2">
            <Badge tone="good">导出 {result.stats.export_rows} 行</Badge>
            <Badge>支出 {result.stats.expense_rows} 行 ¥{money(result.stats.expense_total)}</Badge>
            <Badge>收入 {result.stats.income_rows} 行 ¥{money(result.stats.income_total)}</Badge>
            <Badge>重复合并 {result.stats.deduped}</Badge>
            <Badge>内部转账排除 {result.stats.internal_transfers_excluded}</Badge>
            <Badge>退款 {result.stats.refunds}</Badge>
          </div>
          <a href={`/api/v1/jobs/${jobId}/artifacts/${encodeURIComponent(result.file)}`}>
            <Button>
              <Download className="h-4 w-4" aria-hidden />
              下载 {result.file}
            </Button>
          </a>
        </div>
      ) : (
        <Button onClick={run} disabled={busy}>
          {busy ? "导出中…" : "生成并下载"}
        </Button>
      )}
      <a
        href={`/api/v1/jobs/${jobId}/review-pack`}
        className="inline-flex items-center gap-1.5 text-xs text-lumi-muted hover:text-lumi-accent"
      >
        <Download className="h-3 w-3" aria-hidden />
        下载核对包（CSV）
      </a>
    </Card>
  );
}

function DoneSummary({ job }: { job: Job }) {
  const stats = job.stats ?? {};
  return (
    <Card className="flex flex-wrap items-center gap-2 p-5 text-sm">
      <Badge tone="good">已完成导出</Badge>
      {stats.export_rows != null ? <Badge>{stats.export_rows} 行</Badge> : null}
      {stats.expense_total != null ? <Badge>支出 ¥{money(stats.expense_total)}</Badge> : null}
      {stats.income_total != null ? <Badge>收入 ¥{money(stats.income_total)}</Badge> : null}
      <a
        href={`/api/v1/jobs/${job.id}/artifacts/${encodeURIComponent(`一木记账_账单_${job.id}.xls`)}`}
        className="inline-flex items-center gap-1.5 text-lumi-accent hover:underline"
      >
        <Download className="h-3.5 w-3.5" aria-hidden />
        重新下载
      </a>
    </Card>
  );
}

export { RefreshCw };
