import { ChevronDown, ChevronRight, Compass, GitBranch, Map } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { cn } from "@/lib/utils";
import { apiFetch } from "@/lib/api";

type TreeNode = {
  path: string;
  title?: string;
  children?: TreeNode[];
};

type NavRoute = {
  path?: string;
  title?: string;
  interactables?: { kind?: string; text?: string; href?: string; reaches?: string }[];
  verified_reaches?: Record<string, { via?: { text?: string; href?: string } }>;
};

type PageEntry = {
  path?: string;
  title?: string;
  features?: string[];
  content?: { contains?: string[]; summary?: string; features?: string[] };
};

type ExplorationData = {
  navigation?: {
    tree?: TreeNode[];
    routes?: Record<string, NavRoute>;
    global_nav?: { text?: string; href?: string }[];
  };
  pages?: Record<string, PageEntry>;
  updated_at?: string;
};

type Props = {
  projectPath: string;
  compact?: boolean;
};

function TreeBranch({ node, depth = 0 }: { node: TreeNode; depth?: number }) {
  const [open, setOpen] = useState(depth < 2);
  const children = node.children ?? [];
  return (
    <li>
      <button
        type="button"
        className="flex w-full items-center gap-2 rounded px-2 py-1 text-left hover:bg-white/5"
        style={{ paddingLeft: `${depth * 12 + 8}px` }}
        onClick={() => children.length && setOpen((value) => !value)}
      >
        {children.length ? (
          open ? <ChevronDown className="size-3 shrink-0 text-white/40" /> : <ChevronRight className="size-3 shrink-0 text-white/40" />
        ) : (
          <span className="size-3 shrink-0" />
        )}
        <span className="font-medium text-white/85">{node.path}</span>
        {node.title && node.title !== node.path ? <span className="truncate text-white/50">— {node.title}</span> : null}
      </button>
      {open && children.length ? (
        <ul className="space-y-0.5">
          {children.map((child) => (
            <TreeBranch key={child.path} node={child} depth={depth + 1} />
          ))}
        </ul>
      ) : null}
    </li>
  );
}

