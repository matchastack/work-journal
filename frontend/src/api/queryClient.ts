import { MutationCache, QueryCache, QueryClient } from "@tanstack/react-query";

import { ApiError, meQuery } from "./auth";

/**
 * The cache for API data. Requests that fail to reach the API are retried; the API's own error
 * answers aren't. A 401, to a read or a change, means the session ended (it expired, or signed
 * out elsewhere), so the app goes back to the sign-in page.
 */
export function createQueryClient({ retries = 2 }: { retries?: number } = {}): QueryClient {
  const signOutOn401 = (error: Error) => {
    if (error instanceof ApiError && error.status === 401) {
      queryClient.setQueryData(meQuery.queryKey, null);
    }
  };
  const queryClient: QueryClient = new QueryClient({
    queryCache: new QueryCache({ onError: signOutOn401 }),
    mutationCache: new MutationCache({ onError: signOutOn401 }),
    defaultOptions: {
      queries: {
        retry: (failures, error) => !(error instanceof ApiError) && failures < retries,
      },
    },
  });
  return queryClient;
}
