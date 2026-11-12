import { useSaveBasics, type Basics } from "../../api/profile";
import { basicsDraft, basicsEditOf, LIMITS, type LinkDraft } from "./drafts";
import { EditForm, Rows, TextArea, TextField } from "./fields";
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
  const { draft, change, form } = useEditForm({
    current: basicsDraft(basics),
    editOf: basicsEditOf,
    subject: "Your basics",
    baseVersion,
    mutation: useSaveBasics(),
    input: (edit) => edit,
    onSaved,
    onDone,
  });
  return (
    <EditForm label="Edit your basics" action="save your basics" {...form}>
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
      <Rows
        legend="Profiles elsewhere"
        noun="Profile"
        rows={draft.profiles}
        max={LIMITS.profiles}
        onChange={(profiles) => {
          change({ profiles });
        }}
        makeRow={(key): LinkDraft => ({ key, network: "", username: "", url: "" })}
      >
        {(link, changeLink, index) => (
          <div className="grid gap-2 sm:grid-cols-3">
            <TextField
              label="Network"
              hint={index === 0 ? "Such as GitHub." : undefined}
              value={link.network}
              onChange={(network) => {
                changeLink({ network });
              }}
              maxLength={LIMITS.name}
              required
            />
            <TextField
              label="Username"
              value={link.username}
              onChange={(username) => {
                changeLink({ username });
              }}
              maxLength={LIMITS.name}
            />
            <TextField
              label="Link"
              inputMode="url"
              value={link.url}
              onChange={(url) => {
                changeLink({ url });
              }}
              maxLength={LIMITS.url}
            />
          </div>
        )}
      </Rows>
    </EditForm>
  );
}
