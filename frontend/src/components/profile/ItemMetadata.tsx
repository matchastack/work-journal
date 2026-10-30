import {
  useSaveCoursework,
  useSaveRoleRules,
  useSaveSwaps,
  type Bullet,
  type Education,
  type Role,
  type RoleType,
} from "../../api/profile";
import { LABEL } from "../ui";
import { Block, Facts } from "./Block";
import { LIMITS } from "./drafts";
import { CheckboxField, EditForm, Rows, SelectField, TextArea, TextField } from "./fields";
import {
  courseworkDraft,
  courseworkEditOf,
  lineList,
  METADATA_LIMITS,
  roleRulesDraft,
  roleRulesEditOf,
  roleTypeOptions,
  swapDrafts,
  swapsEditOf,
  type SwapDraft,
} from "./metadata";
import { useEditForm } from "./useEditForm";

interface Saving {
  baseVersion: number;
  onSaved: (message: string) => void;
}

const SENSITIVITY = {
  normal: "Normal",
  sensitive: "Sensitive: architecture and outcomes only",
} as const;

/** Your role types, for a hint: `Your role types: backend, data.` */
function roleTypesHint(roleTypes: readonly RoleType[]): string {
  return roleTypes.length === 0
    ? "Add role types under Role types first."
    : `Separated by commas. Your role types: ${roleTypes.map((type) => type.id).join(", ")}.`;
}

function RoleRulesForm({
  role,
  roleTypes,
  baseVersion,
  onSaved,
  onDone,
}: Saving & { role: Role; roleTypes: readonly RoleType[]; onDone: () => void }) {
  const save = useSaveRoleRules();
  const { draft, problem, stale, change, submit } = useEditForm({
    current: roleRulesDraft(role),
    editOf: roleRulesEditOf,
    subject: "This role",
    baseVersion,
    save: (edit, onSuccess) => {
      save.mutate({ id: role.id, edit }, { onSuccess });
    },
    onSaved,
    onDone,
  });
  return (
    <EditForm
      label="Edit the tailoring rules"
      action="save the tailoring rules"
      problem={problem}
      stale={stale}
      error={save.error}
      pending={save.isPending}
      onSubmit={submit}
      onCancel={onDone}
    >
      <TextArea
        label="Approved title variants"
        hint="One per line. A tailored resume may use one instead of the official title."
        value={draft.titleVariants}
        onChange={(titleVariants) => {
          change({ titleVariants });
        }}
        maxLength={LIMITS.text}
        autoFocus
      />
      <CheckboxField
        label="Load-bearing: always shown"
        checked={draft.loadBearing}
        onChange={(loadBearing) => {
          change({ loadBearing });
        }}
      />
      <CheckboxField
        label="Held while studying: can be cut without leaving a gap"
        checked={draft.duringEducation}
        onChange={(duringEducation) => {
          change({ duringEducation });
        }}
      />
      <div className="grid gap-3 sm:grid-cols-2">
        <TextField
          label="Keep for"
          hint={roleTypesHint(roleTypes)}
          value={draft.keepFor}
          onChange={(keepFor) => {
            change({ keepFor });
          }}
          maxLength={LIMITS.text}
        />
        <TextField
          label="Cut for"
          hint="Role types it's cut for first."
          value={draft.cutFor}
          onChange={(cutFor) => {
            change({ cutFor });
          }}
          maxLength={LIMITS.text}
        />
      </div>
      <SelectField
        label="Sensitivity"
        value={draft.sensitivity}
        options={SENSITIVITY}
        onChange={(sensitivity) => {
          change({ sensitivity });
        }}
      />
      <TextArea
        label="Honesty boundaries"
        hint="One per line: things never to claim for this role."
        value={draft.honestyBoundaries}
        onChange={(honestyBoundaries) => {
          change({ honestyBoundaries });
        }}
        maxLength={LIMITS.text * 5}
      />
    </EditForm>
  );
}

/**
 * A role's tailoring rules (FR-PRF-5): approved title variants, whether it's load-bearing or
 * held while studying, the role types it's kept or cut for, its sensitivity and its honesty
 * boundaries.
 */
