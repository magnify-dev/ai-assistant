import { useEffect, useState } from "react";
import { cn } from "@/lib/utils";
import { apiFetch } from "@/lib/api";
import type { LocalEnvStatus, ProjectBundle } from "@/lib/projectTypes";

type Props = {
  projectPath: string;
  onSaved?: () => void;
  testTargetMode?: "local" | "deployed";
};

export function CheatsheetPanel({ projectPath, onSaved, testTargetMode = "local" }: Props) {
  const [cheatsheet, setCheatsheet] = useState("");
  const [localEnv, setLocalEnv] = useState<LocalEnvStatus | null>(null);
  const [localDev, setLocalDev] = useState<{ state?: Record<string, unknown> | null } | null>(null);
  const [learnings, setLearnings] = useState<{ insight?: string; at?: string; source?: string }[]>([]);
  const [saving, setSaving] = useState(false);
  const [dirty, setDirty] = useState(false);

  const loadLocalEnv = () => {
    if (!projectPath.trim()) return;
    apiFetch(`/api/project/local-env?path=${encodeURIComponent(projectPath)}`)
      .then((r) => r.json())
      .then((data: LocalEnvStatus) => setLocalEnv(data))
      .catch(() => setLocalEnv(null));
  };

  const loadLocalDev = () => {
    if (!projectPath.trim()) return;
    apiFetch(`/api/project/local-dev?path=${encodeURIComponent(projectPath)}`)
      .then((r) => r.json())
      .then((data) => setLocalDev(data))
      .catch(() => setLocalDev(null));
  };

  const loadLearnings = () => {
    if (!projectPath.trim()) return;
    apiFetch(`/api/project/learnings?path=${encodeURIComponent(projectPath)}`)
      .then((r) => r.json())
      .then((data: { entries?: { insight?: string; at?: string; source?: string }[] }) => {
        setLearnings(data.entries ?? []);
      })
      .catch(() => setLearnings([]));
  };

  const loadBundle = () => {
    if (!projectPath.trim()) return;
    apiFetch(`/api/project/bundle?path=${encodeURIComponent(projectPath)}`)
      .then((r) => r.json())
      .then((data: ProjectBundle) => {
        setCheatsheet(data.cheatsheet || "");
        setDirty(false);
      })
      .catch(() => setCheatsheet(""));
  };

  useEffect(() => {
    loadBundle();
    loadLocalEnv();
    loadLocalDev();
    loadLearnings();
  }, [projectPath]);

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
    return <p className="text-xs text-white/50">Select a project to edit run settings.</p>;
  }

  return (
    <div className="space-y-3">
      {localDev?.state ? (
        <div className="rounded-md border border-sky-500/30 bg-sky-950/20 px-3 py-2 text-xs text-sky-100">
          Local dev running in terminal(s) since{" "}
          {String((localDev.state as { started_at?: string }).started_at ?? "unknown")}. Close terminal windows to stop.
        </div>
      ) : null}
      {learnings.length > 0 ? (
        <div className="rounded-md border border-violet-500/20 bg-violet-950/15 px-3 py-2 text-xs">
          <p className="mb-1 font-medium text-violet-200/90">Run learnings (append-only)</p>
          <ul className="max-h-32 space-y-1 overflow-auto text-white/70">
            {learnings.map((entry, i) => (
              <li key={`${entry.insight}-${i}`}>• {entry.insight}</li>
            ))}
          </ul>
        </div>
      ) : null}
      {testTargetMode === "local" && localEnv ? (
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
                <code className="text-white/90">{localEnv.env_path ?? localEnv.env_local_path}</code> and set{" "}
                {localEnv.missing.join(", ")}.
              </p>
            </>
          )}
        </div>
      ) : null}
      <p className="text-[10px] text-white/40">
        How to start the project locally, deploy settings, and env files. Page exploration lives in{" "}
        <code className="text-white/60">.agent/exploration.yaml</code>.
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
        <button
          type="button"
          onClick={() => {
            loadBundle();
            loadLearnings();
            loadLocalDev();
          }}
          className="text-xs text-white/50 hover:text-white/80"
        >
          Reload
        </button>
      </div>
    </div>
  );
}
