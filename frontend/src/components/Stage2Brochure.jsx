import React, { useState, useEffect, useRef } from 'react';
import { TrashIcon, PdfIcon, WarningTriangleIcon } from './Icons';
import { ErrorDisplay } from './ErrorDisplay';

export function Stage2Brochure({ ingestResult, onBack, onStartCollecting }) {
  // Brand selection - pre-filled from Stage 1 user input, still editable
  const [brand, setBrand] = useState(ingestResult?.brand_name || '');
  const category = ingestResult?.category_name || 'Powerbank';
  const [knownBrands, setKnownBrands] = useState([]);
  const [isCustomBrand, setIsCustomBrand] = useState(false);

  // Rows state - keep real display name in display_name column!
  const [rows, setRows] = useState(() => {
    const products = ingestResult?.products || [];
    const dups = ingestResult?.duplicates || [];
    const dupMap = new Map();
    dups.forEach((d) => {
      dupMap.set(d.row_index, d.duplicates_row_index);
    });

    return products.map((p, idx) => {
      const rowNum = idx + 1;
      const duplicateOf = dupMap.get(rowNum);
      return {
        id: `row-${rowNum}-${Date.now()}`,
        rowNumber: rowNum,
        model_name: p.model_name || '',
        display_name: p.display_name || p.model_name || '',
        dp: p.dp !== undefined && p.dp !== null ? p.dp : '',
        mrp: p.mrp !== undefined && p.mrp !== null ? p.mrp : '',
        raw_text: p.raw_text || '',
        notes: p.notes || '',
        isDuplicate: !!duplicateOf,
        duplicateOf: duplicateOf || null,
      };
    });
  });

  // Brochure file
  const [brochureFile, setBrochureFile] = useState(null);
  const brochureInputRef = useRef(null);

  // Action state
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [isConfirmed, setIsConfirmed] = useState(false);
  const [errorMsg, setErrorMsg] = useState('');
  const [techDetails, setTechDetails] = useState(null);
  const [showIncompleteModal, setShowIncompleteModal] = useState(false);

  // Helper to parse price strings with currency symbols, commas, trailing text
  const parsePrice = (v) => {
    if (v === '' || v === null || v === undefined) return NaN;
    if (typeof v === 'number') return isNaN(v) ? NaN : v;
    const clean = String(v).replace(/[^0-9.]/g, '');
    return clean ? parseFloat(clean) : NaN;
  };

  // Helper to get array of missing field names for a row
  const getMissingFields = (r) => {
    const missing = [];
    if (!r.model_name || !r.model_name.trim()) missing.push('Model Name');
    const dpNum = parsePrice(r.dp);
    if (isNaN(dpNum) || dpNum <= 0) missing.push('DP');
    const mrpNum = parsePrice(r.mrp);
    if (isNaN(mrpNum) || mrpNum <= 0) missing.push('MRP');
    return missing;
  };

  // Fetch existing brands for the dropdown
  useEffect(() => {
    async function fetchBrands() {
      try {
        const res = await fetch('/brands');
        if (res.ok) {
          const list = await res.json();
          const names = list.map((b) => b.brand).filter(Boolean);
          setKnownBrands(names);
        }
      } catch (err) {
        console.warn('Could not fetch brands list:', err);
      }
    }
    fetchBrands();
  }, []);

  // Check on mount if brand is already active (e.g. user navigated back mid-collection)
  useEffect(() => {
    async function checkActiveJob() {
      if (!brand) return;
      try {
        const res = await fetch(`/brands/${encodeURIComponent(brand.trim())}/active-job`);
        if (res.ok) {
          const data = await res.json();
          if (data.active) {
            setIsConfirmed(true);
          }
        }
      } catch (e) {
        // ignore
      }
    }
    checkActiveJob();
  }, [brand]);

  // Update a specific cell
  const handleCellChange = (rowIndex, field, value) => {
    setIsConfirmed(false);
    setRows((prevRows) => {
      const updated = [...prevRows];
      updated[rowIndex] = { ...updated[rowIndex], [field]: value };
      return updated;
    });
  };

  // Clean cell value on blur
  const handleCellBlur = (rowIndex, field, value) => {
    if (field === 'dp' || field === 'mrp') {
      const num = parsePrice(value);
      if (!isNaN(num) && num > 0) {
        handleCellChange(rowIndex, field, num);
      }
    } else if (field === 'model_name' || field === 'display_name') {
      if (typeof value === 'string') {
        handleCellChange(rowIndex, field, value.trim());
      }
    }
  };

  // Delete a row
  const handleDeleteRow = (rowIndex) => {
    setIsConfirmed(false);
    setRows((prevRows) => prevRows.filter((_, idx) => idx !== rowIndex));
  };

  // Count duplicates in active rows
  const duplicateCount = rows.filter((r) => r.isDuplicate).length;

  // Incomplete rows calculation
  const incompleteRows = rows.map((r, idx) => {
    const missing = getMissingFields(r);
    if (missing.length > 0) {
      return {
        rowIndex: idx + 1,
        model_name: r.model_name,
        display_name: r.display_name || r.model_name || `Row ${idx + 1}`,
        missing: missing,
      };
    }
    return null;
  }).filter(Boolean);

  const incompleteCount = incompleteRows.length;

  // Duplicate display name detection across all rows
  const displayNameCounts = rows.reduce((acc, r) => {
    const dn = (r.display_name || '').trim().toLowerCase();
    if (dn) acc[dn] = (acc[dn] || 0) + 1;
    return acc;
  }, {});
  const dupDisplayNames = Object.keys(displayNameCounts).filter((k) => displayNameCounts[k] > 1);

  // Core execution of confirm and start
  const executeConfirmAndStart = async () => {
    if (!brand || rows.length === 0 || isSubmitting) return;

    // If already confirmed in this session or brand has active job, advance straight to Stage 3
    if (isConfirmed) {
      onStartCollecting({
        brand: brand.trim(),
        category: category,
        rowCount: rows.length,
      });
      return;
    }

    setIsSubmitting(true);
    setErrorMsg('');
    setTechDetails(null);

    try {
      // 1. Confirm products via POST /brands/{brand}/confirm (submits ALL non-deleted rows)
      const confirmPayload = {
        brand_name: brand.trim(),
        brand_code: ingestResult?.brand_code || null,
        category: category,
        domain: ingestResult?.domain || null,
        platform: ingestResult?.platform || null,
        column_mapping: ingestResult?.column_mapping || {},
        qualifier_tokens: ingestResult?.qualifier_tokens || [],
        dp_column_explanation: ingestResult?.dp_column_explanation || 'User confirmed price sheet rows',
        rows: rows.map((r) => {
          const dpNum = parsePrice(r.dp);
          const mrpNum = parsePrice(r.mrp);
          return {
            model_name: (r.model_name || '').trim(),
            display_name: (r.display_name || r.model_name || '').trim(),
            dp: !isNaN(dpNum) && dpNum > 0 ? dpNum : null,
            mrp: !isNaN(mrpNum) && mrpNum > 0 ? mrpNum : null,
            raw_text: r.raw_text,
            notes: r.notes,
          };
        }),
      };

      const confirmRes = await fetch(`/brands/${encodeURIComponent(brand.trim())}/confirm`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(confirmPayload),
      });

      const confirmData = await confirmRes.json();

      if (confirmRes.status === 409) {
        const detail = confirmData.detail;
        const activeId = typeof detail === 'object' ? detail.active_job_id : null;
        if (activeId) {
          // Brand already running a collection job — advance straight to Stage 3 to reattach
          setIsConfirmed(true);
          setIsSubmitting(false);
          onStartCollecting({
            brand: brand.trim(),
            category: category,
            rowCount: rows.length,
          });
          return;
        }

        const plainMsg = typeof detail === 'object' && detail.message
          ? detail.message
          : (typeof detail === 'string' ? detail : `Brand '${brand}' is currently locked by an active job.`);
        const techInfo = typeof detail === 'object' && detail.active_job_id
          ? `Job ID: ${detail.active_job_id}`
          : null;
        setErrorMsg(plainMsg);
        setTechDetails(techInfo);
        setIsSubmitting(false);
        return;
      }

      if (!confirmRes.ok) {
        const detail = confirmData.detail;
        const plainMsg = typeof detail === 'string' ? detail : 'Failed to confirm brand products.';
        throw new Error(plainMsg);
      }

      setIsConfirmed(true);

      // 2. Attach brochure if provided via POST /brands/{brand}/brochure
      if (brochureFile) {
        const formData = new FormData();
        formData.append('file', brochureFile);

        const brochureRes = await fetch(`/brands/${encodeURIComponent(brand.trim())}/brochure`, {
          method: 'POST',
          body: formData,
        });

        if (!brochureRes.ok) {
          const bData = await brochureRes.json();
          console.warn('Brochure upload failed:', bData.detail);
        }
      }

      // Advance to Stage 3
      onStartCollecting({
        brand: brand.trim(),
        category: category,
        rowCount: rows.length,
      });
    } catch (err) {
      console.error('Failed to confirm and start collection:', err);
      setIsSubmitting(false);
      setErrorMsg(err.message || 'An error occurred during confirmation.');
    }
  };

  // Button click handler: checks for duplicates and incomplete rows first
  const handleContinueClick = () => {
    if (!brand || rows.length === 0 || isSubmitting) return;

    // Check for duplicate display names before proceeding
    if (dupDisplayNames.length > 0) {
      setErrorMsg(`Duplicate Display Name detected for "${dupDisplayNames.join(', ')}". Each product must have a unique Display Name.`);
      return;
    }

    // If there are incomplete rows and not already confirmed, prompt with confirmation dialog
    if (incompleteCount > 0 && !isConfirmed) {
      setShowIncompleteModal(true);
      return;
    }

    executeConfirmAndStart();
  };

  return (
    <div className="stage-2-container">
      {/* Block 1: Amber Bar with Brand & Category */}
      <div className="stage-2-bar">
        <div className="stage-2-bar__left">
          <span className="stage-2-bar__label">Brand</span>
          {isCustomBrand ? (
            <div className="brand-input-wrapper">
              <input
                type="text"
                className="brand-input"
                value={brand}
                onChange={(e) => setBrand(e.target.value)}
                placeholder="Enter brand name"
                autoFocus
              />
              <button
                type="button"
                className="brand-input-cancel"
                onClick={() => setIsCustomBrand(false)}
                title="Select existing brand"
              >
                ✕
              </button>
            </div>
          ) : (
            <div className="brand-select-wrapper">
              <select
                className="brand-select"
                value={brand}
                onChange={(e) => {
                  if (e.target.value === '__custom__') {
                    setIsCustomBrand(true);
                  } else {
                    setBrand(e.target.value);
                  }
                }}
              >
                {/* Ensure current brand is included in options */}
                {brand && !knownBrands.includes(brand) && (
                  <option value={brand}>{brand}</option>
                )}
                {knownBrands.map((b) => (
                  <option key={b} value={b}>
                    {b}
                  </option>
                ))}
                <option value="__custom__">+ Enter different brand...</option>
              </select>
              <span className="brand-select__chevron" aria-hidden="true">
                <svg width="10" height="6" viewBox="0 0 10 6" fill="none" stroke="#BA7517" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M1 1L5 5L9 1" />
                </svg>
              </span>
            </div>
          )}

          <div className="stage-2-bar__category">
            <span className="stage-2-bar__label">Category</span>
            <span className="category-pill">{category}</span>
          </div>
        </div>

        <div className="stage-2-bar__right">
          <div className="stage-2-stat">
            <span className="stage-2-stat__number">{rows.length}</span>
            <span className="stage-2-stat__label">rows read</span>
          </div>
          <div className="stage-2-stat">
            <span className="stage-2-stat__number">{duplicateCount}</span>
            <span className="stage-2-stat__label">
              {duplicateCount === 1 ? 'duplicate' : 'duplicates'}
            </span>
          </div>
          {dupDisplayNames.length > 0 && (
            <div className="stage-2-stat" style={{ borderLeft: '1px solid var(--amber-dark)', paddingLeft: '14px' }}>
              <span className="stage-2-stat__number" style={{ color: 'var(--amber-deep)' }}>{dupDisplayNames.length}</span>
              <span className="stage-2-stat__label">dup names</span>
            </div>
          )}
          {incompleteCount > 0 && (
            <div className="stage-2-stat" style={{ borderLeft: '1px solid var(--amber-dark)', paddingLeft: '14px' }}>
              <span className="stage-2-stat__number" style={{ color: 'var(--amber-deep)' }}>{incompleteCount}</span>
              <span className="stage-2-stat__label">incomplete</span>
            </div>
          )}
        </div>
      </div>

      {/* Block 2: Parsed Rows Table */}
      <div className="parsed-rows-section">
        <div className="parsed-rows-header">
          <div className="parsed-rows-title">Parsed rows</div>
          <div className="parsed-rows-hint">
            {incompleteCount > 0
              ? `${incompleteCount} ${incompleteCount === 1 ? 'row is' : 'rows are'} missing price or name. Fill them in inline or proceed anyway.`
              : 'Click any cell to edit'}
          </div>
        </div>

        <div className="parsed-rows-table-wrap">
          <table className="parsed-rows-table">
            <thead>
              <tr>
                <th style={{ width: '30%' }}>Model</th>
                <th style={{ width: '35%' }}>Display name</th>
                <th style={{ width: '15%' }}>DP</th>
                <th style={{ width: '15%' }}>MRP</th>
                <th style={{ width: '50px', textAlign: 'center' }}></th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row, idx) => {
                const missingModel = !row.model_name || !row.model_name.trim();
                const dpNum = parsePrice(row.dp);
                const mrpNum = parsePrice(row.mrp);
                const missingDp = isNaN(dpNum) || dpNum <= 0;
                const missingMrp = isNaN(mrpNum) || mrpNum <= 0;
                const rowIsIncomplete = missingModel || missingDp || missingMrp;

                return (
                  <tr
                    key={row.id}
                    className={`parsed-row ${row.isDuplicate ? 'parsed-row--duplicate' : ''}`}
                    style={rowIsIncomplete ? { backgroundColor: 'var(--cream)' } : {}}
                  >
                    {/* Model column */}
                    <td>
                      <div className="model-cell-wrapper">
                        <div className="model-cell-main">
                          {row.isDuplicate && (
                            <span className="dup-warning-icon" title={`Same as row ${row.duplicateOf}`}>
                              <WarningTriangleIcon width={15} height={15} color="var(--amber-dark)" />
                            </span>
                          )}
                          {rowIsIncomplete && !row.isDuplicate && (
                            <span className="dup-warning-icon" title="Incomplete row — missing information">
                              <WarningTriangleIcon width={15} height={15} color="var(--amber-dark)" />
                            </span>
                          )}
                          <input
                            type="text"
                            className={`cell-input ${missingModel ? 'cell-input--amber' : ''}`}
                            placeholder={missingModel ? 'Enter model name' : ''}
                            value={row.model_name}
                            onChange={(e) => handleCellChange(idx, 'model_name', e.target.value)}
                            onBlur={(e) => handleCellBlur(idx, 'model_name', e.target.value)}
                          />
                        </div>
                        {row.isDuplicate && (
                          <div className="dup-note">same as row {row.duplicateOf}</div>
                        )}
                        {rowIsIncomplete && (
                          <div className="dup-note" style={{ color: 'var(--amber-deep)' }}>
                            {missingDp && missingMrp
                              ? 'Missing DP & MRP'
                              : missingDp
                              ? 'Missing DP'
                              : missingMrp
                              ? 'Missing MRP'
                              : 'Missing Model'}
                          </div>
                        )}
                      </div>
                    </td>

                    {/* Display Name column */}
                    <td>
                      <div className="model-cell-wrapper">
                        <div className="model-cell-main">
                          {displayNameCounts[(row.display_name || '').trim().toLowerCase()] > 1 && (
                            <span className="dup-warning-icon" title="Duplicate Display Name across rows">
                              <WarningTriangleIcon width={15} height={15} color="var(--amber-dark)" />
                            </span>
                          )}
                          <input
                            type="text"
                            className={`cell-input ${displayNameCounts[(row.display_name || '').trim().toLowerCase()] > 1 ? 'cell-input--amber' : ''}`}
                            value={row.display_name}
                            onChange={(e) => handleCellChange(idx, 'display_name', e.target.value)}
                            onBlur={(e) => handleCellBlur(idx, 'display_name', e.target.value)}
                          />
                        </div>
                        {displayNameCounts[(row.display_name || '').trim().toLowerCase()] > 1 && (
                          <div className="dup-note" style={{ color: 'var(--amber-deep)' }}>
                            Duplicate display name
                          </div>
                        )}
                      </div>
                    </td>

                    {/* DP column */}
                    <td>
                      <input
                        type="text"
                        className={`cell-input ${missingDp ? 'cell-input--amber' : ''}`}
                        placeholder={missingDp ? 'Enter DP' : ''}
                        value={row.dp}
                        onChange={(e) => handleCellChange(idx, 'dp', e.target.value)}
                        onBlur={(e) => handleCellBlur(idx, 'dp', e.target.value)}
                      />
                    </td>

                    {/* MRP column */}
                    <td>
                      <input
                        type="text"
                        className={`cell-input ${missingMrp ? 'cell-input--amber' : ''}`}
                        placeholder={missingMrp ? 'Enter MRP' : ''}
                        value={row.mrp}
                        onChange={(e) => handleCellChange(idx, 'mrp', e.target.value)}
                        onBlur={(e) => handleCellBlur(idx, 'mrp', e.target.value)}
                      />
                    </td>

                    {/* Delete action */}
                    <td style={{ textAlign: 'center' }}>
                      <button
                        type="button"
                        className="row-delete-btn"
                        onClick={() => handleDeleteRow(idx)}
                        title="Delete this row"
                      >
                        <TrashIcon width={16} height={16} color="var(--grey-400)" />
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>

      {/* Block 3: Brochure Strip */}
      <div className="brochure-strip">
        <input
          ref={brochureInputRef}
          type="file"
          accept=".pdf"
          style={{ display: 'none' }}
          onChange={(e) => {
            if (e.target.files && e.target.files.length > 0) {
              setBrochureFile(e.target.files[0]);
            }
          }}
        />

        <div className="brochure-strip__left">
          <PdfIcon width={26} height={26} color="var(--amber-dark)" />
          <div className="brochure-strip__text">
            <div className="brochure-strip__title">Brand brochure</div>
            <div className="brochure-strip__subtitle">
              {brochureFile ? (
                <span>
                  {brochureFile.name} ({(brochureFile.size / (1024 * 1024)).toFixed(1)} MB)
                </span>
              ) : (
                'Optional'
              )}
            </div>
          </div>
        </div>

        <div className="brochure-strip__right">
          {brochureFile ? (
            <button
              type="button"
              className="btn-secondary"
              onClick={() => setBrochureFile(null)}
            >
              Remove PDF
            </button>
          ) : (
            <button
              type="button"
              className="btn-secondary"
              onClick={() => brochureInputRef.current?.click()}
            >
              Choose PDF
            </button>
          )}
        </div>
      </div>

      {/* Error banner if submission failed or brand is locked */}
      {errorMsg && (
        <ErrorDisplay error={errorMsg} technicalDetails={techDetails} showIcon={true} />
      )}

      {/* Incomplete Rows Confirmation Modal */}
      {showIncompleteModal && (
        <div
          className="modal-overlay"
          style={{
            position: 'fixed',
            top: 0,
            left: 0,
            right: 0,
            bottom: 0,
            backgroundColor: 'rgba(0, 0, 0, 0.45)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            zIndex: 1000,
            padding: '16px',
          }}
        >
          <div
            className="modal-content"
            style={{
              background: '#FFFFFF',
              borderRadius: '12px',
              maxWidth: '520px',
              width: '100%',
              padding: '24px',
              boxShadow: '0 12px 32px rgba(0, 0, 0, 0.18)',
              border: '1px solid var(--grey-50)',
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', gap: '10px', marginBottom: '12px' }}>
              <WarningTriangleIcon width={22} height={22} color="var(--amber-dark)" />
              <h3 style={{ margin: 0, fontSize: '18px', fontWeight: 600, color: 'var(--amber-deep)' }}>
                Incomplete Rows Detected ({incompleteCount})
              </h3>
            </div>

            <p style={{ fontSize: '14px', color: '#4A4A48', lineHeight: '1.5', margin: '0 0 16px 0' }}>
              The following {incompleteCount === 1 ? 'row has' : 'rows have'} missing information. You can go back and fill them in now, or proceed to collection anyway:
            </p>

            <div
              style={{
                maxHeight: '200px',
                overflowY: 'auto',
                border: '1px solid var(--cream)',
                borderRadius: '8px',
                background: 'var(--page-bg)',
                padding: '8px 12px',
                marginBottom: '20px',
              }}
            >
              {incompleteRows.map((item) => (
                <div
                  key={item.rowIndex}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                    padding: '8px 0',
                    borderBottom: '1px solid var(--grey-50)',
                    fontSize: '13px',
                  }}
                >
                  <span style={{ fontWeight: 600, color: '#2C2C2A' }}>
                    {item.display_name}
                  </span>
                  <span
                    style={{
                      background: 'var(--cream)',
                      color: 'var(--amber-deep)',
                      fontSize: '12px',
                      fontWeight: 500,
                      padding: '2px 8px',
                      borderRadius: '4px',
                      border: '1px solid var(--amber-pale)',
                    }}
                  >
                    Missing {item.missing.join(' & ')}
                  </span>
                </div>
              ))}
            </div>

            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '12px' }}>
              <button
                type="button"
                className="btn-secondary"
                onClick={() => setShowIncompleteModal(false)}
              >
                Go back & edit
              </button>
              <button
                type="button"
                className="btn-primary"
                onClick={() => {
                  setShowIncompleteModal(false);
                  executeConfirmAndStart();
                }}
              >
                Proceed anyway ({rows.length} {rows.length === 1 ? 'row' : 'rows'})
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Footer Actions */}
      <div className="stage-footer">
        <div className="stage-footer__left">
          <button
            type="button"
            className="btn-secondary"
            onClick={onBack}
            disabled={isSubmitting}
          >
            ← Back
          </button>
        </div>
        <div className="stage-footer__right">
          <button
            type="button"
            className="btn-primary"
            disabled={isSubmitting || rows.length === 0 || !brand.trim()}
            onClick={handleContinueClick}
          >
            {isSubmitting
              ? 'Saving...'
              : isConfirmed
              ? `Continue to collection (${rows.length} ${rows.length === 1 ? 'row' : 'rows'})`
              : `Start collecting ${rows.length} ${rows.length === 1 ? 'row' : 'rows'}`}
          </button>
        </div>
      </div>
    </div>
  );
}
