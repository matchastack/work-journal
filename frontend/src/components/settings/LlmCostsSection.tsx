import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useId, useState, type ReactNode } from "react";

import { llmCostsQuery, type CostLine } from "../../api/settings";
import { count, monthTitle, usd } from "../../format";
import { BUTTON, INPUT, LABEL } from "../ui";
import { HINT } from "./form";
import { SettingsSection } from "./SettingsSection";

/** A task or tier as words: `fact_extraction` as `Fact extraction`, `heavy` as `Heavy`. */
function named(key: string): string {
  const words = key.replaceAll("_", " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

function calls(n: number): string {
  return `${count(n)} ${n === 1 ? "call" : "calls"}`;
}

const NUMBER = "px-2 py-1.5 text-right tabular-nums";
const HEADER = "px-2 py-1.5 font-medium";
const FIGURES = ["Calls", "Input tokens", "Cached input tokens", "Output tokens", "Estimated cost"];

/** The figures every row has (`FIGURES`): calls, tokens and the estimated cost. */
function Figures({ line }: { line: CostLine }) {
  return (
    <>
      <td className={NUMBER}>{count(line.calls)}</td>
      <td className={NUMBER}>{count(line.inputTokens)}</td>
      <td className={NUMBER}>{count(line.cacheReadTokens + line.cacheWriteTokens)}</td>
      <td className={NUMBER}>{count(line.outputTokens)}</td>
      <td className={NUMBER}>{usd(line.costUsd)}</td>
    </>
  );
}

function Table({
  caption,
  labels,
  children,
  footer,
}: {
  caption: string;
  labels: string[];
  children: ReactNode;
  footer?: ReactNode;
}) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-max text-sm">
        <caption className="mb-1 text-left text-xs font-medium text-stone-700">{caption}</caption>
        <thead>
          <tr className="border-b border-stone-200 text-xs text-stone-600">
            {labels.map((label) => (
              <th key={label} scope="col" className={`${HEADER} text-left`}>
                {label}
              </th>
            ))}
            {FIGURES.map((label) => (
              <th key={label} scope="col" className={`${HEADER} text-right`}>
                {label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-stone-100">{children}</tbody>
        {footer}
      </table>
    </div>
  );
}

/**
 * What the owner's LLM calls cost in a month (NFR-COST-2), estimated from each tier's price, by
 * task and by tier, with earlier months to choose from.
 */
export function LlmCostsSection() {
  const [month, setMonth] = useState<string | null>(null);
  const costs = useQuery({ ...llmCostsQuery(month), placeholderData: keepPreviousData });
  const id = useId();
  let body: ReactNode;
  if (costs.isPending) {
    body = (
      <p role="status" className="text-sm text-stone-600">
        Loading the costs...
      </p>
    );
  } else if (costs.isError) {
    body = (
      <div role="alert">
        <p className="text-sm text-stone-700">The costs didn&apos;t load.</p>
        <button type="button" onClick={() => void costs.refetch()} className={`mt-2 ${BUTTON}`}>
          Try again
        </button>
      </div>
    );
  } else {
    const shown = costs.data;
    const { total } = shown;
    body = (
      <div className="space-y-4">
        <div>
          <label htmlFor={`${id}-month`} className={LABEL}>
            Month
          </label>
          <select
            id={`${id}-month`}
            value={month ?? shown.month}
            onChange={(event) => {
              setMonth(event.target.value);
            }}
            className={`${INPUT} max-w-xs`}
          >
            {shown.months.map((key) => (
              <option key={key} value={key}>
                {monthTitle(key)}
              </option>
            ))}
          </select>
        </div>
        <div aria-live="polite" aria-busy={costs.isPlaceholderData} className="space-y-4">
          {total.calls === 0 ? (
            <p className="text-sm">No model calls in {monthTitle(shown.month)}.</p>
          ) : (
            <>
              <p className="text-sm">
                <span className="text-lg font-semibold">{usd(total.costUsd)}</span> estimated for{" "}
                {calls(total.calls)} in {monthTitle(shown.month)}.
              </p>
              {total.unpricedCalls > 0 && (
                <p className="rounded border border-amber-200 bg-amber-50 p-2 text-sm text-amber-950">
                  {total.unpricedCalls === 1
                    ? "1 call had no price for its tier when made, so the estimate leaves it out."
                    : `${calls(total.unpricedCalls)} had no price for their tier when made, so ` +
                      "the estimate leaves them out."}
                </p>
              )}
              <Table caption="By task" labels={["Task", "Tier"]}>
                {shown.byTask.map((line) => (
                  <tr key={`${line.task}-${line.tier}`}>
                    <th scope="row" className="px-2 py-1.5 text-left font-normal">
                      {named(line.task)}
                    </th>
                    <td className="px-2 py-1.5">{named(line.tier)}</td>
                    <Figures line={line} />
                  </tr>
                ))}
              </Table>
              <Table
                caption="By tier"
                labels={["Tier"]}
                footer={
                  <tfoot>
                    <tr className="border-t border-stone-300 font-medium">
                      <th scope="row" className="px-2 py-1.5 text-left">
                        All tiers
                      </th>
                      <Figures line={total} />
                    </tr>
                  </tfoot>
                }
              >
                {shown.byTier.map((line) => (
                  <tr key={line.tier}>
                    <th scope="row" className="px-2 py-1.5 text-left font-normal">
                      {named(line.tier)}
                    </th>
                    <Figures line={line} />
                  </tr>
                ))}
              </Table>
            </>
          )}
        </div>
      </div>
    );
  }
  return (
    <SettingsSection
      title="LLM costs"
      description={
        <>
          <p>
            What the model calls made for you cost each month: each call&apos;s tokens at its
            tier&apos;s price when it was made (<code>LLM_PRICE_HEAVY</code>,{" "}
            <code>LLM_PRICE_STANDARD</code> and <code>LLM_PRICE_LIGHT</code>).
          </p>
          <p className={HINT}>
            These are estimates; Anthropic&apos;s bill has the real cost. Months are in UTC.
          </p>
        </>
      }
    >
      {body}
    </SettingsSection>
  );
}
