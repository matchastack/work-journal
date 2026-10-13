import { useId, useRef, useState } from "react";

import { failureMessage } from "../../api/auth";
import {
  useEditFact,
  type FactEdit,
  type FactKind,
  type JournalEntry,
  type JournalFact,
  type Metric,
  type Ownership,
  type ProjectOption,
  type Qualifier,
  type RoleOption,
} from "../../api/journal";
import { BUTTON, INPUT, LABEL, PRIMARY_BUTTON, TEXT_BUTTON } from "../ui";
import { KIND_LABELS, OWNERSHIP_LABELS, QUALIFIER_LABELS } from "./labels";

type ValueKind = Metric["value"]["kind"];

const VALUE_KINDS: Record<ValueKind, { label: string; numbers: [string, string] }> = {
  single: { label: "One number", numbers: ["Value", ""] },
  change: { label: "A change", numbers: ["From", "To"] },
  range: { label: "A range", numbers: ["Low", "High"] },
};

interface MetricDraft {
  key: number;
  subject: string;
  kind: ValueKind;
  first: string;
  second: string;
  unit: string;
  qualifier: Qualifier;
}

interface Draft {
  statement: string;
  outcome: string;
  kind: FactKind;
  ownership: Ownership | "";
  date: string;
  tools: string;
  /** `role:<id>`, `new:<name>` or empty for none. */
  role: string;
  project: string;
  metrics: MetricDraft[];
}

function metricDraft(metric: Metric, key: number): MetricDraft {
  const { value } = metric;
  const [first, second] =
    value.kind === "single"
      ? [value.value, null]
      : value.kind === "change"
        ? [value.before, value.after]
        : [value.low, value.high];
  return {
    key,
    subject: metric.subject,
    kind: value.kind,
    first: String(first),
    second: second === null ? "" : String(second),
    unit: metric.unit ?? "",
    qualifier: metric.qualifier,
  };
}

function draftOf(fact: JournalFact): Draft {
  return {
    statement: fact.statement,
    outcome: fact.outcome ?? "",
    kind: fact.kind,
    ownership: fact.ownership ?? "",
    date: fact.date ?? "",
    tools: fact.tools.join(", "),
    role: fact.roleId ? `role:${fact.roleId}` : fact.proposedRole ? `new:${fact.proposedRole}` : "",
    project: fact.projectId ?? "",
    metrics: fact.metrics.map(metricDraft),
  };
}

function parseNumber(text: string): number | undefined {
  const value = Number(text.trim());
  return text.trim() === "" || !Number.isFinite(value) ? undefined : value;
}

/** The edit the draft makes, or what's wrong with it. */
function editOf(draft: Draft): { edit: FactEdit } | { problem: string } {
  if (draft.statement.trim() === "") {
    return { problem: "The fact needs a statement." };
  }
  if (draft.date !== "" && !/^\d{4}-(0[1-9]|1[0-2])$/.test(draft.date)) {
    return { problem: "The date needs to be a month, such as 2026-10." };
  }
  const metrics: Metric[] = [];
  for (const [index, metric] of draft.metrics.entries()) {
    const name = `Metric ${String(index + 1)}`;
    const first = parseNumber(metric.first);
    const second = parseNumber(metric.second);
    if (metric.subject.trim() === "") {
      return { problem: `${name} needs to say what was measured.` };
    }
    if (first === undefined || (metric.kind !== "single" && second === undefined)) {
      return {
        problem: `${name} needs ${metric.kind === "single" ? "a number" : "both numbers"}.`,
      };
    }
    let value: Metric["value"];
    if (metric.kind === "single" || second === undefined) {
      value = { kind: "single", value: first };
    } else if (metric.kind === "change") {
      value = { kind: "change", before: first, after: second };
    } else if (first > second) {
      return { problem: `${name}'s low number is above its high one.` };
    } else {
      value = { kind: "range", low: first, high: second };
    }
    metrics.push({
      subject: metric.subject.trim(),
      value,
      unit: metric.unit.trim() || null,
      qualifier: metric.qualifier,
    });
  }
  return {
    edit: {
      statement: draft.statement.trim(),
      kind: draft.kind,
      metrics,
      ownership: draft.ownership === "" ? null : draft.ownership,
      tools: draft.tools
        .split(",")
        .map((tool) => tool.trim())
        .filter((tool) => tool !== ""),
      outcome: draft.outcome.trim() || null,
      date: draft.date || null,
      roleId: draft.role.startsWith("role:") ? draft.role.slice("role:".length) : null,
      projectId: draft.project || null,
      proposedRole: draft.role.startsWith("new:") ? draft.role.slice("new:".length) : null,
    },
  };
}

