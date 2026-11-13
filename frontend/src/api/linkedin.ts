import { queryOptions, useMutation, useQueryClient } from "@tanstack/react-query";

import { ApiError, detailOf } from "./auth";
import { api } from "./client";
import type { components } from "./schema";

export type LinkedInPage = components["schemas"]["LinkedInPage"];
export type LinkedInPack = components["schemas"]["LinkedInPagePack"];
export type LinkedInSection = components["schemas"]["LinkedInPageSection"];
export type Marked = components["schemas"]["Marked"];

/** The LinkedIn pack last written, with only the sections that changed since it was marked
 * done. */
export const linkedinQuery = queryOptions({
  queryKey: ["linkedin"],
  queryFn: async (): Promise<LinkedInPage> => {
    const { data, error, response } = await api.GET("/api/linkedin");
    if (data === undefined) {
      throw new ApiError(response.status, detailOf(error));
    }
    return data;
  },
});

/**
 * Mark the pack with `digest` done, once its sections are pasted into LinkedIn. The page is read
 * again afterwards, whether it worked or not: when the pack was written again since the page
 * loaded, the API refuses, and the new pack then shows.
 */
export function useMarkDone() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (digest: string): Promise<Marked> => {
      const { data, error, response } = await api.POST("/api/linkedin/done", {
        body: { digest },
      });
      if (data === undefined) {
        throw new ApiError(response.status, detailOf(error));
      }
      return data;
    },
    onSettled: () => queryClient.invalidateQueries({ queryKey: linkedinQuery.queryKey }),
  });
}

export type MarkDone = ReturnType<typeof useMarkDone>;
