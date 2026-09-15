import { useState } from "react";

import type { TrendInterval } from "../api/analytics";
import { useDocumentTitle } from "../app/useDocumentTitle";
import { useSignedInUser } from "../auth/session";
import { Alert } from "../components/Alert";
import { Button } from "../components/Button";
import { ButtonLink } from "../components/ButtonLink";
import { Card } from "../components/Card";
import { EmptyState } from "../components/EmptyState";
import { PageHeader } from "../components/PageHeader";
import { Spinner } from "../components/Spinner";
import { CategoryBreakdown } from "../dashboard/CategoryBreakdown";
import { formatCompact } from "../dashboard/format";
import { SentimentTrend } from "../dashboard/SentimentTrend";
import { SummaryTiles } from "../dashboard/SummaryTiles";
import {
  isWaitingForAnalysis,
  useCategoryBreakdown,
  useSummary,
  useTrend,
} from "../dashboard/useAnalytics";
import { formatDate } from "../imports/format";
import styles from "./DashboardPage.module.css";

const INTERVALS: { value: TrendInterval; label: string }[] = [
  { value: "day", label: "Day" },
  { value: "week", label: "Week" },
  { value: "month", label: "Month" },
];

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
    ? `Customer feedback for ${user.organisation.name} · latest feedback ${formatDate(latest)}`
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
    return <Loading label="Loading your figures…" />;
  }

  if (summary.isError) {
    return <LoadError title="The dashboard could not be loaded" error={summary.error} onRetry={() => void summary.refetch()} />;
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

  const poll = isWaitingForAnalysis(figures);

  return (
    <div className={styles.sections}>
      {poll && (
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

      <TrendCard poll={poll} />
      <CategoriesCard poll={poll} />
    </div>
  );
}

function TrendCard({ poll }: { poll: boolean }) {
  const [interval, setInterval] = useState<TrendInterval>("week");
  const trend = useTrend(interval, poll);

  return (
    <Card
      title="Sentiment over time"
      actions={
        <div className={styles.segmented} role="group" aria-label="Group by">
          {INTERVALS.map((option) => (
            <button
              key={option.value}
              type="button"
              className={styles.segment}
              aria-pressed={interval === option.value}
              onClick={() => setInterval(option.value)}
            >
              {option.label}
            </button>
          ))}
        </div>
      }
    >
      {trend.isPending ? (
        <Loading label="Loading the trend…" />
      ) : trend.isError ? (
        <LoadError title="The trend could not be loaded" error={trend.error} onRetry={() => void trend.refetch()} />
      ) : (
        <SentimentTrend points={trend.data} interval={interval} />
      )}
    </Card>
  );
}

function CategoriesCard({ poll }: { poll: boolean }) {
  const breakdown = useCategoryBreakdown(poll);

  return (
    <Card title="Complaint categories">
      {breakdown.isPending ? (
        <Loading label="Loading categories…" />
      ) : breakdown.isError ? (
        <LoadError
          title="Categories could not be loaded"
          error={breakdown.error}
          onRetry={() => void breakdown.refetch()}
        />
      ) : (
        <CategoryBreakdown rows={breakdown.data} />
      )}
    </Card>
  );
}

function Loading({ label }: { label: string }) {
  return (
    <p className={styles.loading} role="status">
      <Spinner size="small" /> {label}
    </p>
  );
}

function LoadError({ title, error, onRetry }: { title: string; error: Error; onRetry: () => void }) {
  return (
    <Alert tone="error" title={title}>
      <p>{error.message}</p>
      <Button variant="secondary" onClick={onRetry}>
        Try again
      </Button>
    </Alert>
  );
}
