import { useId } from "react";

import {
  useSaveQuestion,
  useSaveRoleTypes,
  useSaveSkillPresets,
  useSaveSkills,
  useSaveSummaries,
  type OpenQuestion,
  type Profile,
  type RoleType,
} from "../../api/profile";
import { LABEL, TEXT_BUTTON } from "../ui";
import { Block, Facts } from "./Block";
import { LIMITS } from "./drafts";
import { CheckboxField, EditForm, Rows, SelectField, TextArea, TextField } from "./fields";
import { entryLabel } from "./labels";
import {
  METADATA_LIMITS,
  presetDrafts,
  presetsEditOf,
  questionDraft,
  questionEditOf,
  roleTypeDrafts,
  roleTypeOptions,
  roleTypesEditOf,
  skillsDraft,
  skillsEditOf,
  summariesEditOf,
  summaryDrafts,
  type SkillDraft,
  type Tier,
} from "./metadata";
import { useEditForm } from "./useEditForm";
import { useEditing } from "./useEditing";

/** What each part of the profile here is given. */
interface Part {
  profile: Profile;
  baseVersion: number;
  onSaved: (message: string) => void;
}

const TIERS: Record<Tier, string> = {
  "": "No tier",
  strong: "Strong",
  working: "Working",
  academic: "Academic",
};

const QUESTION_STATUS = { open: "Open", answered: "Answered", closed: "Closed" } as const;

function roleTypeName(roleTypes: readonly RoleType[], id: string): string {
  return roleTypes.find((type) => type.id === id)?.name ?? id;
}

// --- Role types ----------------------------------------------------------------------------------

function RoleTypesForm({ profile, baseVersion, onSaved, onDone }: Part & { onDone: () => void }) {
  const save = useSaveRoleTypes();
  const { draft, problem, stale, change, submit } = useEditForm({
    current: { roleTypes: roleTypeDrafts(profile.roleTypes) },
    editOf: roleTypesEditOf,
    subject: "Your role types",
    baseVersion,
    save: (edit, onSuccess) => {
      save.mutate(edit, { onSuccess });
    },
    onSaved,
    onDone,
  });
  return (
    <EditForm
      label="Edit your role types"
      action="save your role types"
      problem={problem}
      stale={stale}
      error={save.error}
      pending={save.isPending}
      onSubmit={submit}
      onCancel={onDone}
    >
      <Rows
        legend="Role types"
        noun="Role type"
        rows={draft.roleTypes}
        max={METADATA_LIMITS.roleTypes}
        empty="No role types yet."
        onChange={(roleTypes) => {
          change({ roleTypes });
        }}
        makeRow={(key) => ({ key, id: "", name: "", saved: false })}
      >
        {(type, changeType) => (
          <div className="grid gap-2 sm:grid-cols-2">
            {type.saved ? (
              <p className="text-sm">
                <span className={LABEL}>ID</span>
                <span className="mt-2 block">{type.id}</span>
              </p>
            ) : (
              <TextField
                label="ID"
                hint="Lowercase, such as backend. It can't change once saved."
                value={type.id}
                onChange={(id) => {
                  changeType({ id });
                }}
                maxLength={LIMITS.tag}
                required
              />
            )}
            <TextField
              label="Name"
              value={type.name}
              onChange={(name) => {
                changeType({ name });
              }}
              maxLength={LIMITS.name}
              required
            />
          </div>
        )}
      </Rows>
    </EditForm>
  );
}

/** The kinds of job you apply for. Presets, summaries, coursework and rules are kept by them. */
export function RoleTypes(part: Part) {
  const { roleTypes } = part.profile;
  return (
    <Block
      title="Role types"
      kind="card"
      editLabel="Edit role types"
      form={(onDone) => <RoleTypesForm {...part} onDone={onDone} />}
    >
      {roleTypes.length === 0 ? (
        <p className="text-stone-500">No role types yet.</p>
      ) : (
        <ul aria-label="Role types" className="flex flex-wrap gap-x-4 gap-y-1">
          {roleTypes.map((type) => (
            <li key={type.id}>
              {type.name} <span className="text-stone-500">({type.id})</span>
            </li>
          ))}
        </ul>
      )}
    </Block>
  );
}

// --- Skills and gaps -----------------------------------------------------------------------------

