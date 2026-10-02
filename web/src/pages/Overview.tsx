import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { PlusCircle } from "lucide-react";
import { api, STATUS_LABEL, type Job } from "@/api";
import { Badge, Button, Card, EmptyState, ErrorNote, Spinner, Stat } from "@/components/ui";
import { money, timeAgo } from "@/lib/cn";

export function OverviewPage() {
  const { data: jobs, isLoading, error } = useQuery({
    queryKey: ["jobs"],
    queryFn: () => api.get<Job[]>("/api/v1/jobs"),
  });

  if (isLoading) {
    return (
      <div className="flex justify-center py-20">
        <Spinner />
      </div>
    );
  }
  if (error) return <ErrorNote message={error instanceof Error ? error.message : "加载失败"} />;

  const recent = (jobs ?? []).slice(0, 5);
  const pendingReview = (jobs ?? []).filter((j) => j.status === "needs_review").length;
  const done = (jobs ?? []).filter((j) => j.status === "done");
  const latestDone = done[0];

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">总览</h1>
          <p className="text-sm text-lumi-muted">账单整理的当前状态</p>
        </div>
        <Link to="/import">
          <Button size="lg">
            <PlusCircle className="h-5 w-5" aria-hidden />
            导入新账单
          </Button>
        </Link>
      </div>

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Stat label="待审核任务" value={pendingReview} hint="需要人工确认" />
        <Stat label="已完成任务" value={done.length} />
        <Stat
          label="最近导出行数"
          value={latestDone?.stats?.export_rows != null ? latestDone.stats.export_rows : "—"}
        />
        <Stat
          label="任务总数"
          value={(jobs ?? []).length}
        />
      </div>

      <Card>
        <div className="border-b border-lumi-line px-4 py-3 text-sm font-medium">最近任务</div>
        {recent.length === 0 ? (
          <EmptyState
            title="还没有任务"
            hint="从导入页上传支付宝 / 微信 / 工行 / 中行账单，TabLedger 会自动识别、对账、去重并生成一木导入文件。"
          >
            <Link to="/import">
              <Button className="mt-2">
                <PlusCircle className="h-4 w-4" aria-hidden />
                开始导入
              </Button>
            </Link>
          </EmptyState>
        ) : (
          <ul className="divide-y divide-lumi-line">
            {recent.map((job) => (
              <li key={job.id}>
                <Link
                  to={`/jobs/${job.id}`}
                  className="flex items-center gap-3 px-4 py-3 transition-colors hover:bg-lumi-accent-soft/40"
                >
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-sm font-medium">{job.title}</div>
                    <div className="text-xs text-lumi-muted">{timeAgo(job.created_at)}</div>
                  </div>
                  <StatusBadge status={job.status} />
                </Link>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}

export function StatusBadge({ status }: { status: string }) {
  const tone =
    status === "done"
      ? "good"
      : status === "needs_review"
        ? "accent"
        : status === "failed"
          ? "bad"
          : status === "interrupted"
            ? "warn"
            : "neutral";
  const active = ["queued", "extracting", "reconciling", "classifying", "exporting"].includes(status);
  return (
    <Badge tone={tone}>
      {active ? <Spinner className="h-3 w-3 border-lumi-accent" /> : null}
      {STATUS_LABEL[status] ?? status}
    </Badge>
  );
}

export { money };
