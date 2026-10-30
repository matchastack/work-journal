/**
 * The drafts and edits of the metadata that drives tailoring (T-043): a role's tailoring rules, a
 * bullet's swaps, an education entry's coursework and rules, and the profile's role types,
 * skills and gaps, skills presets, summaries and open questions. As in `drafts.ts`, each
 * `...EditOf` returns the edit the API takes, or what's wrong with the draft.
 *
 * Lists of sentences, such as honesty boundaries or a swap's contexts, are a line each; lists of
 * short names, such as a skill's other spellings, are separated by commas (`commaList`). A list
 * whose text wasn't touched is sent as it was saved (`editedList`), so an entry its text can't
 * hold survives.
 */

import type {
  CourseworkEdit,
  Education,
  OpenQuestion,
  OpenQuestionEdit,
  Profile,
  Role,
  RoleRulesEdit,
  RoleType,
  RoleTypesEdit,
  SkillPreset,
  SkillPresetsEdit,
  SkillsEdit,
  SummariesEdit,
  Swap,
  SwapsEdit,
} from "../../api/profile";
import { commaList, editedList, LIMITS, orNull, tagsProblem, type Result } from "./drafts";

/** The API's limits for metadata, in entries (`backend/app/profile/edits.py`). */
export const METADATA_LIMITS = {
  list: 20,
  courses: 100,
  roleTypes: 30,
  skills: 300,
  gaps: 100,
  lines: 20,
  lineSkills: 50,
  blocked: 50,
} as const;

const TAG = /^[a-z0-9][a-z0-9_-]*$/;

/** Text with a line each, as a list without blanks. */
export function lineList(text: string): string[] {
  return text
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line !== "");
}

/** What's wrong with a list, if anything: too many entries, or one too long. */
function listProblem(
  values: readonly string[],
  what: string,
  { count, length }: { count: number; length: number },
): string | null {
  if (values.length > count) {
    return `There can be at most ${String(count)} ${what}.`;
  }
  const long = values.find((value) => value.length > length);
  return long === undefined
    ? null
    : `"${long.slice(0, 40)}..." is too long: ${what} can have at most ${String(length)} characters.`;
}

/** The first value listed twice, if any, ignoring case when `fold`. */
function repeated(values: readonly string[], fold = false): string | undefined {
  const seen = new Set<string>();
  for (const value of values) {
    const key = fold ? value.toLowerCase() : value;
    if (seen.has(key)) {
      return value;
    }
    seen.add(key);
  }
  return undefined;
}

/** A role type's name for a choice, e.g. `Backend / full stack (backend)`. */
export function roleTypeOptions(roleTypes: readonly RoleType[]): Record<string, string> {
  return Object.fromEntries([
    ["", "Choose a role type"],
    ...roleTypes.map((type) => [type.id, `${type.name} (${type.id})`]),
  ]) as Record<string, string>;
}

// --- A role's tailoring rules (FR-PRF-5) ---------------------------------------------------------

export interface RoleRulesDraft {
  titleVariants: string;
  loadBearing: boolean;
  duringEducation: boolean;
  keepFor: string;
  cutFor: string;
  sensitivity: Role["sensitivity"];
  honestyBoundaries: string;
}

export function roleRulesDraft(role: Role): RoleRulesDraft {
  return {
    titleVariants: role.titleVariants.join("\n"),
    loadBearing: role.loadBearing,
    duringEducation: role.duringEducation,
    keepFor: role.keepFor.join(", "),
    cutFor: role.cutFor.join(", "),
    sensitivity: role.sensitivity,
    honestyBoundaries: role.honestyBoundaries.join("\n"),
  };
}

export function roleRulesEditOf(draft: RoleRulesDraft, baseVersion: number): Result<RoleRulesEdit> {
  const titleVariants = lineList(draft.titleVariants);
  const honestyBoundaries = lineList(draft.honestyBoundaries);
  const [keepFor, cutFor] = [commaList(draft.keepFor), commaList(draft.cutFor)];
  const problem =
    listProblem(titleVariants, "title variants", {
      count: METADATA_LIMITS.list,
      length: LIMITS.name,
    }) ??
    listProblem(honestyBoundaries, "honesty boundaries", {
      count: METADATA_LIMITS.list,
      length: LIMITS.reason,
    }) ??
    tagsProblem(keepFor) ??
    tagsProblem(cutFor);
  if (problem !== null) {
    return { problem };
  }
  const both = keepFor.find((type) => cutFor.includes(type));
  if (both !== undefined) {
    return { problem: `The role type ${both} can't be both kept and cut.` };
  }
  return {
    edit: {
      baseVersion,
      titleVariants,
      loadBearing: draft.loadBearing,
      duringEducation: draft.duringEducation,
      keepFor,
      cutFor,
      sensitivity: draft.sensitivity,
      honestyBoundaries,
    },
  };
}

