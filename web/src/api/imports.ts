/**
 * Imports: uploading a CSV and listing what was uploaded (docs/production/milestone-07.md §16).
 *
 * Both calls act for the signed-in user's organisation, which the API takes from the session.
 * Nothing here names an organisation.
 */
import { apiRequest } from "./client";
import type { ImportAccepted, ImportSummary } from "./types";

/**
 * The API's upload limit (settings.MAX_UPLOAD_BYTES). Checked in the browser only so a person is
 * told straight away instead of after uploading 50 MB; the API enforces it regardless.
 */
export const MAX_UPLOAD_BYTES = 10 * 1024 * 1024;

export function listImports(): Promise<ImportSummary[]> {
  return apiRequest<ImportSummary[]>("/api/v1/imports");
}

export function uploadImport(file: File): Promise<ImportAccepted> {
  const form = new FormData();
  form.append("file", file);

  return apiRequest<ImportAccepted>("/api/v1/imports", { method: "POST", form });
}
