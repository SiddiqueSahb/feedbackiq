import { useEffect } from "react";

/** Sets the browser tab title, e.g. "Imports · FeedbackIQ". */
export function useDocumentTitle(title: string): void {
  useEffect(() => {
    document.title = title ? `${title} · FeedbackIQ` : "FeedbackIQ";
  }, [title]);
}
