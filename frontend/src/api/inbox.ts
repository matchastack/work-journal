import { queryOptions, useMutation, useQueryClient } from "@tanstack/react-query";

import { ApiError, detailOf } from "./auth";
import { api } from "./client";
import { journalQuery } from "./journal";
import type { components } from "./schema";

export type Inbox = components["schemas"]["Inbox"];
export type InboxChangeSet = components["schemas"]["InboxChangeSet"];
export type InboxOp = components["schemas"]["InboxOp"];
export type OpStatus = InboxOp["status"];
export type Output = InboxOp["outputs"][number];
export type VerifierReport = components["schemas"]["VerifierReport"];

/** The change sets waiting for review, oldest first. */
export const inboxQuery = queryOptions({
  queryKey: ["inbox"],
  queryFn: async (): Promise<Inbox> => {
    const { data, error, response } = await api.GET("/api/inbox");
    if (data === undefined) {
      throw new ApiError(response.status, detailOf(error));
    }
    return data;
  },
});

interface Answer<T> {
  data?: T;
  error?: unknown;
  response: Response;
}

/**
 * A decision on a change set. The API answers with the change set as it now is, which replaces
 * the one in the inbox at once; the inbox is read again afterwards either way.
 */
function useReview<Input>(send: (input: Input) => Promise<Answer<InboxChangeSet>>) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (input: Input): Promise<InboxChangeSet> => {
      const { data, error, response } = await send(input);
      if (data === undefined) {
        throw new ApiError(response.status, detailOf(error));
      }
      return data;
    },
    onSuccess: (changed) => {
      queryClient.setQueryData(inboxQuery.queryKey, (inbox) =>
        inbox === undefined
          ? inbox
          : {
              ...inbox,
              changeSets: inbox.changeSets.map((changeSet) =>
                changeSet.id === changed.id ? changed : changeSet,
              ),
            },
      );
    },
    onSettled: () => queryClient.invalidateQueries({ queryKey: inboxQuery.queryKey }),
  });
}

interface At {
  changeSetId: string;
  position: number;
}

function path({ changeSetId, position }: At) {
  return { path: { change_set_id: changeSetId, position } };
}

/** Accept or reject a change, or set it back to waiting (`proposed`). */
export function useDecide() {
  return useReview(
    ({ status, reason, ...at }: At & { status: OpStatus; reason?: string | undefined }) =>
      api.POST("/api/inbox/{change_set_id}/ops/{position}/decision", {
        params: path(at),
        body: { status, reason: reason ?? null },
      }),
  );
}

/** Edit a bullet's text; the verifier checks it again. */
export function useEditText() {
  return useReview(({ text, ...at }: At & { text: string }) =>
    api.PUT("/api/inbox/{change_set_id}/ops/{position}/text", { params: path(at), body: { text } }),
  );
}

/** Accept every waiting change whose text passed the verifier. */
export function useAcceptPassed() {
  return useReview((changeSetId: string) =>
    api.POST("/api/inbox/{change_set_id}/accept-passed", {
      params: { path: { change_set_id: changeSetId } },
    }),
  );
}

/** Apply the accepted changes as a new profile version. Facts' citations may change with it. */
export function useApply() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (changeSetId: string): Promise<number | null> => {
      const { data, error, response } = await api.POST("/api/inbox/{change_set_id}/apply", {
        params: { path: { change_set_id: changeSetId } },
      });
      if (data === undefined) {
        throw new ApiError(response.status, detailOf(error));
      }
      return data.version;
    },
    onSettled: async () => {
      await queryClient.invalidateQueries({ queryKey: inboxQuery.queryKey });
      await queryClient.invalidateQueries({ queryKey: journalQuery.queryKey });
    },
  });
}
