import { useState } from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { api } from "@/api";
import { Button, ErrorNote, Input } from "@/components/ui";

export function LoginPage() {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const navigate = useNavigate();
  const location = useLocation();
  const queryClient = useQueryClient();

  const session = queryClient.getQueryData(["session"]);
  if (session) return <Navigate to={fromPath(location.state)} replace />;

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const result = await api.post<{ must_change_password: boolean }>("/api/v1/auth/login", {
        username,
        password,
      });
      await queryClient.invalidateQueries();
      navigate(result.must_change_password ? "/settings?tab=security" : fromPath(location.state), { replace: true });
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "登录失败");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex min-h-screen items-center justify-center bg-lumi-bg px-4">
      <div className="w-full max-w-sm">
        <div className="mb-8 flex flex-col items-center gap-3">
          <span className="flex h-12 w-12 items-center justify-center rounded-2xl bg-lumi-accent text-xl font-semibold text-white">
            T
          </span>
          <h1 className="text-xl font-semibold tracking-tight">TabLedger</h1>
          <p className="text-sm text-lumi-muted">登录以整理你的账单</p>
        </div>
        <form
          onSubmit={submit}
          className="rounded-card border border-lumi-line bg-lumi-surface p-6 shadow-sm shadow-black/[0.03]"
          aria-label="登录表单"
        >
          <div className="space-y-4">
            <label className="block">
              <span className="mb-1.5 block text-sm font-medium">用户名</span>
              <Input
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                autoComplete="username"
                required
                autoFocus
              />
            </label>
            <label className="block">
              <span className="mb-1.5 block text-sm font-medium">密码</span>
              <Input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="current-password"
                required
              />
            </label>
            {error ? <ErrorNote message={error} /> : null}
            <Button type="submit" className="w-full" disabled={busy}>
              {busy ? "登录中…" : "登录"}
            </Button>
          </div>
        </form>
      </div>
    </div>
  );
}

function fromPath(state: unknown): string {
  if (state && typeof state === "object" && "from" in state && typeof state.from === "string") {
    return state.from;
  }
  return "/";
}
