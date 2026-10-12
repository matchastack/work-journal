import { queryOptions, useMutation, useQueryClient } from "@tanstack/react-query";

import { ApiError, detailOf } from "./auth";
import { api } from "./client";
import type { components } from "./schema";

export type Journal = components["schemas"]["Journal"];
export type JournalEntry = components["schemas"]["JournalEntryView"];
export type JournalMessage = components["schemas"]["JournalMessageView"];
export type JournalFact = components["schemas"]["JournalFact"];
export type Citation = components["schemas"]["Citation"];
export type RoleOption = components["schemas"]["RoleOption"];
export type ProjectOption = components["schemas"]["ProjectOption"];
export type FactEdit = components["schemas"]["FactEdit"];
export type Metric = components["schemas"]["Metric"];
export type FactKind = FactEdit["kind"];
export type Ownership = NonNullable<FactEdit["ownership"]>;
export type Qualifier = Metric["qualifier"];

/** The whole journal, oldest entry first. */
export const journalQuery = queryOptions({
  queryKey: ["journal"],
  queryFn: async (): Promise<Journal> => {
    const { data, error, response } = await api.GET("/api/journal");
    if (data === undefined) {
      throw new ApiError(response.status, detailOf(error));
    }
    return data;
  },
});

/** Where the journal downloads, with times in the browser's time zone in the Markdown. */
export function exportUrls(timeZone: string = Intl.DateTimeFormat().resolvedOptions().timeZone) {
  return {
    markdown: `/api/journal/export.md?${new URLSearchParams({ tz: timeZone }).toString()}`,
    json: "/api/journal/export.json",
  };
}

interface Sent {
  error?: unknown;
  response: Response;
}

/** A change to the journal. Whether it works or not, the journal is read again afterwards. */
function useJournalChange<Input>(send: (input: Input) => Promise<Sent>) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (input: Input) => {
      const { error, response } = await send(input);
      if (!response.ok) {
        throw new ApiError(response.status, detailOf(error));
      }
    },
    onSettled: () => queryClient.invalidateQueries({ queryKey: journalQuery.queryKey }),
  });
}

export function useEditMessage() {
  return useJournalChange(({ id, text }: { id: string; text: string }) =>
    api.PATCH("/api/journal/messages/{message_id}", {
      params: { path: { message_id: id } },
      body: { text },
    }),
  );
}

export function useDeleteMessage() {
  return useJournalChange((id: string) =>
    api.DELETE("/api/journal/messages/{message_id}", { params: { path: { message_id: id } } }),
  );
}

/** Deleting an entry or a fact the profile cites needs `confirm`. */
export function useDeleteEntry() {
  return useJournalChange(({ id, confirm }: { id: string; confirm: boolean }) =>
    api.DELETE("/api/journal/entries/{entry_id}", {
      params: { path: { entry_id: id }, query: { confirm } },
    }),
  );
}

export function useEditFact() {
  return useJournalChange(({ id, edit }: { id: string; edit: FactEdit }) =>
    api.PUT("/api/journal/facts/{fact_id}", { params: { path: { fact_id: id } }, body: edit }),
  );
}

export function useDeleteFact() {
  return useJournalChange(({ id, confirm }: { id: string; confirm: boolean }) =>
    api.DELETE("/api/journal/facts/{fact_id}", {
      params: { path: { fact_id: id }, query: { confirm } },
    }),
  );
}

/** What to tell the owner when a change didn't work. */
export function failureMessage(action: string, error: Error): string {
  if (error instanceof ApiError && error.detail !== undefined) {
    return `Couldn't ${action}: ${error.detail}.`;
  }
  if (error instanceof ApiError && error.status === 422) {
    return `Couldn't ${action}: check the fields.`;
  }
  return `Couldn't ${action}. Try again.`;
}
