import { ChevronDown, ChevronRight, GitBranch } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { apiFetch } from "@/lib/api";

type NavInteractable = {
  kind?: string;
  text?: string;
  href?: string;
  reaches?: string;
};

type NavRoute = {
  path?: string;
  title?: string;
  interactables?: NavInteractable[];
  verified_reaches?: Record<string, { via?: NavInteractable }>;
};

type NavTreeData = {
  routes?: Record<string, NavRoute>;
  global_nav?: NavInteractable[];
  edges?: { from?: string; to?: string; via?: NavInteractable }[];
  updated_at?: string;
};

export function NavTreePanel({ projectPath, compact }: { projectPath: string; compact?: boolean }) {
  const [navTree, setNavTree] = useState<NavTreeData | null>(null);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});

  const loadNavTree = useCallback(() => {
    if (!projectPath.trim()) return;
    apiFetch(`/api/project/nav-tree?path=${encodeURIComponent(projectPath)}`)
      .then((r) => r.json())
      .then((data: { navTree?: NavTreeData | null }) => {
        if (data.navTree && typeof data.navTree === "object") {
          setNavTree(data.navTree as NavTreeData);
        }
      })
      .catch(() => {});
  }, [projectPath]);

  useEffect(() => {
    loadNavTree();
  }, [loadNavTree]);

  useEffect(() => {
    const handler = (event: Event) => {
      const detail = (event as CustomEvent).detail as
        | { type?: string; routes?: Record<string, NavRoute>; global_nav?: NavInteractable[] }
        | undefined;
      if (!detail) return;
      if (detail.type === "nav_tree" && detail.routes) {
        setNavTree((prev) => ({
          ...prev,
          routes: detail.routes,
          global_nav: detail.global_nav ?? prev?.global_nav,
        }));
      }
    };
    window.addEventListener("test-runner-event", handler);
    return () => window.removeEventListener("test-runner-event", handler);
  }, []);

  const routes = navTree?.routes ?? {};
  const routeEntries = Object.entries(routes).sort(([a], [b]) => a.localeCompare(b));
  const globalNav = navTree?.global_nav ?? [];

  return (
    <section className={compact ? "" : "rounded-lg border border-white/10 bg-white/5 p-4"}>
      {!compact ? (
        <>
          <div className="mb-3 flex items-center justify-between gap-2">
            <h2 className="flex items-center gap-2 text-sm font-semibold text-white/90">
              <GitBranch className="size-4 text-emerald-300" />
              Navigation tree
            </h2>
            <span className="text-xs text-white/50">{routeEntries.length} route(s)</span>
          </div>
          <p className="mb-3 text-xs leading-relaxed text-white/55">
            Explored interactables and verified routes — how to navigate the app. Saved to{" "}
            <code className="text-white/70">.agent/cheatsheet-navigation.yaml</code>.
          </p>
        </>
      ) : null}
      {globalNav.length > 0 ? (
        <div className="mb-3 rounded border border-white/5 bg-black/20 px-3 py-2 text-xs text-white/65">
          <span className="text-white/40">Global nav:</span>{" "}
          {globalNav
            .map((el) => (el.text && el.href ? `${el.text}→${el.href}` : el.text || el.href))
            .filter(Boolean)
            .join(" · ")}
        </div>
      ) : null}
      {routeEntries.length === 0 ? (
        <p className="text-xs text-white/45">No routes explored yet.</p>
      ) : (
        <ul className="max-h-64 space-y-2 overflow-y-auto text-xs">
          {routeEntries.map(([path, info]) => {
            const open = expanded[path] ?? false;
            const links = (info.interactables ?? []).filter((el) => el.kind === "link");
            const actions = (info.interactables ?? []).filter((el) => el.kind !== "link");
            const verified = Object.entries(info.verified_reaches ?? {});
            return (
              <li key={path} className="rounded border border-white/5 bg-black/20">
                <button
                  type="button"
                  className="flex w-full items-start gap-2 px-3 py-2 text-left hover:bg-white/5"
                  onClick={() => setExpanded((prev) => ({ ...prev, [path]: !open }))}
                >
                  {open ? (
                    <ChevronDown className="mt-0.5 size-3.5 shrink-0 text-white/40" />
                  ) : (
                    <ChevronRight className="mt-0.5 size-3.5 shrink-0 text-white/40" />
                  )}
                  <div className="min-w-0 flex-1">
                    <div className="font-medium text-white/85">{path}</div>
                    <div className="truncate text-white/50">{info.title || ""}</div>
                  </div>
                  <span className="shrink-0 text-white/40">{info.interactables?.length ?? 0} el</span>
                </button>
                {open ? (
                  <div className="space-y-2 border-t border-white/5 px-3 py-2 text-white/60">
                    {links.length > 0 ? (
                      <div>
                        <p className="mb-0.5 text-white/40">Links</p>
                        <ul className="list-none space-y-0.5">
                          {links.slice(0, 12).map((el, i) => (
                            <li key={i}>
                              {el.text} → {el.reaches || el.href}
                            </li>
                          ))}
                        </ul>
                      </div>
                    ) : null}
                    {actions.length > 0 ? (
                      <div>
                        <p className="mb-0.5 text-white/40">Actions</p>
                        <p className="truncate">
                          {actions.map((el) => el.text || el.kind).filter(Boolean).join(" · ")}
                        </p>
                      </div>
                    ) : null}
                    {verified.length > 0 ? (
                      <div>
                        <p className="mb-0.5 text-white/40">Verified reaches</p>
                        <ul className="list-none space-y-0.5">
                          {verified.map(([dst, meta]) => (
                            <li key={dst}>
                              → {dst} via {meta.via?.text || meta.via?.href || "?"}
                            </li>
                          ))}
                        </ul>
                      </div>
                    ) : null}
                  </div>
                ) : null}
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
