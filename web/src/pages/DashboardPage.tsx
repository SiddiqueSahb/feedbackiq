import { useDocumentTitle } from "../app/useDocumentTitle";
import { useSignedInUser } from "../auth/session";
import { ButtonLink } from "../components/ButtonLink";
import { Card } from "../components/Card";
import { EmptyState } from "../components/EmptyState";
import { PageHeader } from "../components/PageHeader";

/**
 * The organisation's dashboard. For now its empty state; the figures arrive in Part 6 of
 * Milestone 8.
 */
export function DashboardPage() {
  useDocumentTitle("Dashboard");
  const user = useSignedInUser();

  return (
    <>
      <PageHeader title="Dashboard" description={`Customer feedback for ${user.organisation.name}`} />

      <Card>
        <EmptyState
          icon="dashboard"
          title="Your dashboard fills in as feedback is analysed"
          action={<ButtonLink to="/imports">Upload feedback</ButtonLink>}
        >
          Upload a CSV of customer feedback. FeedbackIQ analyses sentiment and complaint themes in
          the background, and the results appear here.
        </EmptyState>
      </Card>
    </>
  );
}
