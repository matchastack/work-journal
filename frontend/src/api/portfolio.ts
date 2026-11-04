import { queryOptions, useMutation, useQueryClient } from "@tanstack/react-query";

import { ApiError, detailOf } from "./auth";
import { api } from "./client";
import type { components } from "./schema";

export type PortfolioView = components["schemas"]["PortfolioView"];
export type PublishedResume = components["schemas"]["PublishedResume"];
export type ResumeOption = components["schemas"]["ResumeOption"];
export type ChosenResume = components["schemas"]["ChosenResume"];
export type TermMatch = components["schemas"]["TermMatch"];

/** Terms for a sentence, such as `"Atlas" and "Project Falcon"`. */
export function quotedTerms(terms: readonly string[]): string {
  const names = terms.map((term) => `"${term}"`);
  const last = names.pop();
  if (last === undefined) {
    return "";
  }
  return names.length === 0 ? last : `${names.join(", ")} and ${last}`;
}

/** The page as it's published, or as publishing it now would show it, for the owner only. */
export const PREVIEW_URL = "/api/portfolio/preview";

/** Which resume a choice is, whatever its label: as the API tells them apart. */
export function resumeKey(resume: { source: string; applicationId?: string | null }): string {
  return resume.applicationId == null ? "master" : `application:${resume.applicationId}`;
}

/** Whether the page is published, the version it shows, its settings and its resumes. */
export const portfolioQuery = queryOptions({
  queryKey: ["portfolio"],
  queryFn: async (): Promise<PortfolioView> => {
    const { data, error, response } = await api.GET("/api/portfolio");
    if (data === undefined) {
      throw new ApiError(response.status, detailOf(error));
    }
    return data;
  },
});

interface Answer {
  data?: PortfolioView;
  error?: unknown;
  response: Response;
}

/** A change to the page. The API answers with the page as it now is. */
function usePortfolioChange<Input>(send: (input: Input) => Promise<Answer>) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (input: Input): Promise<PortfolioView> => {
      const { data, error, response } = await send(input);
      if (data === undefined) {
        throw new ApiError(response.status, detailOf(error));
      }
      return data;
    },
    onSuccess: (view) => {
      queryClient.setQueryData(portfolioQuery.queryKey, view);
    },
  });
}

export function usePublish() {
  return usePortfolioChange(() => api.POST("/api/portfolio/publish"));
}

export function useUnpublish() {
  return usePortfolioChange(() => api.POST("/api/portfolio/unpublish"));
}

export function useSaveResumes() {
  return usePortfolioChange((resumes: PublishedResume[]) =>
    api.PUT("/api/portfolio/resumes", { body: { resumes } }),
  );
}