// --- A bullet's swaps (FR-WRT-5) -----------------------------------------------------------------

export interface SwapDraft {
  key: number;
  text: string;
  replaces: string;
  contexts: string;
  savedContexts: string[];
  origin: Swap["origin"];
}

export function swapDrafts(swaps: readonly Swap[]): SwapDraft[] {
  return swaps.map((swap, key) => ({
    key,
    text: swap.text,
    replaces: swap.replaces ?? "",
    contexts: swap.contexts.join("\n"),
    savedContexts: [...swap.contexts],
    origin: swap.origin,
  }));
}

export function swapsEditOf(
  draft: { swaps: SwapDraft[] },
  baseVersion: number,
): Result<SwapsEdit> {
  if (draft.swaps.length > METADATA_LIMITS.list) {
    return { problem: `A bullet can have at most ${String(METADATA_LIMITS.list)} swaps.` };
  }
  const swaps: SwapsEdit["swaps"] = [];
  for (const [index, swap] of draft.swaps.entries()) {
    const text = swap.text.trim();
    if (text === "") {
      return { problem: `Swap ${String(index + 1)} needs its phrasing.` };
    }
    const contexts = editedList(
      swap.contexts,
      swap.savedContexts,
      swap.savedContexts.join("\n"),
      lineList,
    );
    const problem =
      listProblem([text], "a swap", { count: 1, length: LIMITS.bullet }) ??
      listProblem(contexts, "contexts", { count: METADATA_LIMITS.list, length: LIMITS.name });
    if (problem !== null) {
      return { problem };
    }
    swaps.push({ text, replaces: orNull(swap.replaces), contexts, origin: swap.origin });
  }
  return { edit: { baseVersion, swaps } };
}

// --- An education entry's coursework and rules (FR-PRF-7) ----------------------------------------

export interface SubsetDraft {
  key: number;
  roleType: string;
  courses: string[];
}

export interface CourseworkDraft {
  courses: string;
  subsets: SubsetDraft[];
  rules: string;
}

export function courseworkDraft(education: Education): CourseworkDraft {
  return {
    courses: education.courses.join("\n"),
    subsets: education.courseworkSubsets.map((subset, key) => ({
      key,
      roleType: subset.roleType,
      courses: [...subset.courses],
    })),
    rules: education.rules.join("\n"),
  };
}

/**
 * A course taken off the full list leaves the role types' coursework too. Each role type's
 * courses keep their order, which resumes print them in, with newly ticked ones last.
 */
export function courseworkEditOf(
  draft: CourseworkDraft,
  baseVersion: number,
): Result<CourseworkEdit> {
  const courses = lineList(draft.courses);
  const rules = lineList(draft.rules);
  const problem =
    listProblem(courses, "courses", { count: METADATA_LIMITS.courses, length: LIMITS.name }) ??
    listProblem(rules, "rules", { count: METADATA_LIMITS.list, length: LIMITS.reason });
  if (problem !== null) {
    return { problem };
  }
  const twice = repeated(courses);
  if (twice !== undefined) {
    return { problem: `${twice} is on the coursework list twice.` };
  }
  if (draft.subsets.some((subset) => subset.roleType === "")) {
    return { problem: "Choose the role type of each coursework for a role type." };
  }
  const roleType = repeated(draft.subsets.map((subset) => subset.roleType));
  if (roleType !== undefined) {
    return { problem: `There's more than one coursework for ${roleType}.` };
  }
  const courseworkSubsets = draft.subsets.map((subset) => ({
    roleType: subset.roleType,
    courses: subset.courses.filter((course) => courses.includes(course)),
  }));
  const empty = courseworkSubsets.find((subset) => subset.courses.length === 0);
  if (empty !== undefined) {
    return { problem: `Choose at least one course for ${empty.roleType}, or remove it.` };
  }
  return { edit: { baseVersion, courses, courseworkSubsets, rules } };
}

// --- Role types ----------------------------------------------------------------------------------

export interface RoleTypeDraft {
  key: number;
  id: string;
  name: string;
  /** Saved before, so its ID is fixed: presets, summaries and rules are kept by it. */
  saved: boolean;
}

export function roleTypeDrafts(roleTypes: readonly RoleType[]): RoleTypeDraft[] {
  return roleTypes.map((type, key) => ({ key, id: type.id, name: type.name, saved: true }));
}