function SkillsForm({ profile, baseVersion, onSaved, onDone }: Part & { onDone: () => void }) {
  const save = useSaveSkills();
  const { draft, problem, stale, change, submit } = useEditForm({
    current: skillsDraft(profile),
    editOf: skillsEditOf,
    subject: "Your skills",
    baseVersion,
    save: (edit, onSuccess) => {
      save.mutate(edit, { onSuccess });
    },
    onSaved,
    onDone,
  });
  return (
    <EditForm
      label="Edit your skills"
      action="save your skills"
      problem={problem}
      stale={stale}
      error={save.error}
      pending={save.isPending}
      onSubmit={submit}
      onCancel={onDone}
    >
      <Rows
        legend="Skills"
        noun="Skill"
        rows={draft.skills}
        max={METADATA_LIMITS.skills}
        empty="No skills yet."
        onChange={(skills) => {
          change({ skills });
        }}
        makeRow={(key): SkillDraft => ({
          key,
          name: "",
          previousName: null,
          category: "",
          tier: "",
          verifyBeforeShipping: false,
          aliases: "",
          savedAliases: [],
        })}
      >
        {(skill, changeSkill) => (
          <div className="space-y-2">
            <div className="grid gap-2 sm:grid-cols-3">
              <TextField
                label="Name"
                hint="The spelling to use, such as Node.js."
                value={skill.name}
                onChange={(name) => {
                  changeSkill({ name });
                }}
                maxLength={LIMITS.name}
                required
              />
              <TextField
                label="Category"
                value={skill.category}
                onChange={(category) => {
                  changeSkill({ category });
                }}
                maxLength={LIMITS.name}
                required
              />
              <SelectField
                label="Tier"
                value={skill.tier}
                options={TIERS}
                onChange={(tier) => {
                  changeSkill({ tier });
                }}
              />
            </div>
            <TextField
              label="Other spellings (optional)"
              hint="Separated by commas, such as NodeJS. They're corrected to the name."
              value={skill.aliases}
              onChange={(aliases) => {
                changeSkill({ aliases });
              }}
              maxLength={LIMITS.text}
            />
            <CheckboxField
              label="Verify before shipping: confirm it each time a resume lists it"
              checked={skill.verifyBeforeShipping}
              onChange={(verifyBeforeShipping) => {
                changeSkill({ verifyBeforeShipping });
              }}
            />
          </div>
        )}
      </Rows>
      <TextArea
        label="Gaps"
        hint="One per line: skills a resume must never claim."
        value={draft.gaps}
        onChange={(gaps) => {
          change({ gaps });
        }}
        maxLength={LIMITS.text * 5}
      />
    </EditForm>
  );
}

/** Your skills, with their tier, other spellings and whether to verify them, and the gaps list
 * (FR-PRF-6). */
export function Skills(part: Part) {
  const { skills, skillGaps } = part.profile;
  return (
    <Block
      title="Skills and gaps"
      kind="card"
      editLabel="Edit skills and gaps"
      form={(onDone) => <SkillsForm {...part} onDone={onDone} />}
    >
      {skills.length === 0 ? (
        <p className="text-stone-500">No skills yet.</p>
      ) : (
        <ul aria-label="Skills" className="space-y-1">
          {skills.map((skill) => (
            <li key={skill.name}>
              <span className="font-medium">{skill.name}</span>{" "}
              <span className="text-stone-500">
                {[
                  skill.category,
                  skill.tier ? TIERS[skill.tier] : null,
                  skill.verifyBeforeShipping ? "verify before shipping" : null,
                  skill.aliases.length > 0 ? `also ${skill.aliases.join(", ")}` : null,
                ]
                  .filter((part) => part !== null)
                  .join(" - ")}
              </span>
            </li>
          ))}
        </ul>
      )}
      <p className="mt-2">
        <span className="text-stone-500">Never claim:</span>{" "}
        {skillGaps.length === 0 ? "nothing listed" : skillGaps.join(", ")}
      </p>
    </Block>
  );
}

// --- Skills presets ------------------------------------------------------------------------------

