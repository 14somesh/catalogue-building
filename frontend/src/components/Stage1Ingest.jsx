import React, { useState, useEffect, useRef } from 'react';
import { UploadIcon, WarningTriangleIcon } from './Icons';
import { ErrorDisplay } from './ErrorDisplay';

export function Stage1Ingest({ onIngestComplete }) {
  // Brand & Category inputs
  const [brand, setBrand] = useState('');
  const [category, setCategory] = useState('Powerbank');
  const [newCategory, setNewCategory] = useState('');
  const [isAddingNewCategory, setIsAddingNewCategory] = useState(false);
  const [knownBrands, setKnownBrands] = useState([]);
  const [knownCategories, setKnownCategories] = useState(['Powerbank']);

  // File & Upload state
  const [file, setFile] = useState(null);
  const [uploadId, setUploadId] = useState(null);
  const [isUploading, setIsUploading] = useState(false);
  const [isIngesting, setIsIngesting] = useState(false);
  const [progressMsg, setProgressMsg] = useState('');
  const [errorMsg, setErrorMsg] = useState('');
  const [techDetails, setTechDetails] = useState(null);
  const [isDragOver, setIsDragOver] = useState(false);

  // Unusable / Low-confidence extraction state
  const [unusableExtraction, setUnusableExtraction] = useState(null);

  const fileInputRef = useRef(null);

  // Fetch known brands and categories for autocomplete & dropdown
  useEffect(() => {
    async function loadMetadata() {
      try {
        const [brandsRes, catsRes] = await Promise.all([
          fetch('/brands'),
          fetch('/categories'),
        ]);

        if (brandsRes.ok) {
          const list = await brandsRes.json();
          const names = list.map((b) => b.brand).filter(Boolean);
          setKnownBrands(names);
        }

        if (catsRes.ok) {
          const catList = await catsRes.json();
          if (Array.isArray(catList) && catList.length > 0) {
            setKnownCategories(catList);
            setCategory(catList[0]);
          }
        }
      } catch (err) {
        console.warn('Could not load brands or categories:', err);
      }
    }
    loadMetadata();
  }, []);

  const handleFileSelection = async (selectedFile) => {
    if (!selectedFile) return;
    setErrorMsg('');
    setTechDetails(null);
    setUnusableExtraction(null);
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
      setTechDetails(err.stack || null);
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

  const effectiveBrand = brand.trim();
  const effectiveCategory = (isAddingNewCategory ? newCategory : category).trim();
  const isFormValid = uploadId && effectiveBrand && effectiveCategory;

  const validateAndProceed = (result) => {
    const products = result?.products || [];
    const usableRows = products.filter(
      (p) =>
        Boolean((p.model_name || p.display_name)?.trim()) &&
        ((p.dp != null && !isNaN(Number(p.dp)) && Number(p.dp) > 0) ||
          (p.mrp != null && !isNaN(Number(p.mrp)) && Number(p.mrp) > 0))
    );

    // If nothing extracted, or fewer than half the rows have a usable name and price: stay on Stage 1
    if (products.length === 0 || usableRows.length < Math.ceil(products.length / 2)) {
      setIsIngesting(false);
      setProgressMsg('');
      setUnusableExtraction({
        total: products.length,
        usableCount: usableRows.length,
        products: products,
        rawResult: result,
      });
      return;
    }

    // At least half rows usable -> proceed to Stage 2 with brand & category
    onIngestComplete({
      ...result,
      brand_name: effectiveBrand,
      category_name: effectiveCategory,
    });
  };

  const handleReadSheet = async () => {
    if (!isFormValid || isIngesting) return;
    setIsIngesting(true);
    setErrorMsg('');
    setTechDetails(null);
    setUnusableExtraction(null);
    setProgressMsg('Initiating ingestion job...');

    try {
      const res = await fetch('/ingest', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          upload_id: uploadId,
          brand: effectiveBrand,
          category: effectiveCategory,
        }),
      });

      const data = await res.json();
      if (!res.ok) {
        throw new Error(data.detail || 'Failed to enqueue ingest job');
      }

      const jobId = data.job_id;

      let eventSource;
      let completed = false;

      const finishWithResult = (result) => {
        if (completed) return;
        completed = true;
        if (eventSource) eventSource.close();
        validateAndProceed(result);
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
                setProgressMsg('');
                setErrorMsg(parsed.error || 'Ingest job failed');
                setTechDetails(parsed.technical_details || null);
              }
            }
          } catch (e) {
            // Ignore parse errors on keep-alives
          }
        };

        eventSource.onerror = () => {
          eventSource.close();
          pollJobStatus(jobId, finishWithResult);
        };
      } catch (err) {
        pollJobStatus(jobId, finishWithResult);
      }
    } catch (err) {
      console.error('Ingest error:', err);
      setIsIngesting(false);
      setProgressMsg('');
      setErrorMsg(err.message || 'Failed to analyze price sheet');
      setTechDetails(err.stack || null);
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
          setProgressMsg('');
          setErrorMsg(job.error || 'Ingest job failed');
          setTechDetails(job.technical_details || null);
        }
      } catch (e) {
        console.warn('Poll error:', e);
      }
    }, 1000);
  };

  const isRetryState =
    progressMsg &&
    (progressMsg.includes('busy right now') ||
      progressMsg.includes('Trying again') ||
      progressMsg.includes('Falling back'));

  return (
    <div className="stage-1-container">
      {/* 1. Pre-upload Brand and Category Fields */}
      <div className="stage-1-meta-form">
        <div className="stage-1-field">
          <label htmlFor="brand-input">Brand name *</label>
          <input
            id="brand-input"
            list="brand-options"
            type="text"
            className="stage-1-input"
            placeholder="e.g. Portronics, Pebble, Stuffcool"
            value={brand}
            onChange={(e) => setBrand(e.target.value)}
            disabled={isIngesting}
          />
          <datalist id="brand-options">
            {knownBrands.map((b) => (
              <option key={b} value={b} />
            ))}
          </datalist>
        </div>

        <div className="stage-1-field">
          <label htmlFor="category-select">Category *</label>
          {isAddingNewCategory ? (
            <div className="stage-1-new-cat-row">
              <input
                type="text"
                className="stage-1-input"
                placeholder="Enter new category name"
                value={newCategory}
                onChange={(e) => setNewCategory(e.target.value)}
                autoFocus
                disabled={isIngesting}
              />
              <button
                type="button"
                className="stage-1-new-cat-cancel"
                onClick={() => {
                  setIsAddingNewCategory(false);
                  setNewCategory('');
                }}
                title="Cancel adding new category"
              >
                ✕
              </button>
            </div>
          ) : (
            <select
              id="category-select"
              className="stage-1-select"
              value={category}
              onChange={(e) => {
                if (e.target.value === '__ADD_NEW__') {
                  setIsAddingNewCategory(true);
                } else {
                  setCategory(e.target.value);
                }
              }}
              disabled={isIngesting}
            >
              {knownCategories.map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
              <option value="__ADD_NEW__">+ Add new category</option>
            </select>
          )}
        </div>
      </div>

      {/* 2. Upload Drop Zone */}
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
                setTechDetails(null);
                setProgressMsg('');
                setUnusableExtraction(null);
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

        {isIngesting && progressMsg && (
          <div className={`drop-zone__progress ${isRetryState ? 'drop-zone__progress--retry' : ''}`}>
            <span className={`spinner-indicator ${isRetryState ? 'spinner-indicator--retry' : ''}`}></span>
            <span>{progressMsg}</span>
          </div>
        )}

        {errorMsg && (
          <ErrorDisplay error={errorMsg} technicalDetails={techDetails} />
        )}
      </div>

      {/* 3. Low-Confidence / Unusable Extraction Review State */}
      {unusableExtraction && (
        <div className="stage-1-unusable">
          <div className="stage-1-unusable__header">
            <div className="stage-1-unusable__title">
              Price sheet could not be parsed reliably
            </div>
            <div className="stage-1-unusable__desc">
              {unusableExtraction.total === 0 ? (
                'No product rows could be recognized in this file. A catalogue requires a list of products each with a dealer price (DP) and an MRP.'
              ) : (
                `We found ${unusableExtraction.total} rows, but only ${unusableExtraction.usableCount} had a usable product name and price. A catalogue requires a list of products each with a dealer price (DP) and an MRP. Here is what was extracted from your sheet:`
              )}
            </div>
          </div>

          {unusableExtraction.products.length > 0 && (
            <div className="stage-1-unusable__table-container">
              <table className="stage-1-unusable__table">
                <thead>
                  <tr>
                    <th style={{ width: '40px' }}>#</th>
                    <th>Model name</th>
                    <th style={{ width: '120px' }}>Dealer price (DP)</th>
                    <th style={{ width: '120px' }}>MRP</th>
                  </tr>
                </thead>
                <tbody>
                  {unusableExtraction.products.map((p, idx) => (
                    <tr key={idx}>
                      <td>{idx + 1}</td>
                      <td>{p.model_name || <em style={{ color: 'var(--amber-dark)' }}>Missing</em>}</td>
                      <td>
                        {p.dp != null ? (
                          `₹${p.dp}`
                        ) : (
                          <span style={{ color: 'var(--amber-dark)' }}>Missing DP</span>
                        )}
                      </td>
                      <td>
                        {p.mrp != null ? (
                          `₹${p.mrp}`
                        ) : (
                          <span style={{ color: 'var(--amber-dark)' }}>Missing MRP</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          <div className="stage-1-unusable__actions">
            <button
              type="button"
              className="btn-secondary"
              onClick={() => {
                setUnusableExtraction(null);
                setFile(null);
                setUploadId(null);
              }}
            >
              Choose different file
            </button>
            {unusableExtraction.products.length > 0 && (
              <button
                type="button"
                className="btn-secondary"
                style={{ borderColor: 'var(--amber)' }}
                onClick={() => {
                  // Allow proceeding to Stage 2 so user can fill in details
                  onIngestComplete({
                    ...unusableExtraction.rawResult,
                    brand_name: effectiveBrand,
                    category_name: effectiveCategory,
                  });
                }}
              >
                Proceed to Stage 2 anyway
              </button>
            )}
          </div>
        </div>
      )}

      {/* 4. Bottom Right: Primary Teal Action Button */}
      <div className="stage-footer">
        <div className="stage-footer__left">
          {(!effectiveBrand || !effectiveCategory) && (
            <span style={{ fontSize: '13px', color: 'var(--amber-mid)' }}>
              {!effectiveBrand
                ? 'Enter a brand name to read sheet'
                : 'Select or enter a category to read sheet'}
            </span>
          )}
        </div>
        <div className="stage-footer__right">
          <button
            type="button"
            className="btn-primary"
            disabled={!isFormValid || isIngesting || isUploading}
            onClick={handleReadSheet}
          >
            {isIngesting ? 'Reading sheet...' : 'Read the sheet'}
          </button>
        </div>
      </div>
    </div>
  );
}
