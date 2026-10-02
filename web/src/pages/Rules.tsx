import { useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, Trash2, Upload } from "lucide-react";
import { api } from "@/api";
import { Badge, Button, Card, ErrorNote, Input, Select, Spinner } from "@/components/ui";

interface Rule {
  id: number;
  match_key: string;
  direction: string;
  category: string;
  subcategory: string;
  tags: string;
  origin: string;
  enabled: boolean;
}

export function RulesPage() {
  const queryClient = useQueryClient();
  const { data, isLoading, error } = useQuery({
    queryKey: ["rules"],
    queryFn: () => api.get<{ rules: Rule[] }>("/api/v1/settings/rules"),
  });
  const { data: taxonomy } = useQuery({
    queryKey: ["taxonomy"],
    queryFn: () => api.get<{ expense: Record<string, string[]>; income: string[] }>("/api/v1/settings/taxonomy"),
  });
  const [key, setKey] = useState("");
  const [direction, setDirection] = useState("");
  const [category, setCategory] = useState("");
  const [subcategory, setSubcategory] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [importError, setImportError] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);

  const create = async () => {
    if (!key || !category) return;
    setBusy(true);
    try {
      await api.put("/api/v1/settings/rules", {
        match_key: key,
        direction,
        category,
        subcategory: subcategory || category,
      });
      setKey("");
      setCategory("");
      setSubcategory("");
      queryClient.invalidateQueries({ queryKey: ["rules"] });
    } finally {
      setBusy(false);
    }
  };

  const remove = async (id: number) => {
    await api.delete(`/api/v1/settings/rules/${id}`);
    queryClient.invalidateQueries({ queryKey: ["rules"] });
  };

  const importCsv = async (file: File) => {
    setImportError("");
    const form = new FormData();
    form.append("file", file);
    try {
      const result = await api.postForm<{ added: number }>("/api/v1/settings/rules/import", form);
      setNotice(`已导入 ${result.added} 条规则`);
      queryClient.invalidateQueries({ queryKey: ["rules"] });
    } catch (exc) {
      setImportError(exc instanceof Error ? exc.message : "导入失败");
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">分类规则</h1>
          <p className="text-sm text-lumi-muted">长键优先匹配；「收支」留空表示支出收入都适用</p>
        </div>
        <div className="flex gap-2">
          <input
            ref={fileRef}
            type="file"
            accept=".csv"
            className="hidden"
            onChange={(e) => {
              const file = e.target.files?.[0];
              if (file) importCsv(file);
              e.target.value = "";
            }}
          />
          <Button variant="secondary" onClick={() => fileRef.current?.click()}>
            <Upload className="h-4 w-4" aria-hidden />
            导入词典 CSV
          </Button>
          <a href="/api/v1/settings/rules/export">
            <Button variant="secondary">导出 CSV</Button>
          </a>
        </div>
      </div>

      {notice ? <div className="rounded-lg border border-lumi-good/30 bg-lumi-good/10 px-3 py-2 text-sm text-lumi-good">{notice}</div> : null}
      {importError ? <ErrorNote message={importError} /> : null}

      <Card className="p-4">
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-5">
          <Input value={key} onChange={(e) => setKey(e.target.value)} placeholder="匹配键（商户/商品）" className="col-span-2" />
          <Select value={direction} onChange={(e) => setDirection(e.target.value)} aria-label="收支">
            <option value="">收支通用</option>
            <option value="支出">仅支出</option>
            <option value="收入">仅收入</option>
          </Select>
          {direction === "收入" ? (
            <Select value={category} onChange={(e) => { setCategory(e.target.value); setSubcategory(""); }} aria-label="收入类别">
              <option value="">收入类别…</option>
              {(taxonomy?.income ?? []).map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
            </Select>
          ) : (
            <Select
              value={category}
              onChange={(e) => {
                setCategory(e.target.value);
                setSubcategory("");
              }}
              aria-label="类别"
            >
              <option value="">类别…</option>
              {taxonomy
                ? Object.keys(taxonomy.expense).map((c) => (
                    <option key={c} value={c}>
                      {c}
                    </option>
                  ))
                : null}
            </Select>
          )}
          {direction === "收入" ? null : (
            <Select value={subcategory} onChange={(e) => setSubcategory(e.target.value)} disabled={!category} aria-label="子类">
              <option value="">子类…</option>
              {(taxonomy?.expense[category] ?? []).map((sub) => (
                <option key={sub} value={sub}>
                  {sub}
                </option>
              ))}
            </Select>
          )}
        </div>
        <div className="mt-3 flex justify-end">
          <Button onClick={create} disabled={!key || !category || busy}>
            <Plus className="h-4 w-4" aria-hidden />
            添加规则
          </Button>
        </div>
      </Card>

      <Card>
        {isLoading ? (
          <div className="flex justify-center py-10">
            <Spinner />
          </div>
        ) : error ? (
          <div className="p-4">
            <ErrorNote message={error instanceof Error ? error.message : "加载失败"} />
          </div>
        ) : !data || data.rules.length === 0 ? (
          <div className="py-10 text-center text-sm text-lumi-muted">
            还没有规则。可以在这里添加，或在审核页勾选「记住规则」自动沉淀；旧版分类词典 CSV 也可以导入。
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[640px] text-sm">
              <thead>
                <tr className="border-b border-lumi-line text-left text-xs text-lumi-muted">
                  <th className="px-4 py-2 font-medium">匹配键</th>
                  <th className="px-4 py-2 font-medium">收支</th>
                  <th className="px-4 py-2 font-medium">类别 / 子类</th>
                  <th className="px-4 py-2 font-medium">来源</th>
                  <th className="px-4 py-2" />
                </tr>
              </thead>
              <tbody className="divide-y divide-lumi-line">
                {data.rules.map((rule) => (
                  <tr key={rule.id}>
                    <td className="px-4 py-2 font-medium">{rule.match_key}</td>
                    <td className="px-4 py-2 text-lumi-muted">{rule.direction || "通用"}</td>
                    <td className="px-4 py-2">
                      {rule.category}
                      {rule.subcategory ? <span className="text-lumi-muted"> / {rule.subcategory}</span> : null}
                    </td>
                    <td className="px-4 py-2">
                      <Badge tone={rule.origin === "user" ? "accent" : "neutral"}>{rule.origin}</Badge>
                    </td>
                    <td className="px-4 py-2 text-right">
                      <button
                        type="button"
                        onClick={() => remove(rule.id)}
                        className="rounded-lg p-1.5 text-lumi-muted hover:bg-lumi-bad/10 hover:text-lumi-bad"
                        aria-label={`删除规则 ${rule.match_key}`}
                      >
                        <Trash2 className="h-4 w-4" />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}