function PresetsForm({ profile, baseVersion, onSaved, onDone }: Part & { onDone: () => void }) {
  const save = useSaveSkillPresets();
  const { draft, problem, stale, change, submit } = useEditForm({
    current: { presets: presetDrafts(profile) },
    editOf: presetsEditOf,
    subject: "Your skills presets",
    baseVersion,
    save: (edit, onSuccess) => {
      save.mutate(edit, { onSuccess });
    },
    onSaved,
    onDone,
  });
  return (
    <EditForm
      label="Edit your skills presets"
      action="save your skills presets"
      problem={problem}
      stale={stale}
      error={save.error}
      pending={save.isPending}
      onSubmit={submit}
      onCancel={onDone}
    >
      <Rows
        legend="Skills presets"
        noun="Preset"
        rows={draft.presets}
        max={METADATA_LIMITS.roleTypes}
        empty="No presets yet."
        onChange={(presets) => {
          change({ presets });
        }}
        makeRow={(key) => ({ key, roleType: "", lines: "", savedLines: null })}
      >
        {(preset, changePreset) => (
          <div className="space-y-2">
            <SelectField
              label="Role type"
              value={preset.roleType}
              options={roleTypeOptions(profile.roleTypes)}
              onChange={(roleType) => {
                changePreset({ roleType });
              }}
            />
            <TextArea
              label="Lines"
              hint="One per line, such as Languages: Python, SQL."
              value={preset.lines}
              onChange={(lines) => {
                changePreset({ lines });
              }}
              maxLength={LIMITS.text * 5}
              rows={4}
            />
          </div>
        )}
      </Rows>
    </EditForm>
  );
}

/** The skills section for each role type (FR-PRF-9). */
export function SkillPresets(part: Part) {
  const { skillPresets, roleTypes } = part.profile;
  return (
    <Block
      title="Skills presets"
      kind="card"
      editLabel="Edit skills presets"
      form={(onDone) => <PresetsForm {...part} onDone={onDone} />}
    >
      {skillPresets.length === 0 ? (
        <p className="text-stone-500">No presets yet.</p>
      ) : (
        skillPresets.map((preset) => (
          <div key={preset.roleType} className="mt-2 first:mt-0">
            <p className="font-medium">{roleTypeName(roleTypes, preset.roleType)}</p>
            <Facts
              label={`Skills preset for ${roleTypeName(roleTypes, preset.roleType)}`}
              facts={preset.lines.map((line): [string, string] => [line.label, line.skills.join(", ")])}
            />
          </div>
        ))
      )}
    </Block>
  );
}

// --- Summaries -----------------------------------------------------------------------------------

function SummariesForm({ profile, baseVersion, onSaved, onDone }: Part & { onDone: () => void }) {
  const save = useSaveSummaries();
  const { draft, problem, stale, change, submit } = useEditForm({
    current: { summaries: summaryDrafts(profile) },
    editOf: summariesEditOf,
    subject: "Your summaries",
    baseVersion,
    save: (edit, onSuccess) => {
      save.mutate(edit, { onSuccess });
    },
    onSaved,
    onDone,
  });
  return (
    <EditForm
      label="Edit your summaries"
      action="save your summaries"
      problem={problem}
      stale={stale}
      error={save.error}
      pending={save.isPending}
      onSubmit={submit}
      onCancel={onDone}
    >
      <Rows
        legend="Summaries"
        noun="Summary"
        rows={draft.summaries}
        max={METADATA_LIMITS.roleTypes}
        empty="No summaries yet."
        onChange={(summaries) => {
          change({ summaries });
        }}
        makeRow={(key) => ({ key, roleType: "", text: "" })}
      >
        {(summary, changeSummary) => (
          <div className="space-y-2">
            <SelectField
              label="Role type"
              value={summary.roleType}
              options={roleTypeOptions(profile.roleTypes)}
              onChange={(roleType) => {
                changeSummary({ roleType });
              }}
            />
            <TextArea
              label="Text"
              value={summary.text}
              onChange={(text) => {
                changeSummary({ text });
              }}
              maxLength={LIMITS.text}
              required
            />
          </div>
        )}
      </Rows>
    </EditForm>
  );
}

/** A summary paragraph for each role type, off unless a resume asks for one (FR-PRF-8). */
export function Summaries(part: Part) {
  const { summaries, roleTypes } = part.profile;
  return (
    <Block
      title="Summaries"
      kind="card"
      editLabel="Edit summaries"
      form={(onDone) => <SummariesForm {...part} onDone={onDone} />}
    >
      <p className="text-stone-500">Off by default: a resume uses one only when asked.</p>
      {summaries.length === 0 ? (
        <p className="mt-1 text-stone-500">No summaries yet.</p>
      ) : (
        <Facts
          label="Summaries"
          facts={summaries.map((summary): [string, string] => [
            roleTypeName(roleTypes, summary.roleType),
            summary.text,
          ])}
        />
      )}
    </Block>
  );
}

// --- Open questions ------------------------------------------------------------------------------

