import React, { useState, useEffect } from 'react';
import { DownloadIcon, RefreshIcon } from './Icons';

export function Stage5Build({ buildTarget, onBack, onStartNewBrand }) {
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

  // Fetch latest build and catalogue metadata
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

      // 2. Fetch brands for total approved products and brand count
      const brandsRes = await fetch('/brands');
      if (brandsRes.ok) {
        const brands = await brandsRes.json();
        if (brands && brands.length > 0) {
          const totalApproved = brands.reduce(
            (sum, b) => sum + (b.status_counts?.Approved || 0),
            0
          );
          setCatalogueMeta(prev => ({
            ...prev,
            brandCount: brands.length,
            productCount: totalApproved || prev.productCount
          }));
        }
      }
    } catch (err) {
      console.error('Error fetching build info:', err);
    }
  };

  useEffect(() => {
    fetchBuildInfo();
  }, []);

  // Trigger rebuild
  const handleTriggerBuild = async () => {
    try {
      setIsBuilding(true);
      setBuildError('');
      setBuildStatusText('Compiling catalogue pages...');

      const res = await fetch('/build', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({})
      });

      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || 'Failed to start build');
      }

      const { job_id } = await res.json();

      // Poll or listen for job completion
      const pollInterval = setInterval(async () => {
        try {
          const jobRes = await fetch(`/jobs/${job_id}`);
          if (jobRes.ok) {
            const job = await jobRes.json();
            if (job.status === 'completed') {
              clearInterval(pollInterval);
              setIsBuilding(false);
              setBuildStatusText('');
              fetchBuildInfo();
            } else if (job.status === 'failed') {
              clearInterval(pollInterval);
              setIsBuilding(false);
              setBuildError(job.error || 'Build failed. Check server logs.');
            } else if (job.progress_msg) {
              setBuildStatusText(job.progress_msg);
            }
          }
        } catch (pollErr) {
          console.error('Polling error:', pollErr);
        }
      }, 1000);
    } catch (err) {
      setIsBuilding(false);
      setBuildError(err.message);
    }
  };

  // If user entered from Stage 4 with an explicit trigger
  useEffect(() => {
    if (buildTarget?.autoTrigger) {
      handleTriggerBuild();
    }
  }, [buildTarget]);

  const downloadUrl = latestBuild?.url || '/dist/powerbank/combined/catalogue.pdf';
  const downloadFilename = latestBuild?.filename || 'catalogue.pdf';

  return (
    <div className="stage-5-container">
      {/* Error callout if rebuild failed */}
      {buildError && (
        <div className="card-edit-error" style={{ marginBottom: '20px' }}>
          <span>{buildError}</span>
        </div>
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
          <a
            href={downloadUrl}
            download={downloadFilename}
            className={`btn-download-pdf ${isBuilding ? 'btn-download-pdf--disabled' : ''}`}
            onClick={(e) => {
              if (isBuilding) e.preventDefault();
            }}
          >
            <DownloadIcon width={16} height={16} color="#FFFFFF" />
            <span>Download PDF</span>
          </a>
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
    </div>
  );
}
