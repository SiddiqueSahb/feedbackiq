import { useDocumentTitle } from "../app/useDocumentTitle";
import { useSignedInUser } from "../auth/session";
import { Alert } from "../components/Alert";
import { Button } from "../components/Button";
import { ButtonLink } from "../components/ButtonLink";
import { Card } from "../components/Card";
import { EmptyState } from "../components/EmptyState";
import { PageHeader } from "../components/PageHeader";
import { Spinner } from "../components/Spinner";
import { formatCompact } from "../dashboard/format";
import { SummaryTiles } from "../dashboard/SummaryTiles";
import { useSummary } from "../dashboard/useAnalytics";
import { formatDateTime } from "../imports/format";
import styles from "./DashboardPage.module.css";

/**
 * The organisation's dashboard. Every figure comes from the API, computed in PostgreSQL for the
 * signed-in user's organisation; this page only chooses how to show it.
 */
export function DashboardPage() {
  useDocumentTitle("Dashboard");
  const user = useSignedInUser();
  const summary = useSummary();

  const latest = summary.data?.latest_feedback_at;
  const description = latest
    ? `Customer feedback for ${user.organisation.name} · latest feedback ${formatDateTime(latest)}`
    : `Customer feedback for ${user.organisation.name}`;

  return (
    <>
      <PageHeader title="Dashboard" description={description} />
      <DashboardBody summary={summary} />
    </>
  );
}

function DashboardBody({ summary }: { summary: ReturnType<typeof useSummary> }) {
  if (summary.isPending) {
    return (
      <p className={styles.loading} role="status">
        <Spinner size="small" /> Loading your figures…
      </p>
    );
  }

  if (summary.isError) {
    return (
      <Alert tone="error" title="The dashboard could not be loaded">
        <p>{summary.error.message}</p>
        <Button variant="secondary" onClick={() => void summary.refetch()}>
          Try again
        </Button>
      </Alert>
    );
  }

  const figures = summary.data;

  if (figures.total_feedback === 0) {
    return (
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
    );
  }

  return (
    <div className={styles.sections}>
      {figures.not_analysed > 0 && (
        <Alert tone="info" title="Analysis in progress">
          {formatCompact(figures.not_analysed)} of {formatCompact(figures.total_feedback)} feedback items are
          still waiting to be analysed. These figures update on their own as analysis completes.
        </Alert>
      )}

      <section aria-labelledby="headline-figures">
        <h2 id="headline-figures" className="visually-hidden">
          Headline figures
        </h2>
        <SummaryTiles summary={figures} />
      </section>
    </div>
  );
}
