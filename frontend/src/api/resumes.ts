import { queryOptions } from "@tanstack/react-query";

import { ApiError, detailOf } from "./auth";
import { api } from "./client";
import type { components } from "./schema";

export type Resumes = components["schemas"]["Resumes"];
export type MasterResume = components["schemas"]["MasterResume"];
export type TailoredResume = components["schemas"]["TailoredResume"];
export type ResumePdf = components["schemas"]["ResumePdf"];
export type ResumeCut = components["schemas"]["ResumeCut"];
export type LintReport = components["schemas"]["LintReport"];
export type Finding = LintReport["findings"][number];

/** The master resume from the latest refresh, and every tailored resume, newest first. */
export const resumesQuery = queryOptions({
  queryKey: ["resumes"],
  queryFn: async (): Promise<Resumes> => {
    const { data, error, response } = await api.GET("/api/resumes");
    if (data === undefined) {
      throw new ApiError(response.status, detailOf(error));
    }
    return data;
  },
});

/** Where a resume's PDF opens in the browser (to preview) and downloads. */
export function pdfUrls(sha256: string) {
  const preview = `/api/resumes/pdf/${encodeURIComponent(sha256)}`;
  return { preview, download: `${preview}?download=true` };
}
