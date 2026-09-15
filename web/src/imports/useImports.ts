import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { listImports, uploadImport } from "../api/imports";
import type { ImportSummary } from "../api/types";

export const IMPORTS_KEY = ["imports"] as const;

/** Everything that shows analysed data, refreshed after an upload (the dashboard, Part 6). */
export const ANALYTICS_KEY = ["analytics"] as const;

/** How often the import list is re-read while an analysis is still in progress. */
export const ANALYSIS_POLL_MS = 4000;

/** True while any import's analysis is still waiting or running. */
export function isAnalysing(imports: ImportSummary[] | undefined): boolean {
  return (imports ?? []).some(
    (item) => item.analysis_status === "queued" || item.analysis_status === "running",
  );
}

export function useImports() {
  return useQuery({
    queryKey: IMPORTS_KEY,
    queryFn: listImports,
    // Poll only while something is being analysed, and stop as soon as nothing is: the list
    // updates itself after an upload without refreshing forever on an idle page.
    refetchInterval: (query) => (isAnalysing(query.state.data) ? ANALYSIS_POLL_MS : false),
  });
}

export function useUploadImport() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: uploadImport,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: IMPORTS_KEY });
      void queryClient.invalidateQueries({ queryKey: ANALYTICS_KEY });
    },
  });
}