export function RoleRules({
  role,
  roleTypes,
  ...saving
}: Saving & { role: Role; roleTypes: readonly RoleType[] }) {
  return (
    <Block
      title="Tailoring rules"
      kind="block"
      editLabel="Edit tailoring rules"
      form={(onDone) => (
        <RoleRulesForm role={role} roleTypes={roleTypes} onDone={onDone} {...saving} />
      )}
    >
      <Facts
        label="Tailoring rules"
        facts={[
          ["Title variants", role.titleVariants.join(", ")],
          ["Load-bearing", role.loadBearing && "Always shown"],
          ["Held while studying", role.duringEducation && "Can be cut without a gap"],
          ["Keep for", role.keepFor.join(", ")],
          ["Cut for", role.cutFor.join(", ")],
          ["Sensitivity", role.sensitivity === "sensitive" && SENSITIVITY.sensitive],
          [
            "Never claim",
            role.honestyBoundaries.length > 0 && (
              <ul className="list-disc pl-4">
                {role.honestyBoundaries.map((boundary) => (
                  <li key={boundary}>{boundary}</li>
                ))}
              </ul>
            ),
          ],
        ]}
      />
      {role.titleVariants.length === 0 &&
        !role.loadBearing &&
        !role.duringEducation &&
        role.keepFor.length === 0 &&
        role.cutFor.length === 0 &&
        role.sensitivity === "normal" &&
        role.honestyBoundaries.length === 0 && <p className="text-stone-500">None yet.</p>}
    </Block>
  );
}

function CourseworkForm({
  education,
  roleTypes,
  baseVersion,
  onSaved,
  onDone,
}: Saving & { education: Education; roleTypes: readonly RoleType[]; onDone: () => void }) {
  const save = useSaveCoursework();
  const { draft, problem, stale, change, submit } = useEditForm({
    current: courseworkDraft(education),
    editOf: courseworkEditOf,
    subject: "This education entry",
    baseVersion,
    save: (edit, onSuccess) => {
      save.mutate({ id: education.id, edit }, { onSuccess });
    },
    onSaved,
    onDone,
  });
  const courses = lineList(draft.courses);
  return (
    <EditForm
      label="Edit the coursework and rules"
      action="save the coursework and rules"
      problem={problem}
      stale={stale}
      error={save.error}
      pending={save.isPending}
      onSubmit={submit}
      onCancel={onDone}
    >
      <TextArea
        label="Coursework"
        hint="One course per line: the full list."
        value={draft.courses}
        onChange={(text) => {
          change({ courses: text });
        }}
        maxLength={LIMITS.text * 10}
        rows={5}
        autoFocus
      />
      <Rows
        legend="Coursework for a role type"
        noun="Role type's coursework"
        rows={draft.subsets}
        max={METADATA_LIMITS.roleTypes}
        empty="None: every resume lists the full coursework."
        onChange={(subsets) => {
          change({ subsets });
        }}
        makeRow={(key) => ({ key, roleType: "", courses: [] })}
      >
        {(subset, changeSubset) => (
          <div className="space-y-2">
            <SelectField
              label="Role type"
              value={subset.roleType}
              options={roleTypeOptions(roleTypes)}
              onChange={(roleType) => {
                changeSubset({ roleType });
              }}
            />
            <fieldset>
              <legend className={LABEL}>Courses to list</legend>
              {courses.length === 0 ? (
                <p className="text-sm text-stone-500">Add courses to the full list first.</p>
              ) : (
                <div className="mt-1 grid gap-1 sm:grid-cols-2">
                  {courses.map((course) => (
                    <CheckboxField
                      key={course}
                      label={course}
                      checked={subset.courses.includes(course)}
                      onChange={(checked) => {
                        changeSubset({
                          courses: checked
                            ? [...subset.courses, course]
                            : subset.courses.filter((each) => each !== course),
                        });
                      }}
                    />
                  ))}
                </div>
              )}
            </fieldset>
          </div>
        )}
      </Rows>
      <TextArea
        label="Rules"
        hint='One per line, such as "Never print the GPA."'
        value={draft.rules}
        onChange={(rules) => {
          change({ rules });
        }}
        maxLength={LIMITS.text * 5}
      />
    </EditForm>
  );
}

/** An education entry's full coursework, the courses to list for each role type, and its
 * rules (FR-PRF-7). */
