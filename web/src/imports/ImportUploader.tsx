import { useRef, useState, type DragEvent, type FormEvent } from "react";

import type { ImportAccepted } from "../api/types";
import { Alert } from "../components/Alert";
import { Button } from "../components/Button";
import { Icon } from "../components/Icon";
import { fileProblem, formatBytes } from "./format";
import styles from "./ImportUploader.module.css";
import { UploadResult } from "./UploadResult";
import { useUploadImport } from "./useImports";

/**
 * Choose or drop one CSV and upload it.
 *
 * The file goes to the API as it is; the browser only checks it is a CSV within the size limit so a
 * person hears about an obvious mistake before waiting for an upload. Everything else - columns,
 * rows, duplicates - is the API's to judge, and its answer is shown as it gave it.
 */
export function ImportUploader() {
  const upload = useUploadImport();
  const input = useRef<HTMLInputElement>(null);

  const [file, setFile] = useState<File | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const [result, setResult] = useState<ImportAccepted | null>(null);

  function clearInput() {
    if (input.current) input.current.value = "";
  }

  function choose(candidate: File | undefined) {
    upload.reset();
    setResult(null);

    if (!candidate) return;

    const reason = fileProblem(candidate);
    setProblem(reason);
    setFile(reason ? null : candidate);

    if (reason) clearInput();
  }

  function handleDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setDragging(false);
    choose(event.dataTransfer.files[0]);
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!file) return;

    upload.mutate(file, {
      onSuccess: (accepted) => {
        setResult(accepted);
        setFile(null);
        clearInput();
      },
    });
  }

  return (
    <form className={styles.uploader} onSubmit={handleSubmit}>
      <div
        className={[styles.dropZone, dragging && styles.dragging].filter(Boolean).join(" ")}
        onDragOver={(event) => {
          event.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={handleDrop}
      >
        <span className={styles.icon}>
          <Icon name="upload" size={20} />
        </span>
        <p className={styles.prompt}>
          <strong>Drop a CSV file here</strong> or
        </p>
        <label className={styles.choose}>
          Choose file
          <input
            ref={input}
            type="file"
            accept=".csv,text/csv"
            className="visually-hidden"
            onChange={(event) => choose(event.target.files?.[0])}
          />
        </label>
        <p className={styles.limit}>One CSV file, up to 10 MB</p>
      </div>

      {file && (
        <div className={styles.selected}>
          <Icon name="file" />
          <span className={styles.fileName}>{file.name}</span>
          <span className={styles.fileSize}>{formatBytes(file.size)}</span>
          <Button
            variant="ghost"
            onClick={() => {
              setFile(null);
              clearInput();
            }}
            disabled={upload.isPending}
          >
            Remove
          </Button>
        </div>
      )}

      {problem && <Alert tone="error">{problem}</Alert>}

      {upload.isError && (
        <Alert tone="error" title="The file was not imported">
          {upload.error.message}
        </Alert>
      )}

      {result && <UploadResult result={result} />}

      <div className={styles.actions}>
        <Button type="submit" disabled={!file} loading={upload.isPending}>
          {upload.isPending ? "Uploading…" : "Upload"}
        </Button>
      </div>
    </form>
  );
}
