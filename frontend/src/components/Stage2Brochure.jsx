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
  const [errorMsg, setErrorMsg] = useState('');

  // Helper to check if a row is complete (usable name, valid DP, valid MRP)
  const isRowComplete = (r) => {
    const hasName = Boolean(r.model_name && r.model_name.trim());
    const hasDp = r.dp !== '' && r.dp !== null && !isNaN(Number(r.dp)) && Number(r.dp) > 0;
    const hasMrp = r.mrp !== '' && r.mrp !== null && !isNaN(Number(r.mrp)) && Number(r.mrp) > 0;
    return hasName && hasDp && hasMrp;
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

  // Update a specific cell
  const handleCellChange = (rowIndex, field, value) => {
    setRows((prevRows) => {
      const updated = [...prevRows];
      updated[rowIndex] = { ...updated[rowIndex], [field]: value };
      return updated;
    });
  };

  // Delete a row
  const handleDeleteRow = (rowIndex) => {
    setRows((prevRows) => prevRows.filter((_, idx) => idx !== rowIndex));
  };

  // Count duplicates in active rows
  const duplicateCount = rows.filter((r) => r.isDuplicate).length;

  // Complete rows count
  const completeRows = rows.filter(isRowComplete);
  const completeCount = completeRows.length;
  const incompleteCount = rows.length - completeCount;

  // Handle continuing to Stage 3
  const handleConfirmAndStart = async () => {
    if (!brand || completeCount === 0 || isSubmitting) return;
    setIsSubmitting(true);
    setErrorMsg('');

    try {
      // 1. Confirm products via POST /brands/{brand}/confirm
      const confirmPayload = {
        brand_name: brand.trim(),
        brand_code: ingestResult?.brand_code || null,
        category: category,
        domain: ingestResult?.domain || '',
        platform: ingestResult?.platform || 'shopify',
        column_mapping: ingestResult?.column_mapping || {},
        qualifier_tokens: ingestResult?.qualifier_tokens || [],
        dp_column_explanation: ingestResult?.dp_column_explanation || 'User confirmed price sheet rows',
        rows: completeRows.map((r) => ({
          model_name: r.model_name.trim(),
          display_name: r.display_name.trim(),
          dp: parseFloat(r.dp),
          mrp: parseFloat(r.mrp),
          raw_text: r.raw_text,
          notes: r.notes,
        })),
      };

      const confirmRes = await fetch(`/brands/${encodeURIComponent(brand.trim())}/confirm`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(confirmPayload),
      });

      const confirmData = await confirmRes.json();

      if (confirmRes.status === 409) {
        throw new Error(confirmData.detail || `Brand '${brand}' is currently locked by an active job.`);
      }

      if (!confirmRes.ok) {
        throw new Error(confirmData.detail || 'Failed to confirm brand products.');
      }

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
        rowCount: completeRows.length,
      });
    } catch (err) {
      console.error('Failed to confirm and start collection:', err);
      setIsSubmitting(false);
      setErrorMsg(err.message || 'An error occurred during confirmation.');
    }
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
          {incompleteCount > 0 && (
            <div className="stage-2-stat" style={{ borderLeft: '1px solid var(--amber-dark)', paddingLeft: '14px' }}>
              <span className="stage-2-stat__number" style={{ color: 'var(--red)' }}>{incompleteCount}</span>
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
              ? `${incompleteCount} ${incompleteCount === 1 ? 'row is' : 'rows are'} missing price or name. Fill them in inline or delete them.`
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
                const complete = isRowComplete(row);
                const missingModel = !row.model_name || !row.model_name.trim();
                const missingDp = row.dp === '' || row.dp === null || isNaN(Number(row.dp)) || Number(row.dp) <= 0;
                const missingMrp = row.mrp === '' || row.mrp === null || isNaN(Number(row.mrp)) || Number(row.mrp) <= 0;

                return (
                  <tr
                    key={row.id}
                    className={`parsed-row ${row.isDuplicate ? 'parsed-row--duplicate' : ''}`}
                    style={!complete ? { backgroundColor: 'var(--cream)' } : {}}
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
                          {!complete && !row.isDuplicate && (
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
                          />
                        </div>
                        {row.isDuplicate && (
                          <div className="dup-note">same as row {row.duplicateOf}</div>
                        )}
                        {!complete && (
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
                      <input
                        type="text"
                        className="cell-input"
                        value={row.display_name}
                        onChange={(e) => handleCellChange(idx, 'display_name', e.target.value)}
                      />
                    </td>

                    {/* DP column */}
                    <td>
                      <input
                        type="text"
                        className={`cell-input ${missingDp ? 'cell-input--amber' : ''}`}
                        placeholder={missingDp ? 'Enter DP' : ''}
                        value={row.dp}
                        onChange={(e) => handleCellChange(idx, 'dp', e.target.value)}
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
        <ErrorDisplay error={errorMsg} showIcon={true} />
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
            disabled={isSubmitting || completeCount === 0}
            onClick={handleConfirmAndStart}
          >
            {isSubmitting
              ? 'Starting...'
              : `Continue with ${completeCount} ${completeCount === 1 ? 'row' : 'rows'}`}
          </button>
        </div>
      </div>
    </div>
  );
}
