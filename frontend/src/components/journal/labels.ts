import type {
  FactKind,
  JournalEntry,
  JournalFact,
  Ownership,
  Qualifier,
  RoleOption,
} from "../../api/journal";

export const KIND_LABELS: Record<FactKind, string> = {
  accomplishment: "Accomplishment",
  project: "Project",
  role_change: "Role change",
  skill: "Skill",
  certification: "Certification",
  award: "Award",
  talk: "Talk",
  learning: "Learning",
};

export const OWNERSHIP_LABELS: Record<Ownership, string> = {
  led: "Led",
  owned: "Owned",
  contributed: "Contributed",
  assisted: "Assisted",
};

export const QUALIFIER_LABELS: Record<Qualifier, string> = {
  exact: "Exactly",
  approximately: "About",
  at_least: "At least",
  more_than: "More than",
  at_most: "Up to",
  less_than: "Less than",
};

/** The role an entry or fact is about: one in the profile, by its label, or a new one. */
export function roleText(
  link: { roleId?: string | null; newRole?: string | null; proposedRole?: string | null },
  roles: readonly RoleOption[],
): string | null {
  if (link.roleId) {
    const role = roles.find((option) => option.id === link.roleId);
    return role ? role.label : `${link.roleId} (not in your profile)`;
  }
  const name = link.newRole ?? link.proposedRole;
  return name ? `New role: ${name}` : null;
}

/** Whether a fact is about another role than its entry, so it's worth showing. */
export function hasOwnRole(fact: JournalFact, entry: JournalEntry): boolean {
  return (fact.roleId ?? null) !== entry.roleId || (fact.proposedRole ?? null) !== entry.newRole;
}
