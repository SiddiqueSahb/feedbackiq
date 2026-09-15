import type { ImportAccepted } from "../api/types";
import { Alert } from "../components/Alert";
import { formatCount, formatDateTime, rows } from "./format";
import styles from "./UploadResult.module.css";

/** Row problems shown inline. The API keeps up to 100; ten is enough to spot a pattern. */
const ROW_ERRORS_SHOWN = 10;

/** What one upload did, in the words a person needs: what went in, what didn't, and why. */
export function UploadResult({ result }: { result: ImportAccepted }) {
  if (result.duplicate_upload) {
    return (
      <Alert tone="info" title="This file was already imported">
        {result.filename ?? "This file"} was imported on {formatDateTime(result.created_at)}. Nothing
        new was added.
      </Alert>
    );
  }

  const skipped = result.rows_rejected;
  const errors = result.row_errors ?? [];

  if (result.rows_imported === 0) {
    return (
      <Alert tone="error" title="No rows could be imported">
        <p>Every row in the file was skipped. Fix the rows below and upload the file again.</p>
        <RowErrors errors={errors} />
      </Alert>
    );
  }

  return (
    <Alert tone="success" title={`Imported ${formatCount(result.rows_imported)} of ${rows(result.rows_received)}`}>
      <p>
        Analysis has been queued. Sentiment and complaint themes appear on the dashboard as it
        completes.
        {skipped > 0 && ` ${rows(skipped)} ${skipped === 1 ? "was" : "were"} skipped.`}
      </p>
      {result.duplicates_in_database > 0 && (
        <p>{rows(result.duplicates_in_database)} had already been imported before.</p>
      )}
      <RowErrors errors={errors} />
    </Alert>
  );
}

function RowErrors({ errors }: { errors: ImportAccepted["row_errors"] }) {
  const list = errors ?? [];
  if (list.length === 0) return null;

  const shown = list.slice(0, ROW_ERRORS_SHOWN);

  return (
    <div className={styles.errors}>
      <table className={styles.table}>
        <caption className="visually-hidden">Rows that were skipped</caption>
        <thead>
          <tr>
            <th scope="col">Line</th>
            <th scope="col">Column</th>
            <th scope="col">Problem</th>
          </tr>
        </thead>
        <tbody>
          {shown.map((error) => (
            <tr key={`${error.row}-${error.field}`}>
              <td className={styles.line}>{error.row}</td>
              <td>
                <code>{error.field}</code>
              </td>
              <td>{error.message}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {list.length > shown.length && (
        <p className={styles.more}>…and {formatCount(list.length - shown.length)} more.</p>
      )}
    </div>
  );
}
