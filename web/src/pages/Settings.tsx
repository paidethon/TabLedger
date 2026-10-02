import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { KeyRound, Lock, Plus, ShieldCheck, Trash2 } from "lucide-react";
import { api } from "@/api";
import { Badge, Button, Card, ErrorNote, Input, Select, Spinner } from "@/components/ui";

const TABS = [
  { key: "ai", label: "AI 分类" },
  { key: "accounts", label: "账户映射" },
  { key: "privacy", label: "隐私与数据" },
  { key: "security", label: "账号安全" },
];

export function SettingsPage() {
  const [params, setParams] = useSearchParams();
  const tab = params.get("tab") ?? "ai";
  return (
    <div className="space-y-6">
      <h1 className="text-xl font-semibold tracking-tight">设置</h1>
      <div role="tablist" aria-label="设置分区" className="flex flex-wrap gap-1">
        {TABS.map((t) => (
          <button
            key={t.key}
            role="tab"
            aria-selected={tab === t.key}
            onClick={() => setParams({ tab: t.key })}
            className={`rounded-lg px-3 py-1.5 text-sm transition-colors ${
              tab === t.key ? "bg-lumi-accent-soft font-medium text-lumi-accent" : "text-lumi-muted hover:bg-lumi-accent-soft/60"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>
      {tab === "ai" ? <AISettings /> : null}
      {tab === "accounts" ? <AccountSettings /> : null}
      {tab === "privacy" ? <PrivacySettings /> : null}
      {tab === "security" ? <SecuritySettings /> : null}
    </div>
  );
}

const PRESETS: Record<string, { base_url: string; hint: string }> = {
  openai: { base_url: "", hint: "OpenAI 官方" },
  deepseek: { base_url: "https://api.deepseek.com", hint: "DeepSeek" },
  anthropic: { base_url: "", hint: "Anthropic" },
  gemini: { base_url: "", hint: "Gemini" },
  openrouter: { base_url: "https://openrouter.ai/api", hint: "OpenRouter" },
  custom: { base_url: "", hint: "自定义 OpenAI 兼容端点" },
};

function AISettings() {
  const queryClient = useQueryClient();
  const { data, isLoading } = useQuery({
    queryKey: ["ai-settings"],
    queryFn: () =>
      api.get<{
        enabled: boolean;
        provider: string;
        base_url: string;
        api_key_hint: string;
        model: string;
        timeout_seconds: number;
        retry: number;
        batch_size: number;
        max_output_tokens: number;
      }>("/api/v1/settings/ai"),
  });
  interface AIForm {
    enabled: boolean;
    provider: string;
    base_url: string;
    api_key_hint: string;
    model: string;
    timeout_seconds: number;
    retry: number;
    batch_size: number;
    max_output_tokens: number;
  }
  const [form, setForm] = useState<AIForm | null>(null);
  const [apiKey, setApiKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    if (data && form === null) setForm({ ...data });
  }, [data, form]);

  if (isLoading || !form) {
    return (
      <div className="flex justify-center py-10">
        <Spinner />
      </div>
    );
  }

  const save = async () => {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const payload: Record<string, unknown> = { ...form };
      if (apiKey) payload.api_key = apiKey;
      await api.put("/api/v1/settings/ai", payload);
      setApiKey("");
      setNotice("已保存");
      queryClient.invalidateQueries({ queryKey: ["ai-settings"] });
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "保存失败");
    } finally {
      setBusy(false);
    }
  };

  const testConnection = async () => {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const result = await api.post<{ ok: boolean; error?: string; input_tokens?: number; output_tokens?: number }>(
        "/api/v1/settings/ai/test",
      );
      setNotice(
        result.ok
          ? `连接成功（${result.input_tokens ?? 0} + ${result.output_tokens ?? 0} tokens）`
          : `连接失败：${result.error ?? "未知错误"}`,
      );
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "测试失败");
    } finally {
      setBusy(false);
    }
  };

  const fetchModels = async () => {
    setBusy(true);
    setError("");
    try {
      const result = await api.get<{ ok: boolean; models: string[] }>("/api/v1/settings/ai/models");
      setNotice(result.ok ? `可用模型 ${result.models.length} 个：${result.models.slice(0, 8).join("、")}` : "该端点不支持列出模型，请手动输入模型名");
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "获取失败");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card className="max-w-2xl space-y-4 p-5">
      <label className="flex items-center gap-2 text-sm font-medium">
        <input
          type="checkbox"
          checked={Boolean(form.enabled)}
          onChange={(e) => setForm({ ...form, enabled: e.target.checked })}
          className="accent-lumi-accent"
        />
        启用 AI 分类（仅对规则无法确定的项目，批量低 token 调用）
      </label>

      <div className="grid grid-cols-2 gap-3">
        <label className="block">
          <span className="mb-1.5 block text-sm">Provider</span>
          <Select value={String(form.provider)} onChange={(e) => setForm({ ...form, provider: e.target.value })}>
            {Object.entries(PRESETS).map(([value, preset]) => (
              <option key={value} value={value}>
                {preset.hint}
              </option>
            ))}
          </Select>
        </label>
        <label className="block">
          <span className="mb-1.5 block text-sm">Base URL（可选）</span>
          <Input
            value={String(form.base_url)}
            onChange={(e) => setForm({ ...form, base_url: e.target.value })}
            placeholder="OpenAI 兼容端点"
          />
        </label>
      </div>

      <label className="block">
        <span className="mb-1.5 flex items-center justify-between text-sm">
          <span>API Key</span>
          {form.api_key_hint ? (
            <Badge tone="good">
              <Lock className="h-3 w-3" aria-hidden /> 已保存 {String(form.api_key_hint)}
            </Badge>
          ) : (
            <Badge>未配置</Badge>
          )}
        </span>
        <Input
          type="password"
          value={apiKey}
          onChange={(e) => setApiKey(e.target.value)}
          placeholder={form.api_key_hint ? "留空保持不变" : "输入 API Key"}
          autoComplete="off"
        />
      </label>

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <label className="block">
          <span className="mb-1.5 block text-sm">模型</span>
          <Input value={String(form.model)} onChange={(e) => setForm({ ...form, model: e.target.value })} placeholder="model name" />
        </label>
        <label className="block">
          <span className="mb-1.5 block text-sm">超时（秒）</span>
          <Input
            type="number"
            value={String(form.timeout_seconds)}
            onChange={(e) => setForm({ ...form, timeout_seconds: Number(e.target.value) })}
          />
        </label>
        <label className="block">
          <span className="mb-1.5 block text-sm">批大小</span>
          <Input type="number" value={String(form.batch_size)} onChange={(e) => setForm({ ...form, batch_size: Number(e.target.value) })} />
        </label>
        <label className="block">
          <span className="mb-1.5 block text-sm">重试</span>
          <Input type="number" value={String(form.retry)} onChange={(e) => setForm({ ...form, retry: Number(e.target.value) })} />
        </label>
      </div>

      <div className="rounded-lg bg-lumi-bg px-3 py-2 text-xs text-lumi-muted">
        API Key 使用应用密钥加密存储，不会返回浏览器、不会写入日志；发送给 AI 的内容仅含脱敏后的商户名与商品名。
      </div>

      {notice ? <div className="rounded-lg border border-lumi-good/30 bg-lumi-good/10 px-3 py-2 text-sm text-lumi-good">{notice}</div> : null}
      {error ? <ErrorNote message={error} /> : null}

      <div className="flex flex-wrap justify-end gap-2">
        <Button variant="secondary" onClick={fetchModels} disabled={busy}>
          拉取模型列表
        </Button>
        <Button variant="secondary" onClick={testConnection} disabled={busy}>
          测试连接
        </Button>
        <Button onClick={save} disabled={busy}>
          {busy ? "处理中…" : "保存"}
        </Button>
      </div>
    </Card>
  );
}

function AccountSettings() {
  const { data, isLoading } = useQuery({
    queryKey: ["accounts"],
    queryFn: () =>
      api.get<{
        mappings: Array<{ id?: string; source: string; tail: string; display_name: string; yimu_account: string; id_prefix: string }>;
        owner_names: string[];
        ledger_name: string;
      }>("/api/v1/settings/accounts"),
  });
  const [form, setForm] = useState<typeof data | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    if (data && form === null) setForm(data);
  }, [data, form]);

  if (isLoading || !form) {
    return (
      <div className="flex justify-center py-10">
        <Spinner />
      </div>
    );
  }

  const save = async () => {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await api.put("/api/v1/settings/accounts", {
        mappings: form.mappings,
        owner_names: form.owner_names,
        ledger_name: form.ledger_name,
      });
      setNotice("已保存");
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "保存失败");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card className="max-w-2xl space-y-4 p-5">
      <p className="text-sm text-lumi-muted">
        银行卡尾号与显示名称完全由你配置；这些信息只保存在你的数据库里，不会进入源码。
      </p>
      {form.mappings.map((mapping, index) => (
        <div key={index} className="grid grid-cols-2 gap-2 sm:grid-cols-5">
          <Input
            value={mapping.source}
            onChange={(e) => {
              const mappings = [...form.mappings];
              mappings[index] = { ...mapping, source: e.target.value };
              setForm({ ...form, mappings });
            }}
            placeholder="银行（如 工商银行）"
          />
          <Input
            value={mapping.tail}
            onChange={(e) => {
              const mappings = [...form.mappings];
              mappings[index] = { ...mapping, tail: e.target.value };
              setForm({ ...form, mappings });
            }}
            placeholder="卡尾号"
          />
          <Input
            value={mapping.display_name}
            onChange={(e) => {
              const mappings = [...form.mappings];
              mappings[index] = { ...mapping, display_name: e.target.value };
              setForm({ ...form, mappings });
            }}
            placeholder="显示名称"
          />
          <Input
            value={mapping.yimu_account}
            onChange={(e) => {
              const mappings = [...form.mappings];
              mappings[index] = { ...mapping, yimu_account: e.target.value };
              setForm({ ...form, mappings });
            }}
            placeholder="一木账户（可选）"
          />
          <div className="flex items-center gap-1">
            <Input
              value={mapping.id_prefix}
              onChange={(e) => {
                const mappings = [...form.mappings];
                mappings[index] = { ...mapping, id_prefix: e.target.value };
                setForm({ ...form, mappings });
              }}
              placeholder="ID 前缀"
            />
            <button
              type="button"
              className="rounded-lg p-1.5 text-lumi-muted hover:bg-lumi-bad/10 hover:text-lumi-bad"
              aria-label="删除此映射"
              onClick={() => setForm({ ...form, mappings: form.mappings.filter((_, i) => i !== index) })}
            >
              <Trash2 className="h-4 w-4" />
            </button>
          </div>
        </div>
      ))}
      <Button
        variant="secondary"
        size="sm"
        onClick={() =>
          setForm({
            ...form,
            mappings: [...form.mappings, { source: "", tail: "", display_name: "", yimu_account: "", id_prefix: "" }],
          })
        }
      >
        <Plus className="h-4 w-4" aria-hidden />
        添加银行卡
      </Button>

      <label className="block">
        <span className="mb-1.5 block text-sm">本人姓名（用于内部转账识别，可多个，用逗号分隔）</span>
        <Input
          value={form.owner_names.join(", ")}
          onChange={(e) => setForm({ ...form, owner_names: e.target.value.split(/[,，]/).map((s) => s.trim()).filter(Boolean) })}
        />
      </label>
      <label className="block">
        <span className="mb-1.5 block text-sm">默认账本名称</span>
        <Input value={form.ledger_name} onChange={(e) => setForm({ ...form, ledger_name: e.target.value })} />
      </label>

      {notice ? <div className="rounded-lg border border-lumi-good/30 bg-lumi-good/10 px-3 py-2 text-sm text-lumi-good">{notice}</div> : null}
      {error ? <ErrorNote message={error} /> : null}
      <div className="flex justify-end">
        <Button onClick={save} disabled={busy}>
          {busy ? "保存中…" : "保存"}
        </Button>
      </div>
    </Card>
  );
}

function PrivacySettings() {
  const queryClient = useQueryClient();
  const { data, isLoading } = useQuery({
    queryKey: ["privacy"],
    queryFn: () =>
      api.get<{
        raw_file_retention: string;
        ai_cache_count: number;
        ai_enabled: boolean;
        ai_key_saved: boolean;
        ai_fields_sent: string[];
      }>("/api/v1/settings/privacy"),
  });
  const [retention, setRetention] = useState<string | null>(null);
  const [notice, setNotice] = useState("");

  if (isLoading || !data) {
    return (
      <div className="flex justify-center py-10">
        <Spinner />
      </div>
    );
  }

  const value = retention ?? data.raw_file_retention;

  const save = async () => {
    await api.put("/api/v1/settings/privacy", { raw_file_retention: value });
    setNotice("已保存");
    queryClient.invalidateQueries({ queryKey: ["privacy"] });
  };

  const clearCache = async () => {
    await api.delete("/api/v1/settings/ai-cache");
    setNotice("AI 缓存已清空");
    queryClient.invalidateQueries({ queryKey: ["privacy"] });
  };

  const downloadBackup = async () => {
    const backup = await api.get<Record<string, unknown>>("/api/v1/settings/backup");
    const blob = new Blob([JSON.stringify(backup, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = "tabledger_backup.json";
    anchor.click();
    URL.revokeObjectURL(url);
  };

  return (
    <Card className="max-w-2xl space-y-4 p-5">
      <label className="block">
        <span className="mb-1.5 block text-sm font-medium">原始账单文件保留</span>
        <Select value={value} onChange={(e) => setRetention(e.target.value)}>
          <option value="immediate">处理完成立即删除（推荐）</option>
          <option value="1h">1 小时</option>
          <option value="24h">24 小时</option>
          <option value="7d">7 天</option>
        </Select>
      </label>
      <ul className="space-y-1 rounded-lg bg-lumi-bg px-3 py-2 text-xs text-lumi-muted">
        <li>· 原始上传与解压明文在任务结束后按上述策略删除</li>
        <li>· 账单密码永不持久化，只存在于处理过程的内存中</li>
        <li>
          · AI：{data.ai_enabled ? "已启用" : "已关闭"}；发送字段仅含脱敏后的商户/商品/收支方向
        </li>
        <li>· API Key 已保存：{data.ai_key_saved ? "是（加密存储）" : "否"}</li>
      </ul>
      <div className="flex items-center gap-2 text-sm">
        <ShieldCheck className="h-4 w-4 text-lumi-good" aria-hidden />
        AI 缓存：{data.ai_cache_count} 条
        <Button variant="ghost" size="sm" onClick={clearCache}>
          清空缓存
        </Button>
      </div>
      {notice ? <div className="rounded-lg border border-lumi-good/30 bg-lumi-good/10 px-3 py-2 text-sm text-lumi-good">{notice}</div> : null}
      <div className="flex justify-between">
        <Button variant="secondary" onClick={downloadBackup}>
          导出配置备份
        </Button>
        <Button onClick={save}>保存</Button>
      </div>
    </Card>
  );
}

function SecuritySettings() {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const { data: session } = useQuery({
    queryKey: ["session"],
    queryFn: () => api.get<{ must_change_password: boolean }>("/api/v1/auth/session"),
  });

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await api.post("/api/v1/auth/change-password", { current_password: current, new_password: next });
      setCurrent("");
      setNext("");
      setNotice("密码已修改，其他会话已全部失效");
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "修改失败");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card className="max-w-md space-y-4 p-5">
      {session?.must_change_password ? (
        <div className="flex items-start gap-2 rounded-lg border border-lumi-warn/40 bg-lumi-warn/10 px-3 py-2 text-sm text-lumi-warn">
          <KeyRound className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
          当前使用临时弱密码（初始引导密码），请立即修改。
        </div>
      ) : null}
      <form onSubmit={submit} className="space-y-3">
        <label className="block">
          <span className="mb-1.5 block text-sm">当前密码</span>
          <Input type="password" value={current} onChange={(e) => setCurrent(e.target.value)} autoComplete="current-password" required />
        </label>
        <label className="block">
          <span className="mb-1.5 block text-sm">新密码（至少 10 位）</span>
          <Input type="password" value={next} onChange={(e) => setNext(e.target.value)} autoComplete="new-password" minLength={10} required />
        </label>
        {notice ? <div className="rounded-lg border border-lumi-good/30 bg-lumi-good/10 px-3 py-2 text-sm text-lumi-good">{notice}</div> : null}
        {error ? <ErrorNote message={error} /> : null}
        <Button type="submit" disabled={busy}>
          {busy ? "提交中…" : "修改密码"}
        </Button>
      </form>
    </Card>
  );
}
