import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";
import { Navigate, Outlet, Route, Routes } from "react-router-dom";
import { api, type SessionInfo } from "@/api";
import { Layout } from "@/components/Layout";
import { ImportPage } from "@/pages/Import";
import { JobDetailPage } from "@/pages/JobDetail";
import { JobsPage } from "@/pages/Jobs";
import { LoginPage } from "@/pages/Login";
import { OverviewPage } from "@/pages/Overview";
import { RulesPage } from "@/pages/Rules";
import { SettingsPage } from "@/pages/Settings";

export function App() {
  const queryClient = useQueryClient();

  useEffect(() => {
    const handler = () => queryClient.invalidateQueries({ queryKey: ["session"] });
    window.addEventListener("tabledger:unauthorized", handler);
    return () => window.removeEventListener("tabledger:unauthorized", handler);
  }, [queryClient]);

  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route element={<Protected />}>
        <Route element={<Layout />} path="/">
          <Route index element={<OverviewPage />} />
          <Route path="/import" element={<ImportPage />} />
          <Route path="/jobs" element={<JobsPage />} />
          <Route path="/jobs/:jobId" element={<JobDetailPage />} />
          <Route path="/rules" element={<RulesPage />} />
          <Route path="/settings" element={<SettingsPage />} />
        </Route>
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}

function Protected() {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["session"],
    queryFn: () => api.get<SessionInfo>("/api/v1/auth/session"),
    retry: false,
  });

  if (isLoading) {
    return (
      <div className="flex min-h-screen items-center justify-center text-lumi-muted text-sm">加载中…</div>
    );
  }
  if (isError || !data) {
    return <Navigate to="/login" replace />;
  }
  return <Outlet context={data} />;
}
