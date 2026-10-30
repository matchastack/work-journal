import {
  useSaveEducation,
  useSaveProject,
  useSaveRole,
  type Education,
  type Project,
  type Role,
} from "../../api/profile";
import {
  educationDraft,
  educationEditOf,
  LIMITS,
  projectDraft,
  projectEditOf,
  roleDraft,
  roleEditOf,
} from "./drafts";
import { EditForm, MonthField, SelectField, TextArea, TextField } from "./fields";
import { STATUS_LABELS } from "./labels";
import { useEditForm } from "./useEditForm";

interface FormProps<Item> {
  item: Item;
  baseVersion: number;
  onSaved: (message: string) => void;
  onDone: () => void;
}

/** Edit a role's organisation, title, location, link, dates and summary. */
export function RoleForm({ item, baseVersion, onSaved, onDone }: FormProps<Role>) {
  const save = useSaveRole();
  const { draft, problem, stale, change, submit } = useEditForm({
    current: roleDraft(item),
    editOf: roleEditOf,
    subject: "This role",
    baseVersion,
    save: (edit, onSuccess) => {
      save.mutate({ id: item.id, edit }, { onSuccess });
    },
    onSaved,
    onDone,
  });
  return (
    <EditForm
      label="Edit the role"
      action="save the role"
      problem={problem}
      stale={stale}
      error={save.error}
      pending={save.isPending}
      onSubmit={submit}
      onCancel={onDone}
    >
      <div className="grid gap-3 sm:grid-cols-2">
        <TextField
          label="Title"
          value={draft.position}
          onChange={(position) => {
            change({ position });
          }}
          maxLength={LIMITS.name}
          required
          autoFocus
        />
        <TextField
          label="Organisation"
          value={draft.name}
          onChange={(name) => {
            change({ name });
          }}
          maxLength={LIMITS.name}
          required
        />
        <TextField
          label="Location"
          value={draft.location}
          onChange={(location) => {
            change({ location });
          }}
          maxLength={LIMITS.name}
        />
        <TextField
          label="Link"
          inputMode="url"
          value={draft.url}
          onChange={(url) => {
            change({ url });
          }}
          maxLength={LIMITS.url}
        />
        <MonthField
          label="Start month"
          value={draft.startDate}
          onChange={(startDate) => {
            change({ startDate });
          }}
          required
        />
        <MonthField
          label="End month"
          hint="Leave it blank while it's your current role."
          value={draft.endDate}
          onChange={(endDate) => {
            change({ endDate });
          }}
        />
      </div>
      <TextArea
        label="Summary"
        value={draft.summary}
        onChange={(summary) => {
          change({ summary });
        }}
        maxLength={LIMITS.text}
      />
    </EditForm>
  );
}

/** Edit an education entry's institution, degree, area, honours, location, link and dates. */
export function EducationForm({ item, baseVersion, onSaved, onDone }: FormProps<Education>) {
  const save = useSaveEducation();
  const { draft, problem, stale, change, submit } = useEditForm({
    current: educationDraft(item),
    editOf: educationEditOf,
    subject: "This education entry",
    baseVersion,
    save: (edit, onSuccess) => {
      save.mutate({ id: item.id, edit }, { onSuccess });
    },
    onSaved,
    onDone,
  });
  return (
    <EditForm
      label="Edit the education entry"
      action="save the education entry"
      problem={problem}
      stale={stale}
      error={save.error}
      pending={save.isPending}
      onSubmit={submit}
      onCancel={onDone}
    >
      <div className="grid gap-3 sm:grid-cols-2">
        <TextField
          label="Degree"
          value={draft.studyType}
          onChange={(studyType) => {
            change({ studyType });
          }}
          maxLength={LIMITS.name}
          required
          autoFocus
        />
        <TextField
          label="Area of study"
          value={draft.area}
          onChange={(area) => {
            change({ area });
          }}
          maxLength={LIMITS.name}
          required
        />
        <TextField
          label="Institution"
          value={draft.institution}
          onChange={(institution) => {
            change({ institution });
          }}
          maxLength={LIMITS.name}
          required
        />
        <TextField
          label="Honours"
          value={draft.honours}
          onChange={(honours) => {
            change({ honours });
          }}
          maxLength={LIMITS.name}
        />
        <TextField
          label="Location"
          value={draft.location}
          onChange={(location) => {
            change({ location });
          }}
          maxLength={LIMITS.name}
        />
        <TextField
          label="Link"
          inputMode="url"
          value={draft.url}
          onChange={(url) => {
            change({ url });
          }}
          maxLength={LIMITS.url}
        />
        <MonthField
          label="Start month"
          value={draft.startDate}
          onChange={(startDate) => {
            change({ startDate });
          }}
          required
        />
        <MonthField
          label="End month"
          hint="Leave it blank while you're studying."
          value={draft.endDate}
          onChange={(endDate) => {
            change({ endDate });
          }}
        />
      </div>
    </EditForm>
  );
}

