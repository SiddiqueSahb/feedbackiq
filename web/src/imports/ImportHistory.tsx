import type { ImportSummary } from "../api/types";
import { Alert } from "../components/Alert";
import { Badge } from "../components/Badge";
import { Button } from "../components/Button";
import { EmptyState } from "../components/EmptyState";
import { Icon } from "../components/Icon";
import { Spinner } from "../components/Spinner";
import { ANALYSIS_STATUS, formatCount, formatDateTime } from "./format";
import styles from "./ImportHistory.module.css";
import { useImports } from "./useImports";

/** The organisation's imports, newest first, with their counts and analysis progress. */
export function ImportHistory() {
  const imports = useImports();

  if (imports.isPending) {
    return (
      <p className={styles.loading} role="status">
        <Spinner size="small" /> Loading imports…
      </p>
    );
  }

  if (imports.isError) {
    return (
      <Alert tone="error" title="Imports could not be loaded">
        <p>{imports.error.message}</p>
        <Button variant="secondary" onClick={() => void imports.refetch()}>
          Try again
        </Button>
      </Alert>
    );
  }

  if (imports.data.length === 0) {
    return (
      <EmptyState icon="imports" title="No imports yet">
        Files you upload appear here with how many rows went in and how their analysis is going.
      </EmptyState>
    );
  }

  return (
    <div className={styles.scroll}>
      <table className={styles.table}>
        <caption className="visually-hidden">Imports, newest first</caption>
        <thead>
          <tr>
            <th scope="col">File</th>
            <th scope="col">Uploaded</th>
            <th scope="col" className={styles.numeric}>
              Rows
            </th>
            <th scope="col" className={styles.numeric}>
              Imported
            </th>
            <th scope="col" className={styles.numeric}>
              Skipped
            </th>
            <th scope="col">Analysis</th>
          </tr>
        </thead>
        <tbody>
          {imports.data.map((item) => (
            <tr key={item.import_id}>
              <td>
                <span className={styles.file}>
                  <Icon name="file" />
                  <span className={styles.fileName}>{item.filename ?? "Untitled upload"}</span>
                </span>
              </td>
              <td className={styles.muted}>{formatDateTime(item.created_at)}</td>
              <td className={styles.numeric}>{formatCount(item.rows_received)}</td>
              <td className={styles.numeric}>{formatCount(item.rows_imported)}</td>
              <td className={styles.numeric}>{formatCount(item.rows_rejected)}</td>
              <td>
                <AnalysisStatus item={item} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function AnalysisStatus({ item }: { item: ImportSummary }) {
  // No analysis is queued for an import that stored nothing.
  if (item.rows_imported === 0) {
    return <Badge tone="danger">Nothing imported</Badge>;
  }

  if (!item.analysis_status) {
    return <span className={styles.muted}>—</span>;
  }

  const { label, tone } = ANALYSIS_STATUS[item.analysis_status];
  return <Badge tone={tone}>{label}</Badge>;
}
