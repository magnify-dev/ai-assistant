import { Loader2 } from "lucide-react";
import type { WebResearchWaitState, WebResearchWaitTone } from "@/lib/webResearchWait";
import { cn } from "@/lib/utils";

type Props = {
  wait: WebResearchWaitState;
  compact?: boolean;
  className?: string;
};

function toneStyles(tone: WebResearchWaitTone): { border: string; bg: string; spinner: string; label: string } {
  switch (tone) {
    case "acting":
      return {
        border: "border-sky-400/35",
        bg: "bg-sky-500/10",
        spinner: "text-sky-300",
        label: "text-sky-100",
      };
    case "waiting":
      return {
        border: "border-amber-400/30",
        bg: "bg-amber-500/10",
        spinner: "text-amber-300",
        label: "text-amber-100",
      };
    case "failed":
      return {
        border: "border-rose-400/35",
        bg: "bg-rose-500/10",
        spinner: "text-rose-300",
        label: "text-rose-100",
      };
    case "blocked":
      return {
        border: "border-orange-400/35",
        bg: "bg-orange-500/10",
        spinner: "text-orange-300",
        label: "text-orange-100",
      };
    case "complete":
      return {
        border: "border-emerald-400/30",
        bg: "bg-emerald-500/10",
        spinner: "text-emerald-300",
        label: "text-emerald-100",
      };
    default:
      return {
        border: "border-violet-400/30",
        bg: "bg-violet-500/10",
        spinner: "text-violet-300",
        label: "text-violet-100",
      };
  }
}

export function OperationWaitBanner({ wait, compact = false, className }: Props) {
  const styles = toneStyles(wait.tone);
  const showSpinner = wait.tone !== "complete";

  return (
    <div
      className={cn(
        "rounded-lg border px-3 py-2.5",
        styles.border,
        styles.bg,
        compact ? "text-xs" : "text-sm",
        className,
      )}
    >
      <div className="flex items-start gap-2.5">
        {showSpinner ? (
          <Loader2 className={cn("mt-0.5 size-4 shrink-0 animate-spin", styles.spinner)} />
        ) : (
          <span className={cn("mt-1 size-2.5 shrink-0 rounded-full bg-emerald-300", styles.spinner)} />
        )}
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <p className={cn("font-semibold", styles.label)}>{wait.label}</p>
            {wait.step && wait.maxSteps ? (
              <span className="rounded-full bg-black/25 px-2 py-0.5 text-[10px] text-white/60">
                {wait.step}/{wait.maxSteps}
              </span>
            ) : null}
            {wait.action && wait.targetLabel ? (
              <span className="rounded-full bg-black/25 px-2 py-0.5 font-mono text-[10px] text-white/70">
                {wait.action} #{wait.targetId ?? wait.targetLabel}
              </span>
            ) : null}
          </div>
          <p className={cn("mt-0.5 leading-snug text-white/75", compact && "text-xs")}>{wait.message}</p>
          {wait.url ? (
            <p className="mt-1 truncate font-mono text-[10px] text-white/40">{wait.url}</p>
          ) : null}
        </div>
      </div>
    </div>
  );
}
