import React, { useState, useEffect, useRef } from 'react';
import { DownloadIcon, RefreshIcon } from './Icons';
import { ErrorDisplay } from './ErrorDisplay';

export function Stage5Build({ buildTarget, onBack, onStartNewBrand }) {
  const category = buildTarget?.category || '';
  const [latestBuild, setLatestBuild] = useState(null);
  const [catalogueMeta, setCatalogueMeta] = useState({
    pageCount: 27,
    brandCount: 5,
    productCount: 40,
    sizeDisplay: '9.2 MB'
  });
  const [isBuilding, setIsBuilding] = useState(false);
  const [buildStatusText, setBuildStatusText] = useState('');
  const [buildError, setBuildError] = useState('');

  const [brandOrder, setBrandOrder] = useState([]);
  const [missingBrand, setMissingBrand] = useState(null);
  const [insertIndex, setInsertIndex] = useState(0);
  const [showOrderModal, setShowOrderModal] = useState(false);

  const autoTriggeredRef = useRef(false);
  const isTriggeringRef = useRef(false);
  const pollIntervalRef = useRef(null);

  // Clean up poll interval on unmount
  useEffect(() => {
    return () => {
      if (pollIntervalRef.current) {
        clearInterval(pollIntervalRef.current);
      }
    };
  }, []);

  // Fetch latest build, catalogue metadata, and brand order
  const fetchBuildInfo = async () => {
    try {
      // 1. Fetch builds
      const buildsRes = await fetch('/builds');
      if (buildsRes.ok) {
        const builds = await buildsRes.json();
        if (builds && builds.length > 0) {
          const latest = builds[0];
          setLatestBuild(latest);

          const sizeInMB = (latest.size / (1024 * 1024)).toFixed(1);
          setCatalogueMeta(prev => ({
            ...prev,
            pageCount: latest.page_count || prev.pageCount,
            sizeDisplay: `${sizeInMB} MB`
          }));
        }
      }

      // 2. Fetch config for brand_order
      let currentOrder = [];
      const cfgRes = await fetch('/config');
      if (cfgRes.ok) {
        const cfgData = await cfgRes.json();
        currentOrder = cfgData.brand_order || [];
        setBrandOrder(currentOrder);
      }

      // 3. Fetch brands for total approved products and brand count
      const brandsRes = await fetch('/brands');
      if (brandsRes.ok) {
        const brands = await brandsRes.json();
        if (brands && brands.length > 0) {
          const approvedBrands = [
            ...new Set(
              brands
                .filter((b) => (b.status_counts?.Approved || 0) > 0)
                .map((b) => b.brand)
            ),
          ];

          const totalApproved = brands.reduce(
            (sum, b) => sum + (b.status_counts?.Approved || 0),
            0
          );
          setCatalogueMeta((prev) => ({
            ...prev,
            brandCount: new Set(brands.map((b) => b.brand)).size,
            productCount: totalApproved || prev.productCount,
          }));

          // Check if any approved brand is missing from brand_order
          const orderLower = currentOrder.map(x => x.toLowerCase());
          const unsequenced = approvedBrands.find(b => !orderLower.includes(b.toLowerCase()));
          if (unsequenced) {
            setMissingBrand(unsequenced);
            setInsertIndex(currentOrder.length); // Default: at the end
            setShowOrderModal(true);
          }
        }
      }
    } catch (err) {
      console.error('Error fetching build info:', err);
    }
  };

  useEffect(() => {
    fetchBuildInfo();
  }, []);

  const startJobPolling = (jobId) => {
    if (pollIntervalRef.current) {
      clearInterval(pollIntervalRef.current);
    }
    setIsBuilding(true);

    let lastProgressTime = Date.now();
    let lastProgressMsg = '';
    const STALL_TIMEOUT_MS = 180 * 1000; // 3 minutes without progress

    pollIntervalRef.current = setInterval(async () => {
      try {
        const jobRes = await fetch(`/jobs/${jobId}`);
        if (jobRes.ok) {
          const job = await jobRes.json();
          const currentMsg = job.message || job.progress_msg || '';

          if (currentMsg && currentMsg !== lastProgressMsg) {
            lastProgressMsg = currentMsg;
            lastProgressTime = Date.now();
            setBuildStatusText(currentMsg);
          }

          if (job.status === 'done' || job.status === 'completed') {
            clearInterval(pollIntervalRef.current);
            pollIntervalRef.current = null;
            setIsBuilding(false);
            setBuildStatusText('');
            isTriggeringRef.current = false;
            fetchBuildInfo();
          } else if (job.status === 'failed' || job.status === 'cancelled') {
            clearInterval(pollIntervalRef.current);
            pollIntervalRef.current = null;
            setIsBuilding(false);
            setBuildStatusText('');
            isTriggeringRef.current = false;
            setBuildError(job.error || job.message || 'Build failed. Check server logs.');
          } else {
            // Check for stall
            if (Date.now() - lastProgressTime > STALL_TIMEOUT_MS) {
              clearInterval(pollIntervalRef.current);
              pollIntervalRef.current = null;
              setIsBuilding(false);
              isTriggeringRef.current = false;
              setBuildError('Build appears stalled (no progress received for 3 minutes). Please check server logs or retry.');
            }
          }
        }
      } catch (pollErr) {
        console.error('Polling error:', pollErr);
      }
    }, 1000);
  };

  // Trigger rebuild
  const handleTriggerBuild = async (overrideOrder) => {
    if (isTriggeringRef.current || isBuilding) return;
    isTriggeringRef.current = true;

    try {
      setIsBuilding(true);
      setBuildError('');
      setBuildStatusText('Compiling catalogue pages...');

      const orderToSend = overrideOrder || (brandOrder.length > 0 ? brandOrder : undefined);
      const buildPayload = {};
      if (orderToSend) buildPayload.brand_order = orderToSend;
      if (buildTarget?.brand) buildPayload.brand = buildTarget.brand;
      if (category) buildPayload.category = category;

      const res = await fetch('/build', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(buildPayload)
      });

      if (res.status === 409) {
        // Build already running - attach to active job
        const conflictData = await res.json().catch(() => ({}));
        if (conflictData.active_job_id) {
          setBuildStatusText('Attaching to in-progress build...');
          startJobPolling(conflictData.active_job_id);
          return;
        } else {
          throw new Error(conflictData.detail || 'Build already in progress');
        }
      }

      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || 'Failed to start build');
      }

      const { job_id } = await res.json();
      startJobPolling(job_id);
    } catch (err) {
      setIsBuilding(false);
      isTriggeringRef.current = false;
      setBuildError(err.message);
    }
  };

  const handleConfirmBrandPlacement = () => {
    if (!missingBrand) return;
    const updated = [...brandOrder];
    updated.splice(insertIndex, 0, missingBrand);
    setBrandOrder(updated);
    setShowOrderModal(false);
    setMissingBrand(null);
    handleTriggerBuild(updated);
  };

  // If user entered from Stage 4 with an explicit trigger
  useEffect(() => {
    if (buildTarget?.autoTrigger && !autoTriggeredRef.current) {
      autoTriggeredRef.current = true;
      handleTriggerBuild();
    }
  }, [buildTarget]);

  return (
    <div className="stage-5-container">
      {/* Error callout if rebuild failed */}
      {buildError && (
        <ErrorDisplay error={buildError} showIcon={true} />
      )}

      {/* Block 1: Amber Bar */}
      <div className="stage-5-amber-bar">
        <div className="stage-5-amber-bar__left">
          <h2 className="stage-5-amber-bar__title">
            {isBuilding ? 'Compiling your catalogue...' : 'Your catalogue is ready'}
          </h2>
          <div className="stage-5-amber-bar__meta">
            {isBuilding ? (
              <span>{buildStatusText || 'Rendering high-resolution A4 print pages...'}</span>
            ) : (
              <span>
                {catalogueMeta.pageCount} pages · {catalogueMeta.brandCount} brands · {catalogueMeta.productCount} products · {catalogueMeta.sizeDisplay}
              </span>
            )}
          </div>
        </div>

        <div className="stage-5-amber-bar__right">
          {isBuilding || !latestBuild?.url ? (
            <button
              type="button"
              className="btn-download-pdf btn-download-pdf--disabled"
              disabled
              title={isBuilding ? "Compilation in progress..." : "No build available yet"}
            >
              <DownloadIcon width={16} height={16} color="#FFFFFF" />
              <span>{isBuilding ? "Compiling PDF..." : "Download PDF"}</span>
            </button>
          ) : (
            <a
              href={latestBuild.url}
              download={latestBuild.filename || 'catalogue.pdf'}
              className="btn-download-pdf"
            >
              <DownloadIcon width={16} height={16} color="#FFFFFF" />
              <span>Download PDF</span>
            </a>
          )}
        </div>
      </div>

      {/* Block 2: Cream Strip */}
      <div className="stage-5-cream-strip">
        <div className="stage-5-cream-strip__label">
          Add another brand to this catalogue?
        </div>
        <button
          type="button"
          className="btn-start-new-brand"
          onClick={onStartNewBrand}
        >
          Start a new brand
        </button>
      </div>

      {/* Footer Navigation */}
      <div className="stage-5-footer">
        <button
          type="button"
          className="btn-back"
          onClick={onBack}
        >
          Back to approve
        </button>

        <button
          type="button"
          className="btn-rebuild"
          onClick={handleTriggerBuild}
          disabled={isBuilding}
        >
          <RefreshIcon width={14} height={14} color="#412402" />
          <span>{isBuilding ? 'Building...' : 'Rebuild'}</span>
        </button>
      </div>

      {/* Brand Sequence / Placement Modal */}
      {showOrderModal && missingBrand && (
        <div className="modal-overlay" style={{
          position: 'fixed', top: 0, left: 0, right: 0, bottom: 0,
          backgroundColor: 'rgba(0, 0, 0, 0.5)', display: 'flex',
          alignItems: 'center', justifyContent: 'center', zIndex: 1000
        }}>
          <div className="modal-content" style={{
            background: '#FFFFFF', padding: '24px', borderRadius: '12px',
            maxWidth: '480px', width: '90%', boxShadow: '0 8px 24px rgba(0,0,0,0.15)'
          }}>
            <h3 style={{ margin: '0 0 12px 0', fontSize: '18px', fontWeight: 600, color: '#1A1A1A' }}>
              Add {missingBrand} to Catalogue Sequence
            </h3>
            <p style={{ margin: '0 0 16px 0', fontSize: '14px', color: '#666666' }}>
              <strong>{missingBrand}</strong> is newly approved and not yet in the catalogue brand order. Where would you like it to appear?
            </p>
            <div style={{ marginBottom: '20px' }}>
              <label style={{ display: 'block', fontSize: '13px', fontWeight: 500, marginBottom: '6px', color: '#333' }}>
                Insert Position
              </label>
              <select
                value={insertIndex}
                onChange={(e) => setInsertIndex(Number(e.target.value))}
                style={{
                  width: '100%', padding: '8px 12px', borderRadius: '6px',
                  border: '1px solid #D0D0D0', fontSize: '14px', background: '#FAFAFA'
                }}
              >
                <option value={0}>At the beginning (First brand)</option>
                {brandOrder.map((b, idx) => (
                  <option key={b} value={idx + 1}>
                    After {b} {idx === brandOrder.length - 1 ? '(At the end)' : ''}
                  </option>
                ))}
              </select>
            </div>
            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '12px' }}>
              <button
                type="button"
                onClick={() => setShowOrderModal(false)}
                style={{
                  padding: '8px 16px', borderRadius: '6px', border: '1px solid #D0D0D0',
                  background: 'transparent', cursor: 'pointer', fontSize: '14px'
                }}
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={handleConfirmBrandPlacement}
                style={{
                  padding: '8px 16px', borderRadius: '6px', border: 'none',
                  background: '#F59E0B', color: '#FFFFFF', fontWeight: 600, cursor: 'pointer', fontSize: '14px'
                }}
              >
                Save & Build
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
