/**
 * The Profile page's forms as drafts of text, and the edits they send. Each `...EditOf` returns
 * the edit, with every field the API takes, or what's wrong with the draft, so nothing is sent
 * that the API would refuse. Blank optional text is sent as null.
 */

import type {
  Basics,
  BasicsEdit,
  Bullet,
  BulletEdit,
  Education,
  EducationEdit,
  ItemStatus,
  Project,
  ProjectEdit,
  Role,
  RoleEdit,
  Strength,
  Verification,
} from "../../api/profile";

/** The API's limits, in characters or entries (`backend/app/profile/edits.py`). */
export const LIMITS = {
  name: 200,
  url: 500,
  text: 2000,
  reason: 500,
  bullet: 500,
  tag: 32,
  tags: 20,
  keywords: 50,
  profiles: 20,
} as const;

const MONTH = /^\d{4}-(0[1-9]|1[0-2])$/;
const TAG = /^[a-z0-9][a-z0-9_-]*$/;

export type Result<Edit> = { edit: Edit } | { problem: string };

/** Trimmed text, or null when it's blank. */
export function orNull(text: string): string | null {
  const trimmed = text.trim();
  return trimmed === "" ? null : trimmed;
}

/** Comma-separated text as a list, without blanks. */
export function commaList(text: string): string[] {
  return text
    .split(",")
    .map((part) => part.trim())
    .filter((part) => part !== "");
}

/** What's wrong with a start and end date, if anything: each is a month such as 2026-10, and
 * the end isn't before the start. */
export function datesProblem(
  start: string,
  end: string,
  { startRequired }: { startRequired: boolean },
): string | null {
  const [from, to] = [start.trim(), end.trim()];
  if (startRequired && from === "") {
    return "It needs a start date.";
  }
  for (const [name, value] of [
    ["start", from],
    ["end", to],
  ] as const) {
    if (value !== "" && !MONTH.test(value)) {
      return `The ${name} date needs to be a month, such as 2026-10.`;
    }
  }
  if (from !== "" && to !== "" && to < from) {
    return "The end date is before the start date.";
  }
  return null;
}

/** What's wrong with a list of tags, if anything. */
export function tagsProblem(tags: readonly string[]): string | null {
  const wrong = tags.find((tag) => !TAG.test(tag) || tag.length > LIMITS.tag);
  if (wrong !== undefined) {
    return `The tag "${wrong}" needs to be lowercase letters, digits, - or _, such as backend.`;
  }
  if (tags.length > LIMITS.tags) {
    return `There can be at most ${String(LIMITS.tags)} tags.`;
  }
  return null;
}

/** What the page says once a save is done: whether it made a new version. */
export function savedMessage(version: number | undefined, baseVersion: number): string {
  return version === undefined || version === baseVersion
    ? "Nothing changed, so no new version was saved."
    : `Saved as version ${String(version)}.`;
}

// --- Bullets -------------------------------------------------------------------------------------

export interface BulletDraft {
  text: string;
  status: ItemStatus;
  statusReason: string;
  strength: Strength;
  verification: Verification;
  verificationNote: string;
  tags: string;
}

export function bulletDraft(bullet: Bullet): BulletDraft {
  return {
    text: bullet.text,
    status: bullet.status,
    statusReason: bullet.statusReason ?? "",
    strength: bullet.strength,
    verification: bullet.verification,
    verificationNote: bullet.verificationNote ?? "",
    tags: bullet.tags.join(", "),
  };
}

export function bulletEditOf(draft: BulletDraft, baseVersion: number): Result<BulletEdit> {
  const text = draft.text.trim();
  if (text === "") {
    return { problem: "The bullet needs text." };
  }
  // A reason goes with a benched or planned bullet only.
  const reason = draft.status === "active" ? null : orNull(draft.statusReason);
  if (draft.status === "benched" && reason === null) {
    return { problem: "Say why the bullet is benched." };
  }
  const tags = commaList(draft.tags);
  const problem = tagsProblem(tags);
  if (problem !== null) {
    return { problem };
  }
  return {
    edit: {
      baseVersion,
      text,
      status: draft.status,
      statusReason: reason,
      strength: draft.strength,
      verification: draft.verification,
      verificationNote: orNull(draft.verificationNote),
      tags,
    },
  };
}

// --- Basics --------------------------------------------------------------------------------------

export interface LinkDraft {
  key: number;
  network: string;
  username: string;
  url: string;
}

export interface BasicsDraft {
  name: string;
  label: string;
  email: string;
  phone: string;
  url: string;
  summary: string;
  city: string;
  region: string;
  countryCode: string;
  profiles: LinkDraft[];
}

export function basicsDraft(basics: Basics): BasicsDraft {
  return {
    name: basics.name,
    label: basics.label ?? "",
    email: basics.email ?? "",
    phone: basics.phone ?? "",
    url: basics.url ?? "",
    summary: basics.summary ?? "",
    city: basics.location?.city ?? "",
    region: basics.location?.region ?? "",
    countryCode: basics.location?.countryCode ?? "",
    profiles: basics.profiles.map((profile, key) => ({
      key,
      network: profile.network,
      username: profile.username ?? "",
      url: profile.url ?? "",
    })),
  };
}

