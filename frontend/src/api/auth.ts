import { queryOptions, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "./client";
import type { components } from "./schema";

export type Me = components["schemas"]["Me"];

/** The API answered with an error status, and said why in `detail` if it could. */
export class ApiError extends Error {
  readonly status: number;
  readonly detail: string | undefined;

  constructor(status: number, detail?: string) {
    super(`The API answered with status ${String(status)}`);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

/** The API's own reason for an error, from FastAPI's `{"detail": "..."}` body. */
export function detailOf(body: unknown): string | undefined {
  if (typeof body === "object" && body !== null && "detail" in body) {
    return typeof body.detail === "string" ? body.detail : undefined;
  }
  return undefined;
}

/** Who is signed in, or null when nobody is. */
export const meQuery = queryOptions({
  queryKey: ["me"],
  queryFn: async (): Promise<Me | null> => {
    const { data, response } = await api.GET("/auth/me");
    if (response.status === 401) {
      return null;
    }
    if (data === undefined) {
      throw new ApiError(response.status);
    }
    return data;
  },
});

export function useMe() {
  return useQuery(meQuery);
}

export function useSignOut() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async () => {
      const { response } = await api.POST("/auth/logout");
      if (!response.ok) {
        throw new ApiError(response.status);
      }
    },
    onSuccess: () => {
      // Forget the signed-out person's data, which also shows the sign-in page.
      queryClient.removeQueries({ predicate: (query) => query.queryKey[0] !== "me" });
      queryClient.setQueryData(meQuery.queryKey, null);
    },
  });
}
