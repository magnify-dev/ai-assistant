import { useEffect, useState } from "react";
import { cn } from "@/lib/utils";
import { apiFetch } from "@/lib/api";
import type { ProjectsRegistry, RegisteredProject } from "@/lib/projectTypes";

type Props = {
  projectPath: string;
  onSelect: (path: string, settings?: RegisteredProject["settings"]) => void;
  onSaveCurrent: () => void;
};

export function ProjectSelector({ projectPath, onSelect, onSaveCurrent }: Props) {
  const [registry, setRegistry] = useState<ProjectsRegistry | null>(null);

  const refresh = () => {
    apiFetch("/api/projects")
      .then((r) => r.json())
      .then((data: ProjectsRegistry) => setRegistry(data))
      .catch(() => setRegistry(null));
  };

  useEffect(() => {
    refresh();
  }, []);

  const active = registry?.projects.find((p) => p.id === registry.activeProjectId);

  const activate = async (id: string) => {
    const res = await apiFetch("/api/projects/active", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id }),
    });
    if (res.ok) {
      const project = (await res.json()) as RegisteredProject;
      onSelect(project.path, project.settings);
      refresh();
    }
  };

  const remove = async (id: string) => {
    await apiFetch(`/api/projects/${id}`, { method: "DELETE" });
    refresh();
  };

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          onClick={() => {
            onSaveCurrent();
            refresh();
          }}
          className="rounded-md border border-white/20 px-2 py-1 text-xs text-white/90"
        >
          Save project
        </button>
        {active ? (
          <span className="self-center text-[10px] text-white/40">Active: {active.name}</span>
        ) : null}
      </div>
      {registry?.projects.length ? (
        <ul className="max-h-32 space-y-1 overflow-auto">
          {registry.projects.map((p) => (
            <li
              key={p.id}
              className={cn(
                "flex items-center justify-between gap-2 rounded border px-2 py-1.5 text-xs",
                p.id === registry.activeProjectId
                  ? "border-violet-400/40 bg-violet-500/10"
                  : "border-white/5 bg-black/20",
              )}
            >
              <button type="button" className="min-w-0 flex-1 truncate text-left" onClick={() => activate(p.id)}>
                <span className="font-medium text-white/90">{p.name}</span>
                <span className="block truncate font-mono text-[10px] text-white/40">{p.path}</span>
              </button>
              <button
                type="button"
                onClick={() => remove(p.id)}
                className="shrink-0 text-white/30 hover:text-red-300/80"
                title="Remove from registry"
              >
                ×
              </button>
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-xs text-white/40">
          No saved projects. Set a path and click Save project — settings persist across runs.
        </p>
      )}
      {projectPath ? (
        <p className="font-mono text-[10px] text-white/30 truncate">{projectPath}</p>
      ) : null}
    </div>
  );
}
