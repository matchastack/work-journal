import { queryOptions, useMutation, useQueryClient } from "@tanstack/react-query";

import { ApiError, detailOf } from "./auth";
import { api } from "./client";
import { resumesQuery } from "./resumes";
import type { components } from "./schema";

export type TailorPage = components["schemas"]["TailorPage"];
export type TailorDraft = components["schemas"]["TailorDraft"];
export type DraftSummary = components["schemas"]["DraftSummary"];
export type DraftWords = components["schemas"]["DraftWords"];
export type DraftSkill = components["schemas"]["DraftSkill"];
export type DraftRender = components["schemas"]["DraftRender"];
export type DraftOutline = components["schemas"]["DraftOutline"];
export type JobPosting = components["schemas"]["JobPosting"];
export type CoverageReport = components["schemas"]["CoverageReport"];
export type AtsReport = components["schemas"]["AtsReport"];
export type BulletUse = DraftWords["use"];
export type SkillDecision = NonNullable<DraftSkill["decision"]>;

/** How often a draft is read again while a job works on it: parsing and tailoring take a minute
 * or two; a render waits for a computer with TeX Live, which can take much longer. */
const WORKING_MS = 2000;
const WAITING_FOR_RENDER_MS = 5000;

interface Answer<T> {
  data?: T;
  error?: unknown;
  response: Response;
}

async function unwrap<T>(answer: Promise<Answer<T>>): Promise<T> {
  const { data, error, response } = await answer;
  if (data === undefined) {
    throw new ApiError(response.status, detailOf(error));
  }
  return data;
}

/** The drafts not yet downloaded, and the application history. */
export const tailorPageQuery = queryOptions({
  queryKey: ["tailor"],
  queryFn: () => unwrap(api.GET("/api/tailor")),
  refetchInterval: (query) =>
    query.state.data?.drafts.some(
      (draft) => draft.status === "parsing" || draft.status === "tailoring",
    )
      ? WORKING_MS * 2
      : false,
});

/** How soon to read a draft again, or false when nothing is at work on it. */
export function pollingInterval(draft: TailorDraft | undefined): number | false {
  if (draft === undefined) {
    return false;
  }
  if (draft.status === "parsing" || draft.status === "tailoring") {
    return WORKING_MS;
  }
  if (draft.status === "ready" && draft.render?.status === "waiting") {
    return WAITING_FOR_RENDER_MS;
  }
  return false;
}

/** One draft, read again while a job works on it. */
export function draftQuery(draftId: string) {
  return queryOptions({
    queryKey: ["tailor", "draft", draftId],
    queryFn: () =>
      unwrap(api.GET("/api/tailor/{draft_id}", { params: { path: { draft_id: draftId } } })),
    refetchInterval: (query) => pollingInterval(query.state.data),
  });
}

/** Where the draft's PDF opens in the browser, to preview it. */
export function draftPdfUrl(draftId: string): string {
  return `/api/tailor/${encodeURIComponent(draftId)}/pdf`;
}

/** A change to a draft. The API answers with the draft as it now is, which replaces the one
 * shown at once; the list of drafts is read again afterwards. */
function useDraftChange<Input>(
  draftId: string,
  send: (input: Input) => Promise<Answer<TailorDraft>>,
) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: Input) => unwrap(send(input)),
    onSuccess: (draft) => {
      queryClient.setQueryData(draftQuery(draftId).queryKey, draft);
    },
    onSettled: () =>
      queryClient.invalidateQueries({ queryKey: tailorPageQuery.queryKey, exact: true }),
  });
}

function draftPath(draftId: string) {
  return { path: { draft_id: draftId } };
}

/** Paste a posting: jobs parse it, then tailor a resume to it. */
export function useNewDraft() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (text: string) => unwrap(api.POST("/api/tailor", { body: { text } })),
    onSuccess: (draft) => {
      queryClient.setQueryData(draftQuery(draft.id).queryKey, draft);
    },
    onSettled: () =>
      queryClient.invalidateQueries({ queryKey: tailorPageQuery.queryKey, exact: true }),
  });
}

/** Choose a reworded bullet's words. */
export function useChooseWords(draftId: string) {
  return useDraftChange(draftId, ({ bulletId, use }: { bulletId: string; use: BulletUse }) =>
    api.PUT("/api/tailor/{draft_id}/bullets/{bullet_id}", {
      params: { path: { draft_id: draftId, bullet_id: bulletId } },
      body: { use },
    }),
  );
}

/** Approve a new phrasing: it's saved as a swap on the bullet, and the draft uses it. */
export function useApprovePhrasing(draftId: string) {
  return useDraftChange(draftId, (bulletId: string) =>
    api.POST("/api/tailor/{draft_id}/bullets/{bullet_id}/approve", {
      params: { path: { draft_id: draftId, bullet_id: bulletId } },
    }),
  );
}

/** Confirm or drop a skill marked verify-before-shipping, or take the decision back (null). */
export function useDecideSkill(draftId: string) {
  return useDraftChange(
    draftId,
    ({ skill, decision }: { skill: string; decision: SkillDecision | null }) =>
      api.PUT("/api/tailor/{draft_id}/skills", {
        params: draftPath(draftId),
        body: { skill, decision },
      }),
  );
}

/** Try a failed draft again. */
export function useRetryDraft(draftId: string) {
  return useDraftChange(draftId, () =>
    api.POST("/api/tailor/{draft_id}/retry", { params: draftPath(draftId) }),
  );
}

/** Discard a draft not yet downloaded. */
export function useDiscardDraft(draftId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async () => {
      const { error, response } = await api.DELETE("/api/tailor/{draft_id}", {
        params: draftPath(draftId),
      });
      if (!response.ok) {
        throw new ApiError(response.status, detailOf(error));
      }
    },
    onSuccess: () => {
      queryClient.removeQueries({ queryKey: draftQuery(draftId).queryKey });
    },
    onSettled: () =>
      queryClient.invalidateQueries({ queryKey: tailorPageQuery.queryKey, exact: true }),
  });
}

/** Today's date in the browser's time zone, e.g. `2026-10-31`: the application's date. */
export function localDate(now: Date = new Date()): string {
  const pad = (value: number) => String(value).padStart(2, "0");
  return `${String(now.getFullYear())}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
}

/** Where the logged application's PDF downloads. */
export function applicationPdfUrl(sha256: string): string {
  return `/api/resumes/pdf/${encodeURIComponent(sha256)}?download=true`;
}

/** Save a file the API serves, as a link with `download` would. */
export function startDownload(url: string): void {
  const link = document.createElement("a");
  link.href = url;
  link.download = "";
  document.body.append(link);
  link.click();
  link.remove();
}

/** Log the draft as an application, once, then download its PDF. */
export function useDownload(draftId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/tailor/{draft_id}/application", {
          params: draftPath(draftId),
          body: { appliedOn: localDate() },
        }),
      ),
    onSuccess: (draft) => {
      queryClient.setQueryData(draftQuery(draftId).queryKey, draft);
      if (draft.application) {
        startDownload(applicationPdfUrl(draft.application.pdfSha256));
      }
    },
    onSettled: async () => {
      await queryClient.invalidateQueries({ queryKey: tailorPageQuery.queryKey, exact: true });
      await queryClient.invalidateQueries({ queryKey: resumesQuery.queryKey });
    },
  });
}