export function ExplorationPanel({ projectPath, compact }: Props) {
  const [tab, setTab] = useState<"tree" | "pages">("tree");
  const [exploration, setExploration] = useState<ExplorationData | null>(null);
  const [expandedPages, setExpandedPages] = useState<Record<string, boolean>>({});
  const [lastDecision, setLastDecision] = useState<{ action?: string; reason?: string } | null>(null);

  const loadExploration = useCallback(() => {
    if (!projectPath.trim()) return;
    apiFetch(`/api/project/exploration?path=${encodeURIComponent(projectPath)}`)
      .then((r) => r.json())
      .then((data: { exploration?: ExplorationData | null }) => {
        if (data.exploration && typeof data.exploration === "object") {
          setExploration(data.exploration);
        }
      })
      .catch(() => setExploration(null));
  }, [projectPath]);

  useEffect(() => {
    loadExploration();
  }, [loadExploration]);

  useEffect(() => {
    const handler = (event: Event) => {
      const detail = (event as CustomEvent).detail as
        | {
            type?: string;
            routes?: Record<string, NavRoute>;
            global_nav?: { text?: string; href?: string }[];
            pages?: Record<string, PageEntry>;
            action?: string;
            reason?: string;
          }
        | undefined;
      if (!detail) return;
      if (detail.type === "nav_tree" && detail.routes) {
        setExploration((prev) => ({
          ...prev,
          navigation: {
            ...prev?.navigation,
            routes: detail.routes,
            global_nav: detail.global_nav ?? prev?.navigation?.global_nav,
          },
        }));
      }
      if (detail.type === "site_map" && detail.pages) {
        setExploration((prev) => ({ ...prev, pages: detail.pages }));
      }
      if (detail.type === "agent_decision") {
        setLastDecision({ action: detail.action, reason: detail.reason });
      }
    };
    window.addEventListener("test-runner-event", handler);
    return () => window.removeEventListener("test-runner-event", handler);
  }, []);

  const tree = exploration?.navigation?.tree ?? [];
  const routes = exploration?.navigation?.routes ?? {};
  const routeCount = Object.keys(routes).length;
  const pages = exploration?.pages ?? {};
  const pageEntries = Object.entries(pages).sort(([a], [b]) => a.localeCompare(b));

  return (
    <section className={compact ? "space-y-2" : "rounded-lg border border-white/10 bg-white/5 p-4"}>
      {!compact ? (
        <div className="mb-3 flex items-center justify-between gap-2">
          <h2 className="flex items-center gap-2 text-sm font-semibold text-white/90">
            <Compass className="size-4 text-emerald-300" />
            Exploration map
          </h2>
          <span className="text-xs text-white/50">{routeCount} route(s) · {pageEntries.length} page(s)</span>
        </div>
      ) : null}
      {!compact ? (
        <p className="mb-3 text-xs leading-relaxed text-white/55">
          How to move through the app and what lives on each URL. Saved to{" "}
          <code className="text-white/70">.agent/exploration.yaml</code>.
        </p>
      ) : null}

      <div className="mb-2 flex gap-2">
        <button
          type="button"
          onClick={() => setTab("tree")}
          className={cn(
            "rounded px-2 py-1 text-xs",
            tab === "tree" ? "bg-white/15 text-white" : "text-white/50 hover:text-white/80",
          )}
        >
          <span className="inline-flex items-center gap-1">
            <GitBranch className="size-3" />
            Navigation
          </span>
        </button>
        <button
          type="button"
          onClick={() => setTab("pages")}
          className={cn(
            "rounded px-2 py-1 text-xs",
            tab === "pages" ? "bg-white/15 text-white" : "text-white/50 hover:text-white/80",
          )}
        >
          <span className="inline-flex items-center gap-1">
            <Map className="size-3" />
            Pages
          </span>
        </button>
      </div>

      {lastDecision?.action && tab === "pages" ? (
        <div className="mb-2 rounded border border-violet-500/30 bg-violet-950/20 px-3 py-2 text-xs">
          <span className="font-medium text-violet-200">Last decision:</span>{" "}
          <span className="text-white/80">{lastDecision.action}</span>
          {lastDecision.reason ? <span className="text-white/60"> — {lastDecision.reason}</span> : null}
        </div>
      ) : null}

      {tab === "tree" ? (
        tree.length > 0 ? (
          <ul className="max-h-64 space-y-0.5 overflow-y-auto text-xs">
            {tree.map((node) => (
              <TreeBranch key={node.path} node={node} />
            ))}
          </ul>
        ) : routeCount > 0 ? (
          <ul className="max-h-64 space-y-1 overflow-y-auto text-xs">
            {Object.entries(routes)
              .sort(([a], [b]) => a.localeCompare(b))
              .map(([path, info]) => (
                <li key={path} className="rounded border border-white/5 bg-black/20 px-3 py-2 text-white/75">
                  <div className="font-medium text-white/85">{path}</div>
                  <div className="text-white/50">{info.title || "—"}</div>
                </li>
              ))}
          </ul>
        ) : (
          <p className="text-xs text-white/45">No routes explored yet — run with a task to build the map.</p>
        )
      ) : pageEntries.length === 0 ? (
        <p className="text-xs text-white/45">No pages cataloged yet.</p>
      ) : (
        <ul className="max-h-64 space-y-2 overflow-y-auto text-xs">
          {pageEntries.map(([path, info]) => {
            const open = expandedPages[path] ?? path === pageEntries[0]?.[0];
            const content = info.content ?? {};
            const contains = content.contains ?? [];
            const summary = content.summary || "";
            const features = info.features ?? content.features ?? [];
            return (
              <li key={path} className="rounded border border-white/5 bg-black/20">
                <button
                  type="button"
                  className="flex w-full items-start gap-2 px-3 py-2 text-left hover:bg-white/5"
                  onClick={() => setExpandedPages((prev) => ({ ...prev, [path]: !open }))}
                >
                  {open ? (
                    <ChevronDown className="mt-0.5 size-3.5 shrink-0 text-white/40" />
                  ) : (
                    <ChevronRight className="mt-0.5 size-3.5 shrink-0 text-white/40" />
                  )}
                  <div className="min-w-0 flex-1">
                    <div className="font-medium text-white/85">{path}</div>
                    <div className="truncate text-white/50">{summary || info.title || ""}</div>
                  </div>
                </button>
                {open ? (
                  <div className="border-t border-white/5 px-3 py-2 text-white/60">
                    {features.length > 0 ? <p className="mb-1 text-white/50">has: {features.join(", ")}</p> : null}
                    {contains.length > 0 ? (
                      <ul className="list-disc space-y-0.5 pl-4 text-white/70">
                        {contains.map((item, index) => (
                          <li key={index}>{item}</li>
                        ))}
                      </ul>
                    ) : (
                      <p className="text-white/45">No capability descriptions yet.</p>
                    )}
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