/** Edit a project's name, description, tech stack, link, dates, status and tags. */
export function ProjectForm({ item, baseVersion, onSaved, onDone }: FormProps<Project>) {
  const save = useSaveProject();
  const { draft, problem, stale, change, submit } = useEditForm({
    current: projectDraft(item),
    editOf: projectEditOf,
    subject: "This project",
    baseVersion,
    save: (edit, onSuccess) => {
      save.mutate({ id: item.id, edit }, { onSuccess });
    },
    onSaved,
    onDone,
  });
  return (
    <EditForm
      label="Edit the project"
      action="save the project"
      problem={problem}
      stale={stale}
      error={save.error}
      pending={save.isPending}
      onSubmit={submit}
      onCancel={onDone}
    >
      <TextField
        label="Name"
        value={draft.name}
        onChange={(name) => {
          change({ name });
        }}
        maxLength={LIMITS.name}
        required
        autoFocus
      />
      <TextArea
        label="Description"
        value={draft.description}
        onChange={(description) => {
          change({ description });
        }}
        maxLength={LIMITS.text}
        rows={2}
      />
      <TextField
        label="Tech stack, separated by commas"
        value={draft.keywords}
        onChange={(keywords) => {
          change({ keywords });
        }}
        maxLength={LIMITS.text}
      />
      <div className="grid gap-3 sm:grid-cols-3">
        <TextField
          label="Link"
          inputMode="url"
          value={draft.url}
          onChange={(url) => {
            change({ url });
          }}
          maxLength={LIMITS.url}
        />
        <MonthField
          label="Start month"
          value={draft.startDate}
          onChange={(startDate) => {
            change({ startDate });
          }}
        />
        <MonthField
          label="End month"
          value={draft.endDate}
          onChange={(endDate) => {
            change({ endDate });
          }}
        />
      </div>
      <div className="grid gap-3 sm:grid-cols-3">
        <SelectField
          label="Status"
          value={draft.status}
          options={STATUS_LABELS}
          onChange={(status) => {
            change({ status });
          }}
        />
        {draft.status !== "active" && (
          <TextField
            className="sm:col-span-2"
            label={draft.status === "benched" ? "Why it's benched" : "Note (optional)"}
            hint={
              draft.status === "benched"
                ? "Benched projects stay for the record, but never show on a resume."
                : "Planned projects never show until they exist."
            }
            value={draft.statusReason}
            onChange={(statusReason) => {
              change({ statusReason });
            }}
            maxLength={LIMITS.reason}
            required={draft.status === "benched"}
          />
        )}
      </div>
      <TextField
        label="Tags"
        hint="Lowercase, separated by commas."
        value={draft.tags}
        onChange={(tags) => {
          change({ tags });
        }}
        maxLength={LIMITS.text}
      />
    </EditForm>
  );
}
