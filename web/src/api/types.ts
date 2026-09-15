/**
 * Friendly names for the API's request and response shapes.
 *
 * Every type here is an alias into `schema.d.ts`, which is generated from the backend's OpenAPI
 * document (`npm run api:types`). Nothing is written by hand, so a change to a backend shape shows
 * up as a TypeScript error wherever the frontend relies on it.
 */
import type { components } from "./schema";

type Schemas = components["schemas"];

export type CurrentUser = Schemas["CurrentUser"];
export type OrganisationContext = Schemas["OrganisationContext"];
export type RegisterRequest = Schemas["RegisterRequest"];
export type LoginRequest = Schemas["LoginRequest"];

export type ImportAccepted = Schemas["ImportAccepted"];
export type ImportSummary = Schemas["ImportSummary"];
export type JobSummary = Schemas["JobSummary"];

export type AnalyticsSummary = Schemas["AnalyticsSummary"];
export type TrendPoint = Schemas["TrendPoint"];
export type CategoryStat = Schemas["CategoryStat"];
export type FeedbackPage = Schemas["FeedbackPage"];
export type FeedbackItem = Schemas["FeedbackItem"];
