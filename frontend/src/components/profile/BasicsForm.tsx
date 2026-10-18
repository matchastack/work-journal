import { useRef } from "react";

import { useSaveBasics, type Basics } from "../../api/profile";
import { LABEL, TEXT_BUTTON } from "../ui";
import { basicsDraft, basicsEditOf, LIMITS, type LinkDraft } from "./drafts";
import { EditForm, TextArea, TextField } from "./fields";
import { useEditForm } from "./useEditForm";

/**
 * Edit your name, label, contact details, location, summary and profiles elsewhere, such as
 * GitHub. Blank optional fields are cleared.
 */
export function BasicsForm({
  basics,
  baseVersion,
  onSaved,
  onDone,
}: {
  basics: Basics;
  baseVersion: number;
  onSaved: (message: string) => void;
  onDone: () => void;
}) {
  const save = useSaveBasics();
  const nextKey = useRef(basics.profiles.length);
  const { draft, problem, stale, change, submit } = useEditForm({
    current: basicsDraft(basics),
    editOf: basicsEditOf,
    subject: "Your basics",
    baseVersion,
    save: (edit, onSuccess) => {
      save.mutate(edit, { onSuccess });
    },
    onSaved,
    onDone,
  });
  const changeLink = (key: number, changes: Partial<LinkDraft>) => {
    change({
      profiles: draft.profiles.map((link) => (link.key === key ? { ...link, ...changes } : link)),
    });
  };
  return (
    <EditForm
      label="Edit your basics"
      action="save your basics"
      problem={problem}
      stale={stale}
      error={save.error}
      pending={save.isPending}
      onSubmit={submit}
      onCancel={onDone}
    >
      <div className="grid gap-3 sm:grid-cols-2">
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
        <TextField
          label="Label"
          hint="What you do, such as Software Engineer."
          value={draft.label}
          onChange={(label) => {
            change({ label });
          }}
          maxLength={LIMITS.name}
        />
        <TextField
          label="Email"
          inputMode="email"
          value={draft.email}
          onChange={(email) => {
            change({ email });
          }}
          maxLength={LIMITS.name}
        />
        <TextField
          label="Phone"
          inputMode="tel"
          value={draft.phone}
          onChange={(phone) => {
            change({ phone });
          }}
          maxLength={LIMITS.name}
        />
        <TextField
          label="Website"
          inputMode="url"
          value={draft.url}
          onChange={(url) => {
            change({ url });
          }}
          maxLength={LIMITS.url}
          className="sm:col-span-2"
        />
      </div>
      <div className="grid gap-3 sm:grid-cols-3">
        <TextField
          label="City"
          value={draft.city}
          onChange={(city) => {
            change({ city });
          }}
          maxLength={LIMITS.name}
        />
        <TextField
          label="Region"
          value={draft.region}
          onChange={(region) => {
            change({ region });
          }}
          maxLength={LIMITS.name}
        />
        <TextField
          label="Country code"
          hint="Such as US."
          value={draft.countryCode}
          onChange={(countryCode) => {
            change({ countryCode });
          }}
          maxLength={LIMITS.name}
        />
      </div>
      <TextArea
        label="Summary"
        value={draft.summary}
        onChange={(summary) => {
          change({ summary });
        }}
        maxLength={LIMITS.text}
        rows={4}
      />
      <fieldset className="space-y-2">
        <legend className={LABEL}>Profiles elsewhere</legend>
        {draft.profiles.map((link, index) => {
          const name = `Profile ${String(index + 1)}`;
          return (
            <fieldset key={link.key} className="rounded border border-stone-200 bg-white p-2">
              <legend className="px-1 text-xs text-stone-600">{name}</legend>
              <div className="grid gap-2 sm:grid-cols-3">
                <TextField
                  label="Network"
                  hint={index === 0 ? "Such as GitHub." : undefined}
                  value={link.network}
                  onChange={(network) => {
                    changeLink(link.key, { network });
                  }}
                  maxLength={LIMITS.name}
                  required
                />
                <TextField
                  label="Username"
                  value={link.username}
                  onChange={(username) => {
                    changeLink(link.key, { username });
                  }}
                  maxLength={LIMITS.name}
                />
                <TextField
                  label="Link"
                  inputMode="url"
                  value={link.url}
                  onChange={(url) => {
                    changeLink(link.key, { url });
                  }}
                  maxLength={LIMITS.url}
                />
              </div>
              <button
                type="button"
                onClick={() => {
                  change({ profiles: draft.profiles.filter((other) => other.key !== link.key) });
                }}
                className={`mt-2 ${TEXT_BUTTON}`}
              >
                Remove {name.toLowerCase()}
              </button>
            </fieldset>
          );
        })}
        {draft.profiles.length < LIMITS.profiles && (
          <button
            type="button"
            onClick={() => {
              const key = nextKey.current++;
              change({
                profiles: [...draft.profiles, { key, network: "", username: "", url: "" }],
              });
            }}
            className={TEXT_BUTTON}
          >
            Add a profile
          </button>
        )}
      </fieldset>
    </EditForm>
  );
}
