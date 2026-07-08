import { useEffect, useState } from "react";
import { cn } from "@/lib/utils";
import { apiFetch } from "@/lib/api";
import type { LocalEnvStatus, ProjectBundle } from "@/lib/projectTypes";

type Props = {
  projectPath: string;
  onSaved?: () => void;
};

export function CheatsheetPanel({ projectPath, onSaved }: Props) {
  const [bundle, setBundle] = useState<ProjectBundle | null>(null);
  const [tab, setTab] = useState<"cheatsheet" | "spec">("cheatsheet");
  const [cheatsheet, setCheatsheet] = useState("");
  const [specName, setSpecName] = useState("");
  const [specContent, setSpecContent] = useState("");
  const [localEnv, setLocalEnv] = useState<LocalEnvStatus | null>(null);
  const [saving, setSaving] = useState(false);
  const [dirty, setDirty] = useState(false);

  const loadLocalEnv = () => {
    if (!projectPath.trim()) return;
    apiFetch(`/api/project/local-env?path=${encodeURIComponent(projectPath)}`)
      .then((r) => r.json())
      .then((data: LocalEnvStatus) => setLocalEnv(data))
      .catch(() => setLocalEnv(null));
  };

  const loadBundle = () => {
    if (!projectPath.trim()) return;
    apiFetch(`/api/project/bundle?path=${encodeURIComponent(projectPath)}`)
      .then((r) => r.json())
      .then((data: ProjectBundle) => {
        setBundle(data);
        setCheatsheet(data.cheatsheet || "");
        setDirty(false);
        const firstSpec = data.specs[0]?.name;
        if (firstSpec) {
          setSpecName(firstSpec);
          loadSpec(projectPath, firstSpec);
        }
      })
      .catch(() => setBundle(null));
  };

  const loadSpec = (path: string, name: string) => {
    apiFetch(`/api/project/spec?path=${encodeURIComponent(path)}&name=${encodeURIComponent(name)}`)
      .then((r) => r.json())
      .then((data: { content: string }) => setSpecContent(data.content ?? ""))
      .catch(() => setSpecContent(""));
  };

  useEffect(() => {
    loadBundle();
    loadLocalEnv();
  }, [projectPath]);

  useEffect(() => {
    if (specName && projectPath) loadSpec(projectPath, specName);
  }, [specName]);

  const saveCheatsheet = async () => {
    setSaving(true);
    try {
      const res = await apiFetch("/api/project/cheatsheet", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: projectPath, content: cheatsheet }),
      });
      if (res.ok) {
        setDirty(false);
        onSaved?.();
      }
    } finally {
      setSaving(false);
    }
  };

  if (!projectPath.trim()) {
    return <p className="text-xs text-white/50">Select a project to view run cheatsheets.</p>;
  }

  return (
    <div className="space-y-3">
      <div className="flex gap-2">
        <button
          type="button"
          onClick={() => setTab("cheatsheet")}
          className={cn(
            "rounded px-2 py-1 text-xs",
            tab === "cheatsheet" ? "bg-white/15 text-white" : "text-white/50 hover:text-white/80",
          )}
        >
          Run cheatsheet
        </button>
        <button
          type="button"
          onClick={() => setTab("spec")}
          className={cn(
            "rounded px-2 py-1 text-xs",
            tab === "spec" ? "bg-white/15 text-white" : "text-white/50 hover:text-white/80",
          )}
        >
          UI spec
        </button>
      </div>

      {tab === "cheatsheet" ? (
        <>
          {localEnv ? (
            <div
              className={cn(
                "rounded-md border px-3 py-2 text-xs",
                localEnv.ready ? "border-green-500/30 bg-green-950/20 text-green-200" : "border-amber-500/30 bg-amber-950/20 text-amber-200",
              )}
            >
              {localEnv.ready ? (
                <p>Local env ready — tests can run against {localEnv.local_base_url || "localhost"}.</p>
              ) : (
                <>
                  <p className="font-medium">Local env not ready</p>
                  <p className="mt-1 text-[11px] opacity-90">
                    Missing: {localEnv.missing.join(", ")}. Copy{" "}
                    <code className="text-white/90">{localEnv.env_example_path}</code> →{" "}
                    <code className="text-white/90">{localEnv.env_local_path}</code> and set DATABASE_URL
                    (from Railway dashboard or micro-services/admin/.env.example).
                  </p>
                </>
              )}
            </div>
          ) : null}
          <p className="text-[10px] text-white/40">
            How to run this project locally before Railway deploy. Edit once — the assistant reuses this every run.
          </p>
          <textarea
            className="min-h-48 w-full rounded-md border border-white/10 bg-black/30 px-3 py-2 font-mono text-xs leading-relaxed"
            value={cheatsheet}
            onChange={(e) => {
              setCheatsheet(e.target.value);
              setDirty(true);
            }}
          />
          <div className="flex items-center gap-2">
            <button
              type="button"
              disabled={!dirty || saving}
              onClick={saveCheatsheet}
              className="rounded-md border border-white/20 px-3 py-1 text-xs disabled:opacity-40"
            >
              {saving ? "Saving…" : "Save cheatsheet"}
            </button>
            <button type="button" onClick={loadBundle} className="text-xs text-white/50 hover:text-white/80">
              Reload
            </button>
          </div>
        </>
      ) : (
        <>
          {bundle?.specs.length ? (
            <select
              className="w-full rounded-md border border-white/10 bg-black/30 px-2 py-1.5 text-xs"
              value={specName}
              onChange={(e) => setSpecName(e.target.value)}
            >
              {bundle.specs.map((s) => (
                <option key={s.name} value={s.name}>
                  {s.name}
                </option>
              ))}
            </select>
          ) : (
            <p className="text-xs text-amber-300/80">No specs in .agent/specs/ yet.</p>
          )}
          <pre className="max-h-64 overflow-auto rounded-md border border-white/10 bg-black/30 p-3 font-mono text-xs leading-relaxed text-white/80">
            {specContent || "—"}
          </pre>
          <p className="text-[10px] text-white/40">
            State-based traversal tree — each node URL lists interactions and expected outcomes.
          </p>
        </>
      )}
    </div>
  );
}
