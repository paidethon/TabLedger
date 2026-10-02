import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import {
  BookOpenCheck,
  Download,
  ListChecks,
  Moon,
  PlusCircle,
  Settings as SettingsIcon,
  Sun,
  Workflow,
} from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "@/api";

const NAV = [
  { to: "/", label: "总览", icon: BookOpenCheck, end: true },
  { to: "/import", label: "导入", icon: PlusCircle },
  { to: "/jobs", label: "任务", icon: Workflow },
  { to: "/rules", label: "分类规则", icon: ListChecks },
  { to: "/settings", label: "设置", icon: SettingsIcon },
];

export function Layout() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [dark, setDark] = useState(() => document.documentElement.classList.contains("dark"));

  useEffect(() => {
    document.documentElement.classList.toggle("dark", dark);
  }, [dark]);

  const logout = async () => {
    await api.post("/api/v1/auth/logout");
    queryClient.clear();
    navigate("/login");
  };

  return (
    <div className="flex min-h-screen flex-col bg-lumi-bg text-lumi-ink">
      <header className="sticky top-0 z-20 border-b border-lumi-line bg-lumi-surface/90 backdrop-blur">
        <div className="mx-auto flex h-14 max-w-6xl items-center gap-4 px-4">
          <div className="flex items-center gap-2 font-semibold tracking-tight">
            <span className="flex h-7 w-7 items-center justify-center rounded-lg bg-lumi-accent text-sm text-white">
              T
            </span>
            TabLedger
          </div>
          <nav className="hidden flex-1 items-center gap-1 sm:flex" aria-label="主导航">
            {NAV.map(({ to, label, icon: Icon, end }) => (
              <NavLink
                key={to}
                to={to}
                end={end}
                className={({ isActive }) =>
                  `flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-sm transition-colors duration-150 ${
                    isActive
                      ? "bg-lumi-accent-soft font-medium text-lumi-accent"
                      : "text-lumi-muted hover:bg-lumi-accent-soft/60 hover:text-lumi-ink"
                  }`
                }
              >
                <Icon className="h-4 w-4" aria-hidden />
                {label}
              </NavLink>
            ))}
          </nav>
          <div className="ml-auto flex items-center gap-1">
            <button
              type="button"
              onClick={() => setDark((v) => !v)}
              className="rounded-lg p-2 text-lumi-muted transition-colors hover:bg-lumi-accent-soft/60 hover:text-lumi-ink"
              aria-label={dark ? "切换到浅色模式" : "切换到深色模式"}
            >
              {dark ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
            </button>
            <button
              type="button"
              onClick={logout}
              className="rounded-lg px-3 py-1.5 text-sm text-lumi-muted transition-colors hover:bg-lumi-accent-soft/60 hover:text-lumi-ink"
            >
              退出
            </button>
          </div>
        </div>
      </header>

      <main className="mx-auto w-full max-w-6xl flex-1 px-4 pb-24 pt-6 sm:pb-10">
        <Outlet />
      </main>

      <footer className="hidden border-t border-lumi-line py-4 text-center text-xs text-lumi-muted sm:block">
        TabLedger v0.1.0 · 隐私优先的本地账单整理工具
      </footer>

      {/* Mobile bottom navigation */}
      <nav
        className="fixed inset-x-0 bottom-0 z-20 flex border-t border-lumi-line bg-lumi-surface sm:hidden"
        aria-label="底部导航"
      >
        {NAV.map(({ to, label, icon: Icon, end }) => (
          <NavLink
            key={to}
            to={to}
            end={end}
            className={({ isActive }) =>
              `flex flex-1 flex-col items-center gap-0.5 py-2.5 text-[11px] ${
                isActive ? "text-lumi-accent" : "text-lumi-muted"
              }`
            }
          >
            <Icon className="h-5 w-5" aria-hidden />
            {label}
          </NavLink>
        ))}
      </nav>
    </div>
  );
}

export { Download };
