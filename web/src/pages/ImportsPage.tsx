import { useDocumentTitle } from "../app/useDocumentTitle";
import { Card } from "../components/Card";
import { EmptyState } from "../components/EmptyState";
import { PageHeader } from "../components/PageHeader";

/** Imports: CSV upload and import history arrive in Part 5 of Milestone 8. */
export function ImportsPage() {
  useDocumentTitle("Imports");

  return (
    <>
      <PageHeader title="Imports" description="Upload customer feedback and follow its analysis." />

      <Card>
        <EmptyState icon="upload" title="No imports yet">
          Uploading a CSV will be available here.
        </EmptyState>
      </Card>
    </>
  );
}
