import React, { useState, useRef } from 'react';
import { UploadIcon } from './Icons';

export function Stage1Ingest({ onIngestComplete }) {
  const [file, setFile] = useState(null);
  const [uploadId, setUploadId] = useState(null);
  const [isUploading, setIsUploading] = useState(false);
  const [isIngesting, setIsIngesting] = useState(false);
  const [progressMsg, setProgressMsg] = useState('');
  const [errorMsg, setErrorMsg] = useState('');
  const [isDragOver, setIsDragOver] = useState(false);

  const fileInputRef = useRef(null);

  const handleFileSelection = async (selectedFile) => {
    if (!selectedFile) return;
    setErrorMsg('');
    setFile(selectedFile);
    setIsUploading(true);

    try {
      const formData = new FormData();
      formData.append('file', selectedFile);

      const res = await fetch('/uploads', {
        method: 'POST',
        body: formData,
      });

      const data = await res.json();
      if (!res.ok) {
        throw new Error(data.detail || 'Upload failed');
      }

      setUploadId(data.upload_id);
    } catch (err) {
      console.error('File upload error:', err);
      setErrorMsg(err.message || 'Failed to upload file');
      setFile(null);
      setUploadId(null);
    } finally {
      setIsUploading(false);
    }
  };

  const handleDrop = (e) => {
    e.preventDefault();
    setIsDragOver(false);
    if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
      handleFileSelection(e.dataTransfer.files[0]);
    }
  };

  const handleDragOver = (e) => {
    e.preventDefault();
    setIsDragOver(true);
  };

  const handleDragLeave = () => {
    setIsDragOver(false);
  };

  const handleReadSheet = async () => {
    if (!uploadId || isIngesting) return;
    setIsIngesting(true);
    setErrorMsg('');
    setProgressMsg('Initiating ingestion job...');

    try {
      const res = await fetch('/ingest', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ upload_id: uploadId }),
      });

      const data = await res.json();
      if (!res.ok) {
        throw new Error(data.detail || 'Failed to enqueue ingest job');
      }

      const jobId = data.job_id;

      // Listen via Server-Sent Events (SSE) with fallback to polling
      let eventSource;
      let completed = false;

      const finishWithResult = (result) => {
        if (completed) return;
        completed = true;
        if (eventSource) eventSource.close();
        onIngestComplete(result);
      };

      try {
        eventSource = new EventSource(`/jobs/${jobId}/stream`);

        eventSource.onmessage = (event) => {
          try {
            const parsed = JSON.parse(event.data);
            if (parsed.message) {
              setProgressMsg(parsed.message);
            }
            if (parsed.stage === 'terminal') {
              if (parsed.status === 'done') {
                if (parsed.result) {
                  finishWithResult(parsed.result);
                } else {
                  fetch(`/jobs/${jobId}`)
                    .then((r) => r.json())
                    .then((j) => {
                      if (j.result) finishWithResult(j.result);
                    })
                    .catch((err) => console.error('Fetch job result error:', err));
                }
              } else if (parsed.status === 'failed') {
                eventSource.close();
                setIsIngesting(false);
                setErrorMsg(parsed.error || 'Ingest job failed');
              }
            }
          } catch (e) {
            // Ignore parse errors on keep-alives
          }
        };

        eventSource.onerror = () => {
          // If SSE encounters an issue, fallback to polling
          eventSource.close();
          pollJobStatus(jobId, finishWithResult);
        };
      } catch (err) {
        pollJobStatus(jobId, finishWithResult);
      }
    } catch (err) {
      console.error('Ingest error:', err);
      setIsIngesting(false);
      setErrorMsg(err.message || 'Failed to analyze price sheet');
    }
  };

  const pollJobStatus = (jobId, onComplete) => {
    const interval = setInterval(async () => {
      try {
        const res = await fetch(`/jobs/${jobId}`);
        if (!res.ok) return;
        const job = await res.json();

        if (job.message) {
          setProgressMsg(job.message);
        }

        if (job.status === 'done' && job.result) {
          clearInterval(interval);
          onComplete(job.result);
        } else if (job.status === 'failed') {
          clearInterval(interval);
          setIsIngesting(false);
          setErrorMsg(job.error || 'Ingest job failed');
        }
      } catch (e) {
        console.warn('Poll error:', e);
      }
    }, 1000);
  };

  return (
    <div className="stage-1-container">
      {/* Dashed Amber Border Box on Cream */}
      <div
        className={`drop-zone ${isDragOver ? 'drop-zone--active' : ''}`}
        onDrop={handleDrop}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onClick={() => {
          if (!file && !isIngesting) {
            fileInputRef.current?.click();
          }
        }}
      >
        <input
          ref={fileInputRef}
          type="file"
          accept=".png,.jpg,.jpeg,.webp,.pdf,.xlsx,.xls,.csv,.txt"
          style={{ display: 'none' }}
          onChange={(e) => {
            if (e.target.files && e.target.files.length > 0) {
              handleFileSelection(e.target.files[0]);
            }
          }}
        />

        <div className="drop-zone__icon">
          <UploadIcon width={36} height={36} color="var(--amber-dark)" />
        </div>

        <div className="drop-zone__title">
          {file ? file.name : 'Drop your price sheet here'}
        </div>

        <div className="drop-zone__subline">
          {file ? (
            `${(file.size / 1024).toFixed(1)} KB ${isUploading ? '— uploading...' : '— ready to read'}`
          ) : (
            'Screenshot, PDF, Excel, CSV, or pasted text'
          )}
        </div>

        <div className="drop-zone__action">
          {file ? (
            <button
              type="button"
              className="btn-secondary"
              onClick={(e) => {
                e.stopPropagation();
                setFile(null);
                setUploadId(null);
                setErrorMsg('');
                setProgressMsg('');
              }}
              disabled={isIngesting}
            >
              Choose different file
            </button>
          ) : (
            <button
              type="button"
              className="btn-secondary"
              onClick={(e) => {
                e.stopPropagation();
                fileInputRef.current?.click();
              }}
            >
              Choose file
            </button>
          )}
        </div>

        {progressMsg && (
          <div className="drop-zone__progress">
            <span className="spinner-indicator"></span>
            <span>{progressMsg}</span>
          </div>
        )}

        {errorMsg && (
          <div className="drop-zone__error">
            {errorMsg}
          </div>
        )}
      </div>

      {/* Bottom Right: Primary Teal Action Button */}
      <div className="stage-footer">
        <div className="stage-footer__left"></div>
        <div className="stage-footer__right">
          <button
            type="button"
            className="btn-primary"
            disabled={!uploadId || isIngesting || isUploading}
            onClick={handleReadSheet}
          >
            {isIngesting ? 'Reading sheet...' : 'Read the sheet'}
          </button>
        </div>
      </div>
    </div>
  );
}