function QuestionForm({
  question,
  profile,
  baseVersion,
  onSaved,
  onDone,
}: Part & { question: OpenQuestion | null; onDone: () => void }) {
  const save = useSaveQuestion();
  const { draft, problem, stale, change, submit } = useEditForm({
    current: questionDraft(question, profile),
    editOf: questionEditOf,
    subject: "This question",
    baseVersion,
    save: (edit, onSuccess) => {
      save.mutate({ id: question?.id ?? null, edit }, { onSuccess });
    },
    onSaved,
    onDone,
  });
  const items = [
    ...profile.work.map((item) => ({ section: "work" as const, item })),
    ...profile.education.map((item) => ({ section: "education" as const, item })),
    ...profile.projects.map((item) => ({ section: "projects" as const, item })),
  ].filter((entry) => entry.item.highlights.length > 0);
  return (
    <EditForm
      label={question === null ? "Add an open question" : "Edit the open question"}
      action="save the open question"
      problem={problem}
      stale={stale}
      error={save.error}
      pending={save.isPending}
      onSubmit={submit}
      onCancel={onDone}
    >
      <TextField
        label="Question"
        hint="A detail that would make a bullet stronger, such as the scale of a system."
        value={draft.question}
        onChange={(text) => {
          change({ question: text });
        }}
        maxLength={LIMITS.reason}
        required
        autoFocus
      />
      <SelectField
        label="Status"
        value={draft.status}
        options={QUESTION_STATUS}
        onChange={(status) => {
          change({ status });
        }}
      />
      <fieldset>
        <legend className={LABEL}>Bullets it blocks</legend>
        <div className="mt-1 max-h-64 space-y-2 overflow-y-auto rounded border border-stone-200 bg-white p-2">
          {items.map((entry) => (
            <fieldset key={entry.item.id}>
              <legend className="text-xs text-stone-600">{entryLabel(entry)}</legend>
              {entry.item.highlights.map((bullet) => (
                <CheckboxField
                  key={bullet.id}
                  label={bullet.text}
                  checked={draft.bulletIds.includes(bullet.id)}
                  onChange={(checked) => {
                    change({
                      bulletIds: checked
                        ? [...draft.bulletIds, bullet.id]
                        : draft.bulletIds.filter((id) => id !== bullet.id),
                    });
                  }}
                />
              ))}
            </fieldset>
          ))}
        </div>
      </fieldset>
    </EditForm>
  );
}

function QuestionItem({ question, ...part }: Part & { question: OpenQuestion }) {
  const { editing, button, open, close } = useEditing();
  const textId = useId();
  const blocked = [...part.profile.work, ...part.profile.education, ...part.profile.projects]
    .flatMap((item) => item.highlights)
    .filter((bullet) => bullet.needs.includes(question.id));
  return (
    <li className="rounded border border-stone-200 bg-stone-50 p-3">
      {editing ? (
        <QuestionForm question={question} {...part} onDone={close} />
      ) : (
        <>
          <p id={textId} className={question.status === "open" ? "" : "text-stone-500"}>
            {question.question}
          </p>
          <p className="mt-1 text-xs text-stone-600">
            {QUESTION_STATUS[question.status]}
            {question.answerFactIds.length > 0 &&
              ` - answered by ${String(question.answerFactIds.length)} ${question.answerFactIds.length === 1 ? "fact" : "facts"}`}
          </p>
          {blocked.length > 0 && (
            <ul aria-label="Bullets it blocks" className="mt-1 list-disc pl-4 text-sm text-stone-700">
              {blocked.map((bullet) => (
                <li key={bullet.id}>{bullet.text}</li>
              ))}
            </ul>
          )}
          <button
            ref={button}
            type="button"
            aria-label="Edit question"
            aria-describedby={textId}
            onClick={open}
            className={`mt-2 ${TEXT_BUTTON}`}
          >
            Edit
          </button>
        </>
      )}
    </li>
  );
}

/**
 * Open questions: details that would make a bullet stronger (FR-PRF-10). Each blocks the
 * bullets it names, and is open, answered or closed.
 */
export function OpenQuestions(part: Part) {
  const { editing, button, open, close } = useEditing();
  const questions = part.profile.openQuestions;
  return (
    <div className="space-y-3">
      {questions.length === 0 ? (
        <p className="text-sm text-stone-600">No open questions yet.</p>
      ) : (
        <ul aria-label="Open questions" className="space-y-2">
          {questions.map((question) => (
            <QuestionItem key={question.id} question={question} {...part} />
          ))}
        </ul>
      )}
      {editing ? (
        <div className="rounded border border-stone-200 bg-stone-50 p-3">
          <QuestionForm question={null} {...part} onDone={close} />
        </div>
      ) : (
        <button ref={button} type="button" onClick={open} className={TEXT_BUTTON}>
          Add an open question
        </button>
      )}
    </div>
  );
}
