import type { RunReport } from "@/lib/projectTypes";
import { cn } from "@/lib/utils";

type Props = {
  report: RunReport | null;
};

export function RunReportPanel({ report }: Props) {
  if (!report) {
    return (
      <p className="text-xs text-white/50">
        Run report from the current run appears here — requested vs executed vs pass/fail.
      </p>
    );
  }

  return (
    <details open className="rounded-md border border-white/10 bg-black/20">
      <summary className="cursor-pointer list-none px-3 py-2 marker:content-none">
        <div className="flex items-center justify-between gap-2">
          <span className="text-sm font-medium text-white/90">Run report</span>
          <span
            className={cn(
              "rounded-full px-2 py-0.5 text-[10px] font-medium",
              report.overall_ok ? "bg-green-500/20 text-green-200" : "bg-red-500/20 text-red-200",
            )}
          >
            {report.overall_ok ? "PASS" : "FAIL"}
          </span>
        </div>
      </summary>
      <div className="space-y-4 border-t border-white/10 px-3 py-3 text-xs">
        <section>
          <p className="mb-1 text-[10px] uppercase tracking-wide text-white/40">Requested</p>
          {report.requested.summary ? (
            <p className="text-white/85">{report.requested.summary}</p>
          ) : null}
          {report.requested.source_text ? (
            <p className="mt-1 whitespace-pre-wrap text-white/60">{report.requested.source_text}</p>
          ) : null}
          {report.requested.success_criteria.length > 0 ? (
            <ul className="mt-2 list-disc space-y-1 pl-4 text-white/75">
              {report.requested.success_criteria.map((c) => (
                <li key={c}>{c}</li>
              ))}
            </ul>
          ) : null}
          {report.requested.deliverables && report.requested.deliverables.length > 0 ? (
          <ul className="mt-2 list-disc space-y-1 pl-4 text-white/75">
            {report.requested.deliverables.map((d) => (
              <li key={d}>{d}</li>
            ))}
          </ul>
        ) : null}
        {(report.requested.intent_gaps?.length ?? 0) > 0 ? (
          <ul className="mt-2 list-disc space-y-1 pl-4 text-amber-200/90">
            {report.requested.intent_gaps!.map((g) => (
              <li key={g}>{g}</li>
            ))}
          </ul>
        ) : null}
        </section>

        <section>
          <p className="mb-1 text-[10px] uppercase tracking-wide text-white/40">Executed</p>
          {report.executed.length === 0 ? (
            <p className="text-white/50">No Playwright steps recorded.</p>
          ) : (
            <ul className="space-y-1">
              {report.executed.map((step, i) => (
                <li
                  key={`${step.action}-${step.target}-${i}`}
                  className={cn(
                    "rounded border px-2 py-1 font-mono text-[11px]",
                    step.ok ? "border-green-500/20 bg-green-500/5" : "border-red-500/20 bg-red-500/5",
                  )}
                >
                  {step.ok ? "✓" : "✗"} {step.action} {step.target}
                  {step.message ? ` — ${step.message}` : ""}
                </li>
              ))}
            </ul>
          )}
        </section>

        <section>
          <p className="mb-1 text-[10px] uppercase tracking-wide text-white/40">Success criteria</p>
          <ul className="space-y-1">
            {report.criteria_results.map((item) => (
              <li
                key={item.criterion}
                className={cn(
                  "rounded border px-2 py-1",
                  item.met === true
                    ? "border-green-500/20 text-green-200/90"
                    : item.met === false
                      ? "border-red-500/20 text-red-200/90"
                      : "border-white/10 text-white/60",
                )}
              >
                {item.met === true ? "✓" : item.met === false ? "✗" : "?"} {item.criterion}
                {item.note ? <span className="text-white/50"> — {item.note}</span> : null}
              </li>
            ))}
          </ul>
        </section>

        {report.ui_error ? (
          <p className="rounded border border-red-500/30 bg-red-500/10 px-2 py-1 text-red-200/90">
            {report.ui_error}
          </p>
        ) : null}
        {report.final_url ? (
          <p className="font-mono text-[11px] text-white/50">Final URL: {report.final_url}</p>
        ) : null}
      </div>
    </details>
  );
}