export function Coursework({
  education,
  roleTypes,
  ...saving
}: Saving & { education: Education; roleTypes: readonly RoleType[] }) {
  return (
    <Block
      title="Coursework and rules"
      kind="block"
      editLabel="Edit coursework and rules"
      form={(onDone) => (
        <CourseworkForm
          education={education}
          roleTypes={roleTypes}
          onDone={onDone}
          {...saving}
        />
      )}
    >
      <Facts
        label="Coursework and rules"
        facts={[
          ["Coursework", education.courses.join(", ")],
          ...education.courseworkSubsets.map(
            (subset): [string, string] => [`For ${subset.roleType}`, subset.courses.join(", ")],
          ),
          [
            "Rules",
            education.rules.length > 0 && (
              <ul className="list-disc pl-4">
                {education.rules.map((rule) => (
                  <li key={rule}>{rule}</li>
                ))}
              </ul>
            ),
          ],
        ]}
      />
      {education.courses.length === 0 && education.rules.length === 0 && (
        <p className="text-stone-500">None yet.</p>
      )}
    </Block>
  );
}

/**
 * Edit a bullet's swaps: the approved phrasings a tailored resume can use instead of part or
 * all of it (FR-WRT-5). A new swap counts as approved by you.
 */
export function SwapsForm({
  bullet,
  baseVersion,
  onSaved,
  onDone,
}: Saving & { bullet: Bullet; onDone: () => void }) {
  const save = useSaveSwaps();
  const { draft, problem, stale, change, submit } = useEditForm({
    current: { swaps: swapDrafts(bullet.swaps) },
    editOf: swapsEditOf,
    subject: "This bullet",
    baseVersion,
    save: (edit, onSuccess) => {
      save.mutate({ id: bullet.id, edit }, { onSuccess });
    },
    onSaved,
    onDone,
  });
  return (
    <EditForm
      label="Edit the swaps"
      action="save the swaps"
      problem={problem}
      stale={stale}
      error={save.error}
      pending={save.isPending}
      onSubmit={submit}
      onCancel={onDone}
    >
      <p className="text-sm text-stone-700">{bullet.text}</p>
      <Rows<SwapDraft>
        legend="Swaps"
        noun="Swap"
        rows={draft.swaps}
        max={METADATA_LIMITS.list}
        empty="No swaps yet."
        onChange={(swaps) => {
          change({ swaps });
        }}
        makeRow={(key) => ({ key, text: "", replaces: "", contexts: "", origin: "approved" })}
      >
        {(swap, changeSwap) => (
          <div className="space-y-2">
            <TextField
              label="Phrasing"
              value={swap.text}
              onChange={(text) => {
                changeSwap({ text });
              }}
              maxLength={LIMITS.bullet}
              required
            />
            <div className="grid gap-2 sm:grid-cols-2">
              <TextField
                label="Replaces (optional)"
                hint="The part of the bullet it replaces. Blank for the whole bullet."
                value={swap.replaces}
                onChange={(replaces) => {
                  changeSwap({ replaces });
                }}
                maxLength={LIMITS.reason}
              />
              <TextField
                label="Fits (optional)"
                hint="Separated by commas, such as a role type or a posting."
                value={swap.contexts}
                onChange={(contexts) => {
                  changeSwap({ contexts });
                }}
                maxLength={LIMITS.text}
              />
            </div>
          </div>
        )}
      </Rows>
    </EditForm>
  );
}

/** A bullet's swaps, as its card lists them. */
export function SwapList({ swaps }: { swaps: Bullet["swaps"] }) {
  if (swaps.length === 0) {
    return null;
  }
  return (
    <ul aria-label="Swaps" className="mt-2 space-y-1 text-sm text-stone-700">
      {swaps.map((swap) => (
        <li key={`${swap.text}|${swap.replaces ?? ""}`}>
          <span className="text-stone-500">Swap{swap.replaces ? ` for "${swap.replaces}"` : ""}:</span>{" "}
          {swap.text}
          {swap.contexts.length > 0 && (
            <span className="text-stone-500"> ({swap.contexts.join(", ")})</span>
          )}
        </li>
      ))}
    </ul>
  );
}
