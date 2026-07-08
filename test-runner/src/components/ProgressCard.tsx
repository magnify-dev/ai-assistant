import { ChevronDown, ChevronRight, Circle, Loader2, XCircle, CheckCircle2, AlertTriangle } from "lucide-react";
import { useState } from "react";
import { cn } from "@/lib/utils";

export type ProgressStatus = "idle" | "running" | "done" | "failed" | "warning";

function StatusIcon({ status }: { status: ProgressStatus }) {
  if (status === "running") return <Loader2 className="size-4 shrink-0 animate-spin text-sky-300" />;
  if (status === "done") return <CheckCircle2 className="size-4 shrink-0 text-green-400" />;
  if (status === "failed") return <XCircle className="size-4 shrink-0 text-red-400" />;
  if (status === "warning") return <AlertTriangle className="size-4 shrink-0 text-amber-400" />;
  return <Circle className="size-4 shrink-0 text-white/25" />;
}

type Props = {
  title: string;
  status: ProgressStatus;
  summary?: string;
  children?: React.ReactNode;
  defaultOpen?: boolean;
  highlight?: boolean;
};

export function ProgressCard({ title, status, summary, children, defaultOpen = false, highlight }: Props) {
  const expandable = Boolean(children);
  const [open, setOpen] = useState(defaultOpen);

  return (
    <div
      className={cn(
        "rounded-md border px-3 py-2 transition-colors",
        status === "failed" && "border-red-500/30 bg-red-950/20",
        status === "done" && "border-green-500/25 bg-green-950/15",
        status === "running" && "border-sky-500/35 bg-sky-950/20",
        status === "warning" && "border-amber-500/30 bg-amber-950/15",
        status === "idle" && "border-white/8 bg-white/[0.03]",
        highlight && status === "idle" && "border-white/15",
      )}
    >
      <button
        type="button"
        disabled={!expandable}
        onClick={() => expandable && setOpen((v) => !v)}
        className={cn(
          "flex w-full items-start gap-2 text-left",
          expandable && "cursor-pointer hover:opacity-90",
          !expandable && "cursor-default",
        )}
      >
        <StatusIcon status={status} />
        <div className="min-w-0 flex-1">
          <div className="text-sm font-medium text-white/90">{title}</div>
          {summary ? <div className="mt-0.5 truncate text-xs text-white/55">{summary}</div> : null}
        </div>
        {expandable ? (
          open ? (
            <ChevronDown className="size-4 shrink-0 text-white/40" />
          ) : (
            <ChevronRight className="size-4 shrink-0 text-white/40" />
          )
        ) : null}
      </button>
      {expandable && open ? (
        <div className="mt-2 border-t border-white/10 pt-2 text-xs text-white/80">{children}</div>
      ) : null}
    </div>
  );
}

export function phaseToStatus(phase?: { status?: string }): ProgressStatus {
  const s = phase?.status;
  if (s === "running") return "running";
  if (s === "done") return "done";
  if (s === "failed") return "failed";
  if (s === "warning") return "warning";
  return "idle";
}
