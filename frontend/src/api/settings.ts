import { queryOptions, useMutation, useQueryClient } from "@tanstack/react-query";

import { ApiError, detailOf, meQuery } from "./auth";
import { api } from "./client";
import type { components } from "./schema";

export type SettingsView = components["schemas"]["SettingsView"];
export type ReminderView = components["schemas"]["ReminderView"];
export type ReminderSettings = components["schemas"]["ReminderSettings"];
export type Weekday = components["schemas"]["ReminderSchedule"]["days"][number];
export type PortfolioSettings = components["schemas"]["PortfolioSettings"];
export type Visibility = PortfolioSettings["defaultVisibility"];
export type StyleNotes = components["schemas"]["StyleNotes"];
export type StyleNotesChange = components["schemas"]["StyleNotesChange"];
export type ConfidentialTerms = components["schemas"]["ConfidentialTerms"];
export type TelegramDeepLink = components["schemas"]["TelegramDeepLink"];

/** Where everything stored for the owner downloads, as one JSON file. */
export const EXPORT_URL = "/api/settings/export.json";

/** Every preference, as the Settings page shows them. */
export const settingsQuery = queryOptions({
  queryKey: ["settings"],
  queryFn: async (): Promise<SettingsView> => {
    const { data, error, response } = await api.GET("/api/settings");
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

/** Saving one group of settings. The API answers with the group as saved, which replaces it. */
function useSaveGroup<Key extends keyof SettingsView, Input>(
  key: Key,
  send: (input: Input) => Promise<Answer<SettingsView[Key]>>,
) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (input: Input): Promise<SettingsView[Key]> => {
      const { data, error, response } = await send(input);
      if (data === undefined) {
        throw new ApiError(response.status, detailOf(error));
      }
      return data;
    },
    onSuccess: (saved) => {
      queryClient.setQueryData(settingsQuery.queryKey, (settings) =>
        settings === undefined ? settings : { ...settings, [key]: saved },
      );
    },
  });
}

export function useSaveReminders() {
  return useSaveGroup("reminders", (body: ReminderSettings) =>
    api.PUT("/api/settings/reminders", { body }),
  );
}

export function useSavePortfolio() {
  return useSaveGroup("portfolio", (body: PortfolioSettings) =>
    api.PUT("/api/settings/portfolio", { body }),
  );
}

export function useSaveStyleNotes() {
  return useSaveGroup("styleNotes", (body: StyleNotesChange) =>
    api.PUT("/api/settings/style-notes", { body }),
  );
}

export function useSaveConfidentialTerms() {
  return useSaveGroup("confidentialTerms", (body: ConfidentialTerms) =>
    api.PUT("/api/settings/confidential-terms", { body }),
  );
}

/** A one-time link to the bot, for 15 minutes, that links the chat it's opened in. */
export function useNewTelegramLink() {
  return useMutation({
    mutationFn: async (): Promise<TelegramDeepLink> => {
      const { data, error, response } = await api.POST("/api/telegram/link");
      if (data === undefined) {
        throw new ApiError(response.status, detailOf(error));
      }
      return data;
    },
  });
}

/**
 * Delete the account and everything stored for it, confirmed with the GitHub username. The
 * session ends with it, so the app forgets the data it holds and shows the sign-in page.
 */
export function useDeleteAllData() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (confirm: string) => {
      const { error, response } = await api.DELETE("/api/settings/data", { body: { confirm } });
      if (!response.ok) {
        throw new ApiError(response.status, detailOf(error));
      }
    },
    onSuccess: () => {
      queryClient.removeQueries({ predicate: (query) => query.queryKey[0] !== "me" });
      queryClient.setQueryData(meQuery.queryKey, null);
    },
  });
}
