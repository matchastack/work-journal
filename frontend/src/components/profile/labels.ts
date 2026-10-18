import type {
  Author,
  Education,
  ItemStatus,
  LintFinding,
  Project,
  Role,
  Strength,
  Verification,
} from "../../api/profile";

export const STATUS_LABELS: Record<ItemStatus, string> = {
  active: "Active",
  benched: "Benched",
  planned: "Planned",
};

export const STRENGTH_LABELS: Record<Strength, string> = {
  high: "High",
  medium: "Medium",
  low: "Low",
};

export const VERIFICATION_LABELS: Record<Verification, string> = {
  yes: "Verified",
  partial: "Partly verified",
  no: "Not verified",
};

export const SEVERITY_LABELS: Record<LintFinding["severity"], string> = {
  error: "Error",
  warning: "Warning",
};

/** Who saved a version: the owner by hand, or a refresh's proposal the owner accepted. */
export const AUTHOR_LABELS: Record<Author, string> = {
  owner: "By you",
  ai: "From your journal, accepted by you",
};

/** The same, to end a sentence: `saved ... by you`. */
export const SAVED_BY: Record<Author, string> = {
  owner: "by you",
  ai: "from your journal",
};

/** A role, education entry or project, with the part of the profile it's in. */
export type Entry =
  | { section: "work"; item: Role }
  | { section: "education"; item: Education }
  | { section: "projects"; item: Project };

/** What the owner calls an item, as the API's history and findings do. */
export function entryLabel(entry: Entry): string {
  switch (entry.section) {
    case "work":
      return `${entry.item.position}, ${entry.item.name}`;
    case "education":
      return `${entry.item.studyType} in ${entry.item.area}, ${entry.item.institution}`;
    case "projects":
      return entry.item.name;
  }
}