function roleOptions(fact: JournalFact, entry: JournalEntry, roles: readonly RoleOption[]) {
  const options = roles.map((role) => ({ value: `role:${role.id}`, label: role.label }));
  if (fact.roleId && !roles.some((role) => role.id === fact.roleId)) {
    options.push({ value: `role:${fact.roleId}`, label: `${fact.roleId} (not in your profile)` });
  }
  for (const name of new Set([fact.proposedRole, entry.newRole])) {
    if (name) {
      options.push({ value: `new:${name}`, label: `New role: ${name}` });
    }
  }
  return [...options, { value: "", label: "No role" }];
}

function projectOptions(fact: JournalFact, projects: readonly ProjectOption[]) {
  const options = projects.map((project) => ({ value: project.id, label: project.name }));
  if (fact.projectId && !projects.some((project) => project.id === fact.projectId)) {
    options.push({ value: fact.projectId, label: `${fact.projectId} (not in your profile)` });
  }
  return [{ value: "", label: "No project" }, ...options];
}

/** Edit a fact's fields (FR-EXT-3). Escape cancels. */
export function FactForm({
  fact,
  entry,
  roles,
  projects,
  onDone,
}: {
  fact: JournalFact;
  entry: JournalEntry;
  roles: readonly RoleOption[];
  projects: readonly ProjectOption[];
  onDone: () => void;
}) {
  const [draft, setDraft] = useState(() => draftOf(fact));
  const [problem, setProblem] = useState<string | null>(null);
  const nextKey = useRef(fact.metrics.length);
  const save = useEditFact();
  const id = useId();
  const field = (name: string) => `${id}-${name}`;
  const change = (changes: Partial<Draft>) => {
    setDraft((current) => ({ ...current, ...changes }));
  };
  const changeMetric = (key: number, changes: Partial<MetricDraft>) => {
    change({
      metrics: draft.metrics.map((metric) =>
        metric.key === key ? { ...metric, ...changes } : metric,
      ),
    });
  };
  const cited = fact.citedBy.length;
  return (
    <form
      aria-label="Edit the fact"
      onSubmit={(event) => {
        event.preventDefault();
        const result = editOf(draft);
        if ("problem" in result) {
          setProblem(result.problem);
          return;
        }
        setProblem(null);
        save.mutate({ id: fact.id, edit: result.edit }, { onSuccess: onDone });
      }}
      onKeyDown={(event) => {
        if (event.key === "Escape") {
          onDone();
        }
      }}
      className="space-y-3"
    >
      <div>
        <label htmlFor={field("statement")} className={LABEL}>
          Statement
        </label>
        <textarea
          id={field("statement")}
          value={draft.statement}
          onChange={(event) => {
            change({ statement: event.target.value });
          }}
          rows={2}
          required
          autoFocus
          className={INPUT}
        />
      </div>
      <div>
        <label htmlFor={field("outcome")} className={LABEL}>
          Outcome
        </label>
        <input
          id={field("outcome")}
          value={draft.outcome}
          onChange={(event) => {
            change({ outcome: event.target.value });
          }}
          className={INPUT}
        />
      </div>
      <div className="grid gap-3 sm:grid-cols-3">
        <div>
          <label htmlFor={field("kind")} className={LABEL}>
            Kind
          </label>
          <select
            id={field("kind")}
            value={draft.kind}
            onChange={(event) => {
              change({ kind: event.target.value as FactKind });
            }}
            className={INPUT}
          >
            {Object.entries(KIND_LABELS).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label htmlFor={field("ownership")} className={LABEL}>
            Your part
          </label>
          <select
            id={field("ownership")}
            value={draft.ownership}
            onChange={(event) => {
              change({ ownership: event.target.value as Ownership | "" });
            }}
            className={INPUT}
          >
            <option value="">Not said</option>
            {Object.entries(OWNERSHIP_LABELS).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label htmlFor={field("date")} className={LABEL}>
            Month
          </label>
          <input
            id={field("date")}
            type="month"
            placeholder="2026-10"
            value={draft.date}
            onChange={(event) => {
              change({ date: event.target.value });
            }}
            className={INPUT}
          />
        </div>
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        <div>
          <label htmlFor={field("role")} className={LABEL}>
            Role
          </label>
          <select
            id={field("role")}
            value={draft.role}
            onChange={(event) => {
              change({ role: event.target.value });
            }}
            className={INPUT}
          >
            {roleOptions(fact, entry, roles).map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label htmlFor={field("project")} className={LABEL}>
            Project
          </label>
          <select
            id={field("project")}
            value={draft.project}
            onChange={(event) => {
              change({ project: event.target.value });
            }}
            className={INPUT}
          >
            {projectOptions(fact, projects).map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </div>
      </div>
      <div>
        <label htmlFor={field("tools")} className={LABEL}>
          Tools, separated by commas
        </label>
        <input
          id={field("tools")}
          value={draft.tools}
          onChange={(event) => {
            change({ tools: event.target.value });
          }}
          className={INPUT}
        />
      </div>
      <fieldset className="space-y-2">
        <legend className={LABEL}>Metrics</legend>
        {draft.metrics.map((metric, index) => {
          const name = `Metric ${String(index + 1)}`;
          const at = (part: string) => field(`metric-${String(metric.key)}-${part}`);
          const [firstLabel, secondLabel] = VALUE_KINDS[metric.kind].numbers;
          return (
            <fieldset key={metric.key} className="rounded border border-stone-200 bg-white p-2">
              <legend className="px-1 text-xs text-stone-600">{name}</legend>
              <div className="grid gap-2 sm:grid-cols-6">
                <div className="sm:col-span-3">
                  <label htmlFor={at("subject")} className={LABEL}>
                    What was measured
                  </label>
                  <input
                    id={at("subject")}
                    value={metric.subject}
                    onChange={(event) => {
                      changeMetric(metric.key, { subject: event.target.value });
                    }}
                    required
                    className={INPUT}
                  />
                </div>
                <div className="sm:col-span-3">
                  <label htmlFor={at("kind")} className={LABEL}>
                    Value
                  </label>
                  <select
                    id={at("kind")}
                    value={metric.kind}
                    onChange={(event) => {
                      changeMetric(metric.key, { kind: event.target.value as ValueKind });
                    }}
                    className={INPUT}
                  >
                    {Object.entries(VALUE_KINDS).map(([value, kind]) => (
                      <option key={value} value={value}>
                        {kind.label}
                      </option>
                    ))}
                  </select>
                </div>
                <div className="sm:col-span-2">
                  <label htmlFor={at("qualifier")} className={LABEL}>
                    Precision
                  </label>
                  <select
                    id={at("qualifier")}
                    value={metric.qualifier}
                    onChange={(event) => {
                      changeMetric(metric.key, { qualifier: event.target.value as Qualifier });
                    }}
                    className={INPUT}
                  >
                    {Object.entries(QUALIFIER_LABELS).map(([value, label]) => (
                      <option key={value} value={value}>
                        {label}
                      </option>
                    ))}
                  </select>
                </div>
                <div className={metric.kind === "single" ? "sm:col-span-2" : ""}>
                  <label htmlFor={at("first")} className={LABEL}>
                    {firstLabel}
                  </label>
                  <input
                    id={at("first")}
                    inputMode="decimal"
                    value={metric.first}
                    onChange={(event) => {
                      changeMetric(metric.key, { first: event.target.value });
                    }}
                    required
                    className={INPUT}
                  />
                </div>
                {metric.kind !== "single" && (
                  <div>
                    <label htmlFor={at("second")} className={LABEL}>
                      {secondLabel}
                    </label>
                    <input
                      id={at("second")}
                      inputMode="decimal"
                      value={metric.second}
                      onChange={(event) => {
                        changeMetric(metric.key, { second: event.target.value });
                      }}
                      required
                      className={INPUT}
                    />
                  </div>
                )}
                <div className="sm:col-span-2">
                  <label htmlFor={at("unit")} className={LABEL}>
                    Unit
                  </label>
                  <input
                    id={at("unit")}
                    placeholder="minutes, %, users"
                    value={metric.unit}
                    onChange={(event) => {
                      changeMetric(metric.key, { unit: event.target.value });
                    }}
                    className={INPUT}
                  />
                </div>
              </div>
              <button
                type="button"
                onClick={() => {
                  change({ metrics: draft.metrics.filter((other) => other.key !== metric.key) });
                }}
                className={`mt-2 ${TEXT_BUTTON}`}
              >
                Remove {name.toLowerCase()}
              </button>
            </fieldset>
          );
        })}
        <button
          type="button"
          onClick={() => {
            const key = nextKey.current++;
            change({
              metrics: [
                ...draft.metrics,
                {
                  key,
                  subject: "",
                  kind: "single",
                  first: "",
                  second: "",
                  unit: "",
                  qualifier: "exact",
                },
              ],
            });
          }}
          className={TEXT_BUTTON}
        >
          Add a metric
        </button>
      </fieldset>
      {cited > 0 && (
        <p className="text-xs text-stone-600">
          {cited === 1
            ? "A bullet in your profile cites this fact. It stays as it is"
            : `${String(cited)} bullets in your profile cite this fact. They stay as they are`}{" "}
          until your next refresh proposes changes.
        </p>
      )}
      {(problem !== null || save.isError) && (
        <p role="alert" className="text-sm text-red-800">
          {problem ?? (save.error ? failureMessage("save the fact", save.error) : "")}
        </p>
      )}
      <div className="flex gap-2">
        <button type="submit" disabled={save.isPending} className={PRIMARY_BUTTON}>
          Save fact
        </button>
        <button type="button" onClick={onDone} className={BUTTON}>
          Cancel
        </button>
      </div>
    </form>
  );
}
