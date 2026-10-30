import { useId } from "react";

import type { LintFinding, RoleType } from "../../api/profile";
import { factMonth } from "../../format";
import { TEXT_BUTTON } from "../ui";
import { BulletItem } from "./BulletItem";
import { Findings } from "./Findings";
import { EducationForm, ProjectForm, RoleForm } from "./ItemForms";
import { Coursework, RoleRules } from "./ItemMetadata";
import { entryLabel, type Entry } from "./labels";
import { bulletPath, findingsAt, itemPath } from "./paths";
import { useEditing } from "./useEditing";

const NOUNS = { work: "role", education: "education entry", projects: "project" } as const;

/** E.g. `Mar 2025 - now`, or null without dates. */
function dates(start: string | null | undefined, end: string | null | undefined) {
  if (!start && !end) {
    return null;
  }
  if (!start) {
    return `Until ${factMonth(end ?? "")}`;
  }
  return `${factMonth(start)} - ${end ? factMonth(end) : "now"}`;
}

function details(entry: Entry): string[] {
  const { item } = entry;
  const parts: (string | null | undefined)[] = [];
  switch (entry.section) {
    case "work":
      parts.push(dates(entry.item.startDate, entry.item.endDate), entry.item.location);
      break;
    case "education":
      parts.push(
        dates(entry.item.startDate, entry.item.endDate),
        entry.item.honours,
        entry.item.location,
      );
      break;
    case "projects": {
      const project = entry.item;
      if (project.status === "benched") {
        parts.push(`Benched: ${project.statusReason ?? ""}`);
      } else if (project.status === "planned") {
        parts.push(project.statusReason ? `Planned: ${project.statusReason}` : "Planned");
      }
      parts.push(
        dates(project.startDate, project.endDate) ?? "No dates",
        project.keywords.length > 0 ? `Tech stack: ${project.keywords.join(", ")}` : null,
        project.tags.length > 0 ? `Tags: ${project.tags.join(", ")}` : null,
      );
      break;
    }
  }
  parts.push(item.url ? `Link: ${item.url}` : null);
  return parts.filter((part): part is string => typeof part === "string" && part !== "");
}

function ItemForm({
  entry,
  baseVersion,
  onSaved,
  onDone,
}: {
  entry: Entry;
  baseVersion: number;
  onSaved: (message: string) => void;
  onDone: () => void;
}) {
  const props = { baseVersion, onSaved, onDone };
  switch (entry.section) {
    case "work":
      return <RoleForm item={entry.item} {...props} />;
    case "education":
      return <EducationForm item={entry.item} {...props} />;
    case "projects":
      return <ProjectForm item={entry.item} {...props} />;
  }
}

/**
 * A role, education entry or project, with its bullets and the resume-rule findings about it.
 * Its fields are edited in place; each bullet has its own Edit. A role's tailoring rules and an
 * education entry's coursework and rules have their own Edit too (T-043).
 */
export function ItemCard({
  entry,
  findings,
  roleTypes,
  baseVersion,
  onSaved,
}: {
  entry: Entry;
  findings: readonly LintFinding[];
  roleTypes: readonly RoleType[];
  baseVersion: number;
  onSaved: (message: string) => void;
}) {
  const { editing, button, open, close } = useEditing();
  const titleId = useId();
  const path = itemPath(entry.section, entry.item.id);
  const summary = entry.section === "work" ? entry.item.summary : null;
  const description = entry.section === "projects" ? entry.item.description : null;
  const muted = entry.section === "projects" && entry.item.status !== "active";
  return (
    <article
      aria-labelledby={titleId}
      className="rounded-lg border border-stone-200 bg-white p-4 shadow-sm"
    >
      <h3 id={titleId} className={`font-medium ${muted ? "text-stone-500" : ""}`}>
        {entryLabel(entry)}
      </h3>
      {editing ? (
        <div className="mt-3">
          <ItemForm entry={entry} baseVersion={baseVersion} onSaved={onSaved} onDone={close} />
        </div>
      ) : (
        <>
          <ul
            aria-label={`About this ${NOUNS[entry.section]}`}
            className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-sm text-stone-600"
          >
            {details(entry).map((detail) => (
              <li key={detail}>{detail}</li>
            ))}
          </ul>
          {(summary ?? description) && (
            <p className="mt-2 text-sm text-stone-700">{summary ?? description}</p>
          )}
          <Findings findings={findingsAt(findings, path)} path={path} />
          <button
            ref={button}
            type="button"
            aria-label={`Edit ${NOUNS[entry.section]}`}
            aria-describedby={titleId}
            onClick={open}
            className={`mt-2 ${TEXT_BUTTON}`}
          >
            Edit
          </button>
        </>
      )}
      {entry.section === "work" && (
        <RoleRules
          role={entry.item}
          roleTypes={roleTypes}
          baseVersion={baseVersion}
          onSaved={onSaved}
        />
      )}
      {entry.section === "education" && (
        <Coursework
          education={entry.item}
          roleTypes={roleTypes}
          baseVersion={baseVersion}
          onSaved={onSaved}
        />
      )}
      {entry.item.highlights.length === 0 ? (
        <p className="mt-3 text-sm text-stone-500">
          No bullets yet. They come from your journal when you send /refresh.
        </p>
      ) : (
        <ul aria-label="Bullets" className="mt-3 space-y-2">
          {entry.item.highlights.map((bullet) => {
            const at = bulletPath(path, bullet.id);
            return (
              <BulletItem
                key={bullet.id}
                bullet={bullet}
                path={at}
                findings={findingsAt(findings, at)}
                roleTypes={roleTypes}
                baseVersion={baseVersion}
                onSaved={onSaved}
              />
            );
          })}
        </ul>
      )}
    </article>
  );
}
