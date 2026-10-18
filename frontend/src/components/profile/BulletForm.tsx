import { useSaveBullet, type Bullet, type RoleType } from "../../api/profile";
import { bulletDraft, bulletEditOf, LIMITS } from "./drafts";
import { EditForm, SelectField, TextArea, TextField } from "./fields";
import { STATUS_LABELS, STRENGTH_LABELS, VERIFICATION_LABELS } from "./labels";
import { useEditForm } from "./useEditForm";

/** Tags are short labels, often the role types the bullet suits. */
function tagsHint(roleTypes: readonly RoleType[]): string {
  const hint = "Lowercase, separated by commas.";
  return roleTypes.length === 0
    ? hint
    : `${hint} Your role types: ${roleTypes.map((type) => type.id).join(", ")}.`;
}

/**
 * Edit a bullet's text, status, strength, verification and tags. Benching it needs a reason
 * (FR-PRF-4). The text is your own words, so the verifier doesn't check it; the resume rules
 * still do, once it's saved.
 */
export function BulletForm({
  bullet,
  roleTypes,
  baseVersion,
  onSaved,
  onDone,
}: {
  bullet: Bullet;
  roleTypes: readonly RoleType[];
  baseVersion: number;
  onSaved: (message: string) => void;
  onDone: () => void;
}) {
  const save = useSaveBullet();
  const { draft, problem, stale, change, submit } = useEditForm({
    current: bulletDraft(bullet),
    editOf: bulletEditOf,
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
      label="Edit the bullet"
      action="save the bullet"
      problem={problem}
      stale={stale}
      error={save.error}
      pending={save.isPending}
      onSubmit={submit}
      onCancel={onDone}
    >
      <TextArea
        label="Bullet text"
        value={draft.text}
        onChange={(text) => {
          change({ text });
        }}
        maxLength={LIMITS.bullet}
        required
        autoFocus
      />
      <div className="grid gap-3 sm:grid-cols-3">
        <SelectField
          label="Status"
          value={draft.status}
          options={STATUS_LABELS}
          onChange={(status) => {
            change({ status });
          }}
        />
        <SelectField
          label="Strength"
          value={draft.strength}
          options={STRENGTH_LABELS}
          onChange={(strength) => {
            change({ strength });
          }}
        />
        <SelectField
          label="Verification"
          value={draft.verification}
          options={VERIFICATION_LABELS}
          onChange={(verification) => {
            change({ verification });
          }}
        />
      </div>
      {draft.status !== "active" && (
        <TextField
          label={draft.status === "benched" ? "Why it's benched" : "Note (optional)"}
          hint={
            draft.status === "benched"
              ? "Benched bullets stay for the record, but never show on a resume."
              : "Planned bullets never show until they're true."
          }
          value={draft.statusReason}
          onChange={(statusReason) => {
            change({ statusReason });
          }}
          maxLength={LIMITS.reason}
          required={draft.status === "benched"}
        />
      )}
      <TextField
        label="Verification note (optional)"
        value={draft.verificationNote}
        onChange={(verificationNote) => {
          change({ verificationNote });
        }}
        maxLength={LIMITS.reason}
      />
      <TextField
        label="Tags"
        hint={tagsHint(roleTypes)}
        value={draft.tags}
        onChange={(tags) => {
          change({ tags });
        }}
        maxLength={LIMITS.text}
      />
    </EditForm>
  );
}
