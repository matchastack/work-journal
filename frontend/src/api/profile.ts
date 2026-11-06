import { queryOptions, useMutation, useQueryClient } from "@tanstack/react-query";

import { ApiError, detailOf } from "./auth";
import { api } from "./client";
import type { components } from "./schema";

type Schemas = components["schemas"];
export type ProfilePage = Schemas["ProfilePage"];
export type Profile = Schemas["Profile"];
export type Basics = Schemas["Basics"];
export type Role = Schemas["Role"];
export type Education = Schemas["Education"];
export type Project = Schemas["Project"];
export type Bullet = Schemas["Bullet"];
export type RoleType = Schemas["RoleType"];
export type LintFinding = Schemas["LintFinding"];
export type LintView = Schemas["LintView"];
export type VersionSummary = Schemas["VersionSummary"];
export type VersionChange = Schemas["VersionChange"];
export type BasicsEdit = Schemas["BasicsEdit"];
export type RoleEdit = Schemas["RoleEdit"];
export type EducationEdit = Schemas["EducationEdit"];
export type ProjectEdit = Schemas["ProjectEdit"];
export type BulletEdit = Schemas["BulletEdit"];
export type RoleRulesEdit = Schemas["RoleRulesEdit"];
export type CourseworkEdit = Schemas["CourseworkEdit"];
export type SwapsEdit = Schemas["SwapsEdit"];
export type RoleTypesEdit = Schemas["RoleTypesEdit"];
export type SkillsEdit = Schemas["SkillsEdit"];
export type SkillPresetsEdit = Schemas["SkillPresetsEdit"];
export type SummariesEdit = Schemas["SummariesEdit"];
export type OpenQuestionEdit = Schemas["OpenQuestionEdit"];
export type Swap = Schemas["Swap"];
export type Skill = Schemas["Skill"];
export type SkillPreset = Schemas["SkillPreset"];
export type Summary = Schemas["Summary"];
export type OpenQuestion = Schemas["OpenQuestion"];
export type ItemStatus = Bullet["status"];
export type Strength = Bullet["strength"];
export type Verification = Bullet["verification"];
export type Author = VersionSummary["author"];

interface Answer<T> {
  data?: T;
  error?: unknown;
  response: Response;
}

async function read<T>(request: Promise<Answer<T>>): Promise<T> {
  const { data, error, response } = await request;
  if (data === undefined) {
    throw new ApiError(response.status, detailOf(error));
  }
  return data;
}

/** The latest version of the profile, with its resume-rule findings. */
export const profileQuery = queryOptions({
  queryKey: ["profile"],
  queryFn: () => read(api.GET("/api/profile")),
});

/** Every version, newest first. */
export const versionsQuery = queryOptions({
  queryKey: ["profile", "versions"],
  queryFn: () => read(api.GET("/api/profile/versions")),
});

/** A version and what it changed. Versions never change, so it's read once. */
export function versionQuery(number: number) {
  return queryOptions({
    queryKey: ["profile-version", number],
    queryFn: () =>
      read(api.GET("/api/profile/versions/{number}", { params: { path: { number } } })),
    staleTime: Infinity,
  });
}

/**
 * A change to the profile. The API answers with the profile as it now is, which replaces the
 * one on the page at once; the profile and its history are read again afterwards either way, so
 * a change refused because the profile moved on (409) shows the newest version.
 */
function useProfileChange<Input>(send: (input: Input) => Promise<Answer<ProfilePage>>) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: Input) => read(send(input)),
    onSuccess: (page) => {
      queryClient.setQueryData(profileQuery.queryKey, page);
    },
    onSettled: () => queryClient.invalidateQueries({ queryKey: profileQuery.queryKey }),
  });
}

export function useSaveBasics() {
  return useProfileChange((edit: BasicsEdit) => api.PUT("/api/profile/basics", { body: edit }));
}

export function useSaveRole() {
  return useProfileChange(({ id, edit }: { id: string; edit: RoleEdit }) =>
    api.PUT("/api/profile/work/{role_id}", { params: { path: { role_id: id } }, body: edit }),
  );
}

export function useSaveEducation() {
  return useProfileChange(({ id, edit }: { id: string; edit: EducationEdit }) =>
    api.PUT("/api/profile/education/{education_id}", {
      params: { path: { education_id: id } },
      body: edit,
    }),
  );
}

export function useSaveProject() {
  return useProfileChange(({ id, edit }: { id: string; edit: ProjectEdit }) =>
    api.PUT("/api/profile/projects/{project_id}", {
      params: { path: { project_id: id } },
      body: edit,
    }),
  );
}

export function useSaveBullet() {
  return useProfileChange(({ id, edit }: { id: string; edit: BulletEdit }) =>
    api.PUT("/api/profile/bullets/{bullet_id}", {
      params: { path: { bullet_id: id } },
      body: edit,
    }),
  );
}

export function useSaveRoleRules() {
  return useProfileChange(({ id, edit }: { id: string; edit: RoleRulesEdit }) =>
    api.PUT("/api/profile/work/{role_id}/rules", {
      params: { path: { role_id: id } },
      body: edit,
    }),
  );
}

export function useSaveCoursework() {
  return useProfileChange(({ id, edit }: { id: string; edit: CourseworkEdit }) =>
    api.PUT("/api/profile/education/{education_id}/coursework", {
      params: { path: { education_id: id } },
      body: edit,
    }),
  );
}

export function useSaveSwaps() {
  return useProfileChange(({ id, edit }: { id: string; edit: SwapsEdit }) =>
    api.PUT("/api/profile/bullets/{bullet_id}/swaps", {
      params: { path: { bullet_id: id } },
      body: edit,
    }),
  );
}

export function useSaveRoleTypes() {
  return useProfileChange((edit: RoleTypesEdit) =>
    api.PUT("/api/profile/role-types", { body: edit }),
  );
}

export function useSaveSkills() {
  return useProfileChange((edit: SkillsEdit) => api.PUT("/api/profile/skills", { body: edit }));
}

export function useSaveSkillPresets() {
  return useProfileChange((edit: SkillPresetsEdit) =>
    api.PUT("/api/profile/skill-presets", { body: edit }),
  );
}

export function useSaveSummaries() {
  return useProfileChange((edit: SummariesEdit) =>
    api.PUT("/api/profile/summaries", { body: edit }),
  );
}

/** Add an open question (`id` null), or change one. */
export function useSaveQuestion() {
  return useProfileChange(({ id, edit }: { id: string | null; edit: OpenQuestionEdit }) =>
    id === null
      ? api.POST("/api/profile/open-questions", { body: edit })
      : api.PUT("/api/profile/open-questions/{question_id}", {
          params: { path: { question_id: id } },
          body: edit,
        }),
  );
}

/** Save a copy of an old version as the newest one. */
export function useRestore() {
  return useProfileChange(({ number, baseVersion }: { number: number; baseVersion: number }) =>
    api.POST("/api/profile/versions/{number}/restore", {
      params: { path: { number } },
      body: { baseVersion },
    }),
  );
}
