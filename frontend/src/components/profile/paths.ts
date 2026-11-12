import type { LintFinding } from "../../api/profile";

export type Section = "work" | "education" | "projects";

/** Where the basics are in the profile, as resume-rule findings write it. */
export const BASICS_PATH = "basics";

/** Where an item is, e.g. `work[northwind]`. */
export function itemPath(section: Section, id: string): string {
  return `${section}[${id}]`;
}

/** Where a bullet is, e.g. `work[northwind].highlights[nw_export]`. */
export function bulletPath(item: string, bulletId: string): string {
  return `${item}.highlights[${bulletId}]`;
}

/** The findings about `path` or its fields. An item's findings leave out its bullets', which
 * show on the bullets. */
export function findingsAt(findings: readonly LintFinding[], path: string): LintFinding[] {
  return findings.filter(
    ({ location }) =>
      location === path ||
      (location.startsWith(`${path}.`) && !location.startsWith(`${path}.highlights[`)),
  );
}