export function basicsEditOf(draft: BasicsDraft, baseVersion: number): Result<BasicsEdit> {
  const name = draft.name.trim();
  if (name === "") {
    return { problem: "Your basics need your name." };
  }
  const blank = draft.profiles.findIndex((profile) => profile.network.trim() === "");
  if (blank !== -1) {
    return { problem: `Profile ${String(blank + 1)} needs a network, such as GitHub.` };
  }
  const location = {
    city: orNull(draft.city),
    region: orNull(draft.region),
    countryCode: orNull(draft.countryCode),
  };
  const located = Object.values(location).some((part) => part !== null);
  return {
    edit: {
      baseVersion,
      name,
      label: orNull(draft.label),
      email: orNull(draft.email),
      phone: orNull(draft.phone),
      url: orNull(draft.url),
      summary: orNull(draft.summary),
      location: located ? location : null,
      profiles: draft.profiles.map((profile) => ({
        network: profile.network.trim(),
        username: orNull(profile.username),
        url: orNull(profile.url),
      })),
    },
  };
}

// --- Roles, education and projects ---------------------------------------------------------------

export interface RoleDraft {
  name: string;
  position: string;
  location: string;
  url: string;
  startDate: string;
  endDate: string;
  summary: string;
}

export function roleDraft(role: Role): RoleDraft {
  return {
    name: role.name,
    position: role.position,
    location: role.location ?? "",
    url: role.url ?? "",
    startDate: role.startDate,
    endDate: role.endDate ?? "",
    summary: role.summary ?? "",
  };
}

export function roleEditOf(draft: RoleDraft, baseVersion: number): Result<RoleEdit> {
  if (draft.name.trim() === "") {
    return { problem: "The role needs an organisation." };
  }
  if (draft.position.trim() === "") {
    return { problem: "The role needs a title." };
  }
  const problem = datesProblem(draft.startDate, draft.endDate, { startRequired: true });
  if (problem !== null) {
    return { problem };
  }
  return {
    edit: {
      baseVersion,
      name: draft.name.trim(),
      position: draft.position.trim(),
      location: orNull(draft.location),
      url: orNull(draft.url),
      startDate: draft.startDate.trim(),
      endDate: orNull(draft.endDate),
      summary: orNull(draft.summary),
    },
  };
}

export interface EducationDraft {
  institution: string;
  studyType: string;
  area: string;
  honours: string;
  location: string;
  url: string;
  startDate: string;
  endDate: string;
}

export function educationDraft(education: Education): EducationDraft {
  return {
    institution: education.institution,
    studyType: education.studyType,
    area: education.area,
    honours: education.honours ?? "",
    location: education.location ?? "",
    url: education.url ?? "",
    startDate: education.startDate,
    endDate: education.endDate ?? "",
  };
}

export function educationEditOf(draft: EducationDraft, baseVersion: number): Result<EducationEdit> {
  const required = [
    [draft.institution, "an institution"],
    [draft.studyType, "a degree"],
    [draft.area, "an area of study"],
  ] as const;
  for (const [value, what] of required) {
    if (value.trim() === "") {
      return { problem: `The education entry needs ${what}.` };
    }
  }
  const problem = datesProblem(draft.startDate, draft.endDate, { startRequired: true });
  if (problem !== null) {
    return { problem };
  }
  return {
    edit: {
      baseVersion,
      institution: draft.institution.trim(),
      studyType: draft.studyType.trim(),
      area: draft.area.trim(),
      honours: orNull(draft.honours),
      location: orNull(draft.location),
      url: orNull(draft.url),
      startDate: draft.startDate.trim(),
      endDate: orNull(draft.endDate),
    },
  };
}

export interface ProjectDraft {
  name: string;
  description: string;
  keywords: string;
  url: string;
  startDate: string;
  endDate: string;
  status: ItemStatus;
  statusReason: string;
  tags: string;
}

export function projectDraft(project: Project): ProjectDraft {
  return {
    name: project.name,
    description: project.description ?? "",
    keywords: project.keywords.join(", "),
    url: project.url ?? "",
    startDate: project.startDate ?? "",
    endDate: project.endDate ?? "",
    status: project.status,
    statusReason: project.statusReason ?? "",
    tags: project.tags.join(", "),
  };
}

export function projectEditOf(draft: ProjectDraft, baseVersion: number): Result<ProjectEdit> {
  if (draft.name.trim() === "") {
    return { problem: "The project needs a name." };
  }
  const dates = datesProblem(draft.startDate, draft.endDate, { startRequired: false });
  if (dates !== null) {
    return { problem: dates };
  }
  const reason = draft.status === "active" ? null : orNull(draft.statusReason);
  if (draft.status === "benched" && reason === null) {
    return { problem: "Say why the project is benched." };
  }
  const keywords = commaList(draft.keywords);
  if (keywords.length > LIMITS.keywords) {
    return { problem: `The tech stack can list at most ${String(LIMITS.keywords)} entries.` };
  }
  const tags = commaList(draft.tags);
  const problem = tagsProblem(tags);
  if (problem !== null) {
    return { problem };
  }
  return {
    edit: {
      baseVersion,
      name: draft.name.trim(),
      description: orNull(draft.description),
      keywords,
      url: orNull(draft.url),
      startDate: orNull(draft.startDate),
      endDate: orNull(draft.endDate),
      status: draft.status,
      statusReason: reason,
      tags,
    },
  };
}