export function roleTypesEditOf(
  draft: { roleTypes: RoleTypeDraft[] },
  baseVersion: number,
): Result<RoleTypesEdit> {
  if (draft.roleTypes.length > METADATA_LIMITS.roleTypes) {
    return { problem: `There can be at most ${String(METADATA_LIMITS.roleTypes)} role types.` };
  }
  const roleTypes = draft.roleTypes.map((type) => ({ id: type.id.trim(), name: type.name.trim() }));
  for (const type of roleTypes) {
    if (!TAG.test(type.id) || type.id.length > LIMITS.tag) {
      return {
        problem: `The ID "${type.id}" needs to be lowercase letters, digits, - or _, such as backend.`,
      };
    }
    if (type.name === "") {
      return { problem: `The role type ${type.id} needs a name.` };
    }
  }
  const twice = repeated(roleTypes.map((type) => type.id));
  if (twice !== undefined) {
    return { problem: `The role type ${twice} is listed twice.` };
  }
  return { edit: { baseVersion, roleTypes } };
}

// --- Skills and gaps (FR-PRF-6) ------------------------------------------------------------------

export type Tier = "" | "strong" | "working" | "academic";

export interface SkillDraft {
  key: number;
  name: string;
  previousName: string | null;
  category: string;
  tier: Tier;
  verifyBeforeShipping: boolean;
  aliases: string;
  savedAliases: string[];
}

export interface SkillsDraft {
  skills: SkillDraft[];
  gaps: string;
}

export function skillsDraft(profile: Profile): SkillsDraft {
  return {
    skills: profile.skills.map((skill, key) => ({
      key,
      name: skill.name,
      previousName: skill.name,
      category: skill.category,
      tier: skill.tier ?? "",
      verifyBeforeShipping: skill.verifyBeforeShipping,
      aliases: skill.aliases.join(", "),
      savedAliases: [...skill.aliases],
    })),
    gaps: profile.skillGaps.join("\n"),
  };
}

export function skillsEditOf(draft: SkillsDraft, baseVersion: number): Result<SkillsEdit> {
  if (draft.skills.length > METADATA_LIMITS.skills) {
    return { problem: `There can be at most ${String(METADATA_LIMITS.skills)} skills.` };
  }
  const skills: SkillsEdit["skills"] = [];
  for (const [index, skill] of draft.skills.entries()) {
    const [name, category] = [skill.name.trim(), skill.category.trim()];
    if (name === "" || category === "") {
      return { problem: `Skill ${String(index + 1)} needs a name and a category.` };
    }
    const aliases = editedList(skill.aliases, skill.savedAliases, skill.savedAliases.join(", "));
    const problem =
      listProblem([name, category], "a skill's name and category", {
        count: 2,
        length: LIMITS.name,
      }) ?? listProblem(aliases, "aliases", { count: METADATA_LIMITS.list, length: LIMITS.name });
    if (problem !== null) {
      return { problem };
    }
    skills.push({
      name,
      previousName: skill.previousName,
      category,
      tier: skill.tier === "" ? null : skill.tier,
      verifyBeforeShipping: skill.verifyBeforeShipping,
      aliases,
    });
  }
  const skillGaps = lineList(draft.gaps);
  const problem = listProblem(skillGaps, "gaps", {
    count: METADATA_LIMITS.gaps,
    length: LIMITS.name,
  });
  if (problem !== null) {
    return { problem };
  }
  const twice = repeated(
    skills.map((skill) => skill.name),
    true,
  );
  if (twice !== undefined) {
    return { problem: `The skill ${twice} is listed twice.` };
  }
  const gapTwice = repeated(skillGaps, true);
  if (gapTwice !== undefined) {
    return { problem: `The gap ${gapTwice} is listed twice.` };
  }
  const names = new Set(skills.map((skill) => skill.name.toLowerCase()));
  const claimed = skillGaps.find((gap) => names.has(gap.toLowerCase()));
  if (claimed !== undefined) {
    return { problem: `${claimed} can't be both a skill and a gap.` };
  }
  return { edit: { baseVersion, skills, skillGaps } };
}

// --- Skills presets (FR-PRF-9) and summaries (FR-PRF-8) ------------------------------------------

export interface PresetDraft {
  key: number;
  roleType: string;
  /** A line each, as `Languages: Python, SQL`. */
  lines: string;
  /** The lines as saved, sent as they are while their text is untouched; null for a new preset. */
  savedLines: SkillPreset["lines"] | null;
}

export function presetDrafts(profile: Profile): PresetDraft[] {
  return profile.skillPresets.map((preset, key) => ({
    key,
    roleType: preset.roleType,
    lines: presetText(preset.lines),
    savedLines: preset.lines,
  }));
}

