import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api, type Job } from "@/api";
import { Card, EmptyState, ErrorNote, Spinner } from "@/components/ui";
import { StatusBadge } from "@/pages/Overview";
import { timeAgo } from "@/lib/cn";

export function JobsPage() {
  const { data, isLoading, error } = useQuery({
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
  if (!data || data.length === 0) {
    return <EmptyState title="还没有任务" hint="在导入页上传账单后，任务会出现在这里。" />;
  }

  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold tracking-tight">任务</h1>
      <Card className="divide-y divide-lumi-line">
        {data.map((job) => (
          <Link
            key={job.id}
            to={`/jobs/${job.id}`}
            className="flex flex-wrap items-center gap-3 px-4 py-3 transition-colors hover:bg-lumi-accent-soft/40"
          >
            <div className="min-w-0 flex-1">
              <div className="truncate text-sm font-medium">{job.title}</div>
              <div className="text-xs text-lumi-muted">
                {timeAgo(job.created_at)}
                {job.stats?.import_rows ? ` · ${job.stats.import_rows} 行` : ""}
              </div>
            </div>
            <StatusBadge status={job.status} />
          </Link>
        ))}
      </Card>
    </div>
  );
}
