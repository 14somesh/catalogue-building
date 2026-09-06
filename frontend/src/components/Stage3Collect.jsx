import React, { useState, useEffect, useRef } from 'react';
import { CheckIcon, CrossIcon, RefreshIcon, WarningTriangleIcon } from './Icons';
import { ErrorDisplay } from './ErrorDisplay';

// Helper to format source nicely
function formatSourceLabel(url) {
  if (!url) return 'Web';
  const lower = url.toLowerCase();
  if (lower.includes('brochure') || lower.startsWith('pdf:')) return 'Brochure';
  if (lower.includes('amazon.')) return 'Amazon';
  if (lower.includes('flipkart.')) return 'Flipkart';
  if (lower.includes('reliancedigital') || lower.includes('reliance')) return 'Reliance';
  if (lower.includes('croma.')) return 'Croma';
  try {
    const host = new URL(url).hostname.replace(/^www\./, '');
    return host;
  } catch {
    return 'Brand site';
  }
}

// Helper to format seconds as "Xm Ys" or "Xs"
function formatRuntime(seconds) {
  const s = Math.max(0, Math.round(seconds || 0));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const rem = s % 60;
  return `${m}m ${rem}s`;
}

export function Stage3Collect({ collectionTarget, onBack, onContinue }) {
  const brand = collectionTarget?.brand || '';
  const initialCount = collectionTarget?.rowCount || 0;

  // Job & collection state
  const [jobId, setJobId] = useState(null);
  const [stageState, setStageState] = useState('running'); // 'running' | 'finished' | 'cancelled'
  const [progress, setProgress] = useState({
    current: 0,
    total: initialCount,
    message: 'Starting collection run...',
  });
  const [runtimeDisplay, setRuntimeDisplay] = useState('0s');
  const [startTime, setStartTime] = useState(Date.now());

  // Running state rows: { product_id, model_name, status: 'ready'|'failed'|'running'|'waiting', source: '' }
  const [runningRows, setRunningRows] = useState([]);

  // Finished state data
  const [foundRows, setFoundRows] = useState([]);
  const [failureRows, setFailureRows] = useState([]);

  // Control actions
  const [isCancelling, setIsCancelling] = useState(false);
  const [isRetrying, setIsRetrying] = useState(false);
  const [errorMsg, setErrorMsg] = useState('');
  const [techDetails, setTechDetails] = useState(null);

  // Manual URL modal
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [selectedProductId, setSelectedProductId] = useState('');
  const [manualUrl, setManualUrl] = useState('');
  const [manualUrlSubmitting, setManualUrlSubmitting] = useState(false);
  const [manualUrlError, setManualUrlError] = useState('');

  const eventSourceRef = useRef(null);
  const pollTimerRef = useRef(null);

  // Timer for runtime display
  useEffect(() => {
    let timer;
    if (stageState === 'running') {
      timer = setInterval(() => {
        const elapsed = (Date.now() - startTime) / 1000;
        setRuntimeDisplay(formatRuntime(elapsed));
      }, 1000);
    }
    return () => clearInterval(timer);
  }, [stageState, startTime]);

  // Dev / test hook to inspect finished state
  useEffect(() => {
    window.__SET_STAGE_3_FINISHED = (data) => {
      setStageState('finished');
      setRuntimeDisplay(data.runtimeDisplay || '3m 12s');
      setFoundRows(data.foundRows || []);
      setFailureRows(data.failureRows || []);
    };
    return () => {
      delete window.__SET_STAGE_3_FINISHED;
    };
  }, []);

  // Initial fetch: start collection
  useEffect(() => {
    let unmounted = false;

    async function initCollection() {
      if (!brand) return;
      try {
        setErrorMsg('');
        setStageState('running');
        setStartTime(Date.now());

        // First pre-populate running rows from existing rows if available
        try {
          const rRes = await fetch(`/brands/${encodeURIComponent(brand)}/rows`);
          if (rRes.ok && !unmounted) {
            const rowsData = await rRes.json();
            setRunningRows(
              rowsData.map((r, idx) => ({
                product_id: r.product_id || `row-${idx}`,
                name: r.display_name || r.model_name || r.title,
                status: 'waiting',
                source: '',
              }))
            );
            setProgress((prev) => ({ ...prev, total: rowsData.length || initialCount }));
          }
        } catch {
          // fallback placeholder rows
        }

        // 1. Check if there is already an active job for this brand (e.g. page refresh or back navigation)
        try {
          const activeRes = await fetch(`/brands/${encodeURIComponent(brand)}/active-job`);
          if (activeRes.ok) {
            const activeData = await activeRes.json();
            if (activeData.active && activeData.job_id) {
              if (unmounted) return;
              setJobId(activeData.job_id);
              if (activeData.created_at) {
                setStartTime(new Date(activeData.created_at).getTime());
              }
              connectStream(activeData.job_id);
              return;
            }
          }
        } catch (e) {
          console.warn('Active job check error:', e);
        }

        // 2. Start collect job: POST /brands/{brand}/collect
        const colRes = await fetch(`/brands/${encodeURIComponent(brand)}/collect`, {
          method: 'POST',
        });
        const colData = await colRes.json();

        if (colRes.status === 409) {
          const detail = colData.detail;
          const activeId = colData.active_job_id || (typeof detail === 'object' ? detail.active_job_id : null);
          if (activeId) {
            if (unmounted) return;
            setJobId(activeId);
            if (typeof detail === 'object' && detail.started_at) {
              setStartTime(new Date(detail.started_at).getTime());
            }
            connectStream(activeId);
            return;
          }
          const plainMsg = typeof detail === 'object' && detail.message
            ? detail.message
            : (typeof detail === 'string' ? detail : `Collection for '${brand}' is already running.`);
          const tech = typeof detail === 'object' && detail.active_job_id ? `Job ID: ${detail.active_job_id}` : null;
          setErrorMsg(plainMsg);
          setTechDetails(tech);
          return;
        }

        if (!colRes.ok) {
          const detail = colData.detail;
          const plainMsg = typeof detail === 'string' ? detail : 'Failed to start collection run.';
          throw new Error(plainMsg);
        }

        if (unmounted) return;
        setJobId(colData.job_id);
        connectStream(colData.job_id);
      } catch (err) {
        if (!unmounted) {
          console.error('Collect init error:', err);
          setErrorMsg(err.message || 'Error starting collection.');
        }
      }
    }

    initCollection();

    return () => {
      unmounted = true;
      if (eventSourceRef.current) eventSourceRef.current.close();
      if (pollTimerRef.current) clearInterval(pollTimerRef.current);
    };
  }, [brand]);

  // Connect SSE progress stream
  const connectStream = (activeJobId) => {
    if (eventSourceRef.current) {
      eventSourceRef.current.close();
    }

    const sse = new EventSource(`/jobs/${activeJobId}/stream`);
    eventSourceRef.current = sse;

    sse.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        handleStreamEvent(data, activeJobId);
      } catch (err) {
        console.warn('SSE parse error:', err);
      }
    };

    sse.onerror = () => {
      // Fallback polling if SSE disconnects
      sse.close();
      if (!pollTimerRef.current) {
        pollTimerRef.current = setInterval(() => {
          checkJobStatus(activeJobId);
        }, 1500);
      }
    };
  };

  // Poll fallback
  const checkJobStatus = async (activeJobId) => {
    try {
      const res = await fetch(`/jobs/${activeJobId}`);
      if (!res.ok) return;
      const job = await res.json();

      if (job.message) {
        setProgress((prev) => ({
          ...prev,
          current: job.progress_current || prev.current,
          total: job.progress_total || prev.total,
          message: job.message,
        }));
      }

      if (job.status === 'done' || job.status === 'cancelled' || job.status === 'failed') {
        if (pollTimerRef.current) clearInterval(pollTimerRef.current);
        handleJobFinished(job.result, job.status);
      }
    } catch (err) {
      console.warn('Poll error:', err);
    }
  };

  // Process live progress stream event
  const handleStreamEvent = (evt, activeJobId) => {
    if (evt.message) {
      setProgress((prev) => ({
        ...prev,
        current: evt.current !== undefined && evt.current !== null ? evt.current : prev.current,
        total: evt.total !== undefined && evt.total !== null ? evt.total : prev.total,
        message: evt.message,
      }));
    }

    if (evt.product_id) {
      const pid = evt.product_id;
      setRunningRows((prev) => {
        const next = [...prev];
        const idx = next.findIndex((r) => r.product_id === pid);
        if (idx !== -1) {
          if (evt.stage === 'row_start') {
            next[idx] = { ...next[idx], status: 'running' };
          } else if (evt.stage === 'row_done') {
            const isSuccess = evt.status === 'Ready_For_Review' || evt.status === 'Approved';
            next[idx] = {
              ...next[idx],
              status: isSuccess ? 'ready' : 'failed',
              source: evt.source || next[idx].source || 'Web',
            };
          }
        }
        return next;
      });
    }

    if (evt.stage === 'terminal') {
      if (eventSourceRef.current) eventSourceRef.current.close();
      if (pollTimerRef.current) clearInterval(pollTimerRef.current);

      if (evt.status === 'done') {
        handleJobFinished(evt.result, 'done');
      } else if (evt.status === 'cancelled') {
        handleJobFinished(evt.result, 'cancelled');
      } else if (evt.status === 'failed') {
        setErrorMsg(evt.error || evt.message || 'Collection failed.');
        setStageState('finished');
      }
    }
  };

  // Job completed / cancelled: transition to finished view
  const handleJobFinished = async (result, finalStatus) => {
    setStageState('finished');
    setIsCancelling(false);
    setIsRetrying(false);

    if (result?.runtime) {
      setRuntimeDisplay(formatRuntime(result.runtime));
    }

    try {
      // Pull review data to get clean final found and failed lists
      const revRes = await fetch(`/brands/${encodeURIComponent(brand)}/review`);
      if (revRes.ok) {
        const reviewRows = await revRes.json();

        const found = [];
        const failed = [];

        reviewRows.forEach((r) => {
          const isSuccess = r.status === 'Ready_For_Review' || r.status === 'Approved';
          if (isSuccess) {
            const isGoodImage = r.image_status === 'ready' || r.image_status === 'downloaded' || !r.flags?.toLowerCase().includes('low');
            found.push({
              product_id: r.product_id,
              name: r.title || r.model_name,
              source: formatSourceLabel(r.source_url),
              imageQuality: isGoodImage ? 'Good' : 'Low quality',
            });
          } else {
            failed.push({
              product_id: r.product_id,
              name: r.title || r.model_name,
              reason: r.failure_reason || 'Not found across available sources',
            });
          }
        });

        setFoundRows(found);
        setFailureRows(failed);
      } else if (result?.rows) {
        // Fallback from result.rows
        const found = [];
        const failed = [];
        result.rows.forEach((r) => {
          const isSuccess = r.status === 'Ready_For_Review' || r.status === 'Approved';
          if (isSuccess) {
            found.push({
              product_id: r.product_id,
              name: r.display_name || r.model_name || r.title,
              source: formatSourceLabel(r.source_url),
              imageQuality: r.image_status !== 'low_res' ? 'Good' : 'Low quality',
            });
          } else {
            failed.push({
              product_id: r.product_id,
              name: r.display_name || r.model_name || r.title,
              reason: r.failure_reason || 'Not found',
            });
          }
        });
        setFoundRows(found);
        setFailureRows(failed);
      }
    } catch (err) {
      console.warn('Could not parse review rows:', err);
    }
  };

  // Handle Stop
  const handleStop = async () => {
    if (!jobId || isCancelling) return;
    setIsCancelling(true);
    try {
      await fetch(`/jobs/${jobId}/cancel`, { method: 'POST' });
      setProgress((prev) => ({ ...prev, message: 'Stopping collection gracefully...' }));
    } catch (err) {
      console.error('Cancel request error:', err);
      setIsCancelling(false);
    }
  };

  // Handle Retry
  const handleRetry = async () => {
    if (!brand || isRetrying) return;
    setIsRetrying(true);
    setErrorMsg('');
    try {
      const retryRes = await fetch(`/brands/${encodeURIComponent(brand)}/retry`, {
        method: 'POST',
      });
      const retryData = await retryRes.json();

      if (retryRes.status === 409) {
        throw new Error(retryData.detail || `Brand '${brand}' is currently locked.`);
      }

      if (!retryRes.ok) {
        throw new Error(retryData.detail || 'Failed to start retry.');
      }

      setJobId(retryData.job_id);
      setStageState('running');
      setStartTime(Date.now());
      connectStream(retryData.job_id);
    } catch (err) {
      console.error('Retry error:', err);
      setIsRetrying(false);
      setErrorMsg(err.message || 'Failed to retry.');
    }
  };

  // Handle Manual URL Submission
  const handleManualUrlSubmit = async (e) => {
    e.preventDefault();
    if (!selectedProductId || !manualUrl.trim() || manualUrlSubmitting) return;

    setManualUrlSubmitting(true);
    setManualUrlError('');

    try {
      const res = await fetch(`/products/${encodeURIComponent(selectedProductId)}/source`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url: manualUrl.trim() }),
      });

      const data = await res.json();
      if (!res.ok) {
        throw new Error(data.detail || 'Manual source re-run failed.');
      }

      if (data.success) {
        // Move item from failureRows to foundRows
        const resolvedItem = failureRows.find((f) => f.product_id === selectedProductId);
        if (resolvedItem) {
          setFailureRows((prev) => prev.filter((f) => f.product_id !== selectedProductId));
          setFoundRows((prev) => [
            ...prev,
            {
              product_id: selectedProductId,
              name: resolvedItem.name,
              source: formatSourceLabel(manualUrl.trim()),
              imageQuality: 'Good',
            },
          ]);
        }
        setIsModalOpen(false);
        setManualUrl('');
        setSelectedProductId('');
      } else {
        setManualUrlError(data.message || 'Collection failed with provided URL.');
      }
    } catch (err) {
      console.error('Manual URL error:', err);
      setManualUrlError(err.message || 'Failed to submit manual URL.');
    } finally {
      setManualUrlSubmitting(false);
    }
  };

  // Percent calculation
  const totalCount = progress.total || initialCount || 1;
  const currentCount = progress.current || 0;
  const percentComplete = Math.min(100, Math.round((currentCount / totalCount) * 100));

  return (
    <div className="stage-3-container">
      {/* =========================================================================
          STATE 1: RUNNING
          ========================================================================= */}
      {stageState === 'running' && (
        <>
          {/* Amber Panel — Progress Bar */}
          <div className="stage-3-panel stage-3-panel--running">
            <div className="stage-3-panel__header">
              <span className="stage-3-panel__brand">{brand}</span>
              <span className="stage-3-panel__counter">
                {currentCount} of {totalCount}
              </span>
            </div>

            {/* Progress bar */}
            <div className="stage-3-progress-track">
              <div
                className="stage-3-progress-fill"
                style={{ width: `${percentComplete}%` }}
              />
            </div>

            {/* Activity line */}
            <div className="stage-3-panel__activity">
              <span className="spinner-indicator" />
              <span className="stage-3-activity-text">{progress.message}</span>
            </div>
          </div>

          {/* Running rows table */}
          <div className="running-table-wrap">
            <table className="running-table">
              <thead>
                <tr>
                  <th style={{ width: '45%' }}>Product</th>
                  <th style={{ width: '25%' }}>Result</th>
                  <th style={{ width: '30%' }}>Source</th>
                </tr>
              </thead>
              <tbody>
                {runningRows.map((row) => (
                  <tr
                    key={row.product_id}
                    className={`running-row running-row--${row.status}`}
                  >
                    <td className="running-row__name">{row.name}</td>
                    <td className="running-row__result">
                      {row.status === 'ready' && (
                        <span className="badge-result badge-result--ready">
                          <CheckIcon width={13} height={13} color="var(--teal)" />
                          Ready
                        </span>
                      )}
                      {row.status === 'failed' && (
                        <span className="badge-result badge-result--failed">
                          <CrossIcon width={13} height={13} color="var(--red)" />
                          Not found
                        </span>
                      )}
                      {row.status === 'running' && (
                        <span className="badge-result badge-result--running">
                          <span className="spinner-indicator" style={{ width: 11, height: 11 }} />
                          Running
                        </span>
                      )}
                      {row.status === 'waiting' && (
                        <span className="badge-result badge-result--waiting">Waiting</span>
                      )}
                    </td>
                    <td className="running-row__source">{row.source || '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* Error banner if any */}
          {errorMsg && (
            <ErrorDisplay error={errorMsg} showIcon={true} />
          )}

          {/* Footer: Stop on left, visually disabled continue on right */}
          <div className="stage-footer">
            <div className="stage-footer__left">
              <button
                type="button"
                className="btn-secondary"
                onClick={handleStop}
                disabled={isCancelling}
              >
                {isCancelling ? 'Stopping...' : 'Stop'}
              </button>
            </div>
            <div className="stage-footer__right">
              <button
                type="button"
                className="btn-primary"
                disabled={true}
                style={{ opacity: 0.45, cursor: 'not-allowed' }}
              >
                Continue with {initialCount} rows
              </button>
            </div>
          </div>
        </>
      )}

      {/* =========================================================================
          STATE 2: FINISHED
          ========================================================================= */}
      {stageState === 'finished' && (
        <>
          {/* Amber Panel — Summary */}
          <div className="stage-3-panel stage-3-panel--finished">
            <div className="stage-3-summary__left">
              <div className="stage-3-summary__runtime">Done in {runtimeDisplay}</div>
              <div className="stage-3-summary__brand">{brand}</div>
            </div>

            <div className="stage-3-summary__right">
              <div className="stage-2-stat">
                <span className="stage-2-stat__number">{foundRows.length}</span>
                <span className="stage-2-stat__label">found</span>
              </div>
              <div className="stage-2-stat">
                <span className="stage-2-stat__number">{failureRows.length}</span>
                <span className="stage-2-stat__label">not found</span>
              </div>
            </div>
          </div>

          {/* Error banner if any */}
          {errorMsg && (
            <ErrorDisplay error={errorMsg} showIcon={true} />
          )}

          {/* Failures Cream Block (if any failed) */}
          {failureRows.length > 0 && (
            <div className="failures-block">
              <div className="failures-block__header">
                <div className="failures-block__title">
                  {failureRows.length} not found
                </div>
                <div className="failures-block__hint">Retry, or leave them out</div>
              </div>

              <div className="failures-list">
                {failureRows.map((fail) => (
                  <div key={fail.product_id} className="failure-item">
                    <span className="failure-item__name">{fail.name}</span>
                    <span className="failure-item__reason">{fail.reason}</span>
                  </div>
                ))}
              </div>

              <div className="failures-block__actions">
                <button
                  type="button"
                  className="btn-secondary"
                  onClick={handleRetry}
                  disabled={isRetrying}
                >
                  <RefreshIcon width={13} height={13} color="var(--amber-deep)" />
                  {isRetrying ? 'Retrying...' : `Retry these ${failureRows.length}`}
                </button>

                <button
                  type="button"
                  className="btn-outline"
                  onClick={() => {
                    setSelectedProductId(failureRows[0]?.product_id || '');
                    setIsModalOpen(true);
                  }}
                >
                  Add a URL myself
                </button>
              </div>
            </div>
          )}

          {/* Found Table */}
          <div className="found-section">
            <div className="found-header">
              <div className="found-title">{foundRows.length} found</div>
            </div>

            <div className="found-table-wrap">
              <table className="found-table">
                <thead>
                  <tr>
                    <th style={{ width: '45%' }}>Product</th>
                    <th style={{ width: '30%' }}>Source</th>
                    <th style={{ width: '25%' }}>Image</th>
                  </tr>
                </thead>
                <tbody>
                  {foundRows.map((row) => (
                    <tr key={row.product_id} className="found-row">
                      <td className="found-row__name">{row.name}</td>
                      <td className="found-row__source">{row.source}</td>
                      <td className="found-row__image">
                        <span
                          className={`quality-badge quality-badge--${
                            row.imageQuality === 'Good' ? 'good' : 'low'
                          }`}
                        >
                          {row.imageQuality}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          {/* Footer: Back on left, Continue with N rows on right */}
          <div className="stage-footer">
            <div className="stage-footer__left">
              <button
                type="button"
                className="btn-secondary"
                onClick={onBack}
              >
                ← Back
              </button>
            </div>
            <div className="stage-footer__right">
              <button
                type="button"
                className="btn-primary"
                onClick={() =>
                  onContinue({
                    brand,
                    foundRows,
                    count: foundRows.length,
                  })
                }
              >
                Continue with {foundRows.length} {foundRows.length === 1 ? 'row' : 'rows'}
              </button>
            </div>
          </div>
        </>
      )}

      {/* =========================================================================
          MODAL: Add a URL myself
          ========================================================================= */}
      {isModalOpen && (
        <div className="modal-backdrop">
          <div className="modal-content">
            <div className="modal-header">
              <h3 className="modal-title">Add a URL myself</h3>
              <button
                type="button"
                className="modal-close-btn"
                onClick={() => {
                  setIsModalOpen(false);
                  setManualUrlError('');
                }}
              >
                ✕
              </button>
            </div>

            <p className="modal-subtitle">
              Provide a direct product listing URL to collect specifications and images for this item.
            </p>

            <form onSubmit={handleManualUrlSubmit} className="modal-form">
              <div className="modal-field">
                <label className="modal-label">Product</label>
                <div className="brand-select-wrapper" style={{ width: '100%' }}>
                  <select
                    className="brand-select"
                    style={{ width: '100%' }}
                    value={selectedProductId}
                    onChange={(e) => setSelectedProductId(e.target.value)}
                  >
                    {failureRows.map((f) => (
                      <option key={f.product_id} value={f.product_id}>
                        {f.name}
                      </option>
                    ))}
                  </select>
                </div>
              </div>

              <div className="modal-field">
                <label className="modal-label">Product URL</label>
                <input
                  type="url"
                  className="modal-input"
                  placeholder="https://..."
                  value={manualUrl}
                  onChange={(e) => setManualUrl(e.target.value)}
                  required
                  autoFocus
                />
              </div>

              {manualUrlError && (
                <div className="stage-error-banner" style={{ marginTop: '8px' }}>
                  <WarningTriangleIcon width={14} height={14} color="var(--red)" />
                  <span>{manualUrlError}</span>
                </div>
              )}

              <div className="modal-actions">
                <button
                  type="button"
                  className="btn-secondary"
                  onClick={() => setIsModalOpen(false)}
                  disabled={manualUrlSubmitting}
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  className="btn-primary"
                  disabled={manualUrlSubmitting || !manualUrl.trim()}
                >
                  {manualUrlSubmitting ? 'Fetching...' : 'Submit & re-run'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