function presetText(lines: SkillPreset["lines"]): string {
  return lines.map((line) => `${line.label}: ${line.skills.join(", ")}`).join("\n");
}

export function presetsEditOf(
  draft: { presets: PresetDraft[] },
  baseVersion: number,
): Result<SkillPresetsEdit> {
  const skillPresets: SkillPresetsEdit["skillPresets"] = [];
  for (const preset of draft.presets) {
    if (preset.roleType === "") {
      return { problem: "Choose the role type of each skills preset." };
    }
    if (preset.savedLines !== null && preset.lines === presetText(preset.savedLines)) {
      skillPresets.push({ roleType: preset.roleType, lines: preset.savedLines });
      continue;
    }
    const lines = lineList(preset.lines);
    if (lines.length === 0) {
      return { problem: `The ${preset.roleType} preset needs at least one line.` };
    }
    if (lines.length > METADATA_LIMITS.lines) {
      return {
        problem: `The ${preset.roleType} preset can have at most ${String(METADATA_LIMITS.lines)} lines.`,
      };
    }
    const parsed = [];
    for (const line of lines) {
      const colon = line.indexOf(":");
      const label = colon === -1 ? "" : line.slice(0, colon).trim();
      const skills = colon === -1 ? [] : commaList(line.slice(colon + 1));
      if (label === "" || skills.length === 0) {
        return {
          problem: `"${line}" in the ${preset.roleType} preset needs a label and skills, such as Languages: Python, SQL.`,
        };
      }
      const problem =
        listProblem([label], "a line's label", { count: 1, length: LIMITS.name }) ??
        listProblem(skills, "skills on a line", {
          count: METADATA_LIMITS.lineSkills,
          length: LIMITS.name,
        });
      if (problem !== null) {
        return { problem };
      }
      parsed.push({ label, skills });
    }
    skillPresets.push({ roleType: preset.roleType, lines: parsed });
  }
  const twice = repeated(skillPresets.map((preset) => preset.roleType));
  if (twice !== undefined) {
    return { problem: `There's more than one skills preset for ${twice}.` };
  }
  return { edit: { baseVersion, skillPresets } };
}

export interface SummaryDraft {
  key: number;
  roleType: string;
  text: string;
}

export function summaryDrafts(profile: Profile): SummaryDraft[] {
  return profile.summaries.map((summary, key) => ({
    key,
    roleType: summary.roleType,
    text: summary.text,
  }));
}

export function summariesEditOf(
  draft: { summaries: SummaryDraft[] },
  baseVersion: number,
): Result<SummariesEdit> {
  const summaries = draft.summaries.map((summary) => ({
    roleType: summary.roleType,
    text: summary.text.trim(),
  }));
  if (summaries.some((summary) => summary.roleType === "")) {
    return { problem: "Choose the role type of each summary." };
  }
  const empty = summaries.find((summary) => summary.text === "");
  if (empty !== undefined) {
    return { problem: `The ${empty.roleType} summary needs its text.` };
  }
  const twice = repeated(summaries.map((summary) => summary.roleType));
  if (twice !== undefined) {
    return { problem: `There's more than one summary for ${twice}.` };
  }
  return { edit: { baseVersion, summaries } };
}

// --- Open questions (FR-PRF-10) ------------------------------------------------------------------

export interface QuestionDraft {
  question: string;
  status: OpenQuestion["status"];
  bulletIds: string[];
}

export function questionDraft(question: OpenQuestion | null, profile: Profile): QuestionDraft {
  if (question === null) {
    return { question: "", status: "open", bulletIds: [] };
  }
  const items = [...profile.work, ...profile.education, ...profile.projects];
  return {
    question: question.question,
    status: question.status,
    bulletIds: items
      .flatMap((item) => item.highlights)
      .filter((bullet) => bullet.needs.includes(question.id))
      .map((bullet) => bullet.id),
  };
}

export function questionEditOf(draft: QuestionDraft, baseVersion: number): Result<OpenQuestionEdit> {
  const question = draft.question.trim();
  if (question === "") {
    return { problem: "The open question needs its question." };
  }
  if (question.length > LIMITS.reason) {
    return {
      problem: `The question can have at most ${String(LIMITS.reason)} characters.`,
    };
  }
  if (draft.bulletIds.length > METADATA_LIMITS.blocked) {
    return {
      problem: `A question can block at most ${String(METADATA_LIMITS.blocked)} bullets.`,
    };
  }
  return { edit: { baseVersion, question, status: draft.status, bulletIds: draft.bulletIds } };
}
