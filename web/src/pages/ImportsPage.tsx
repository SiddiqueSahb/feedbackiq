import { useDocumentTitle } from "../app/useDocumentTitle";
import { Card } from "../components/Card";
import { PageHeader } from "../components/PageHeader";
import { ImportHistory } from "../imports/ImportHistory";
import { ImportUploader } from "../imports/ImportUploader";
import styles from "./ImportsPage.module.css";

/** Upload customer feedback and follow each import's analysis. */
export function ImportsPage() {
  useDocumentTitle("Imports");

  return (
    <>
      <PageHeader
        title="Imports"
        description="Upload customer feedback as CSV. Each import is analysed in the background."
      />

      <div className={styles.top}>
        <Card title="Upload feedback">
          <ImportUploader />
        </Card>

        <Card title="File format">
          {/* The column names the API accepts (ingestion/csv_reader.py). */}
          <dl className={styles.format}>
            <div>
              <dt>Required</dt>
              <dd>
                A <code>text</code> column with the feedback itself. <code>feedback</code>,{" "}
                <code>comment</code>, <code>review</code> or <code>body</code> also work.
              </dd>
            </div>
            <div>
              <dt>Optional</dt>
              <dd>
                <code>external_id</code> — your own reference, used to skip rows you have already
                imported
              </dd>
              <dd>
                <code>created_at</code> — when the customer wrote it
              </dd>
              <dd>
                <code>rating</code> — a score from 1 to 5
              </dd>
              <dd>
                <code>platform</code> — where it came from, such as “app store”
              </dd>
            </div>
            <div>
              <dt>Limits</dt>
              <dd>Up to 10 MB and 50,000 rows per file.</dd>
            </div>
          </dl>
        </Card>
      </div>

      <Card title="Recent imports" className={styles.history}>
        <ImportHistory />
      </Card>
    </>
  );
}
