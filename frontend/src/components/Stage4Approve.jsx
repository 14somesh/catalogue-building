import React, { useState, useEffect, useRef } from 'react';
import { WarningIcon, RefreshIcon, CheckIcon } from './Icons';

// Format currency in Indian numbering (e.g. 1,331)
function formatCurrency(val) {
  if (val === null || val === undefined || isNaN(Number(val))) return '—';
  return Number(val).toLocaleString('en-IN');
}

// Format clean source domain from URL
function formatSourceDomain(url) {
  if (!url) return '';
  try {
    const parsed = new URL(url);
    return parsed.hostname.replace(/^www\./, '');
  } catch {
    return url;
  }
}

export function Stage4Approve({
  approvalTarget,
  onBack,
  onContinue
}) {
  const brand = approvalTarget?.brand || 'Portronics';
  const category = approvalTarget?.category || '';
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(true);
  const [globalError, setGlobalError] = useState('');
  
  // Track which card is being edited
  const [editingId, setEditingId] = useState(null);
  const [editDraft, setEditDraft] = useState({});
  const [editError, setEditError] = useState('');
  const [savingEdit, setSavingEdit] = useState(false);
  const editInitialValues = useRef({});
  const [titleFit, setTitleFit] = useState({
    percent: 0,
    textWidth: 0,
    containerWidth: 291,
    overflow: false,
    diff: 0
  });

  // Track image uploads and reruns
  const [uploadingImageId, setUploadingImageId] = useState(null);
  const [rerunningId, setRerunningId] = useState(null);
  const fileInputRef = useRef(null);
  const activeUploadProductId = useRef(null);

  // Expose test hook for setting data in tests
  useEffect(() => {
    window.__SET_STAGE_4_DATA = (mockRows) => {
      setRows(mockRows);
      setLoading(false);
    };
    return () => {
      delete window.__SET_STAGE_4_DATA;
    };
  }, []);

  // Fetch review rows on mount
  useEffect(() => {
    let unmounted = false;

    async function fetchReviewData() {
      try {
        setLoading(true);
        setGlobalError('');
        const reviewUrl = `/brands/${encodeURIComponent(brand)}/review${category ? `?category=${encodeURIComponent(category)}` : ''}`;
        const res = await fetch(reviewUrl);
        if (!res.ok) {
          const errData = await res.json().catch(() => ({}));
          throw new Error(errData.detail || `Failed to load review data (status ${res.status})`);
        }
        const data = await res.json();
        if (unmounted) return;

        // If approvalTarget passed specific foundRows, filter to those, else show non-pending
        let displayedRows = data;
        if (approvalTarget?.foundRows && approvalTarget.foundRows.length > 0) {
          const foundIds = new Set(approvalTarget.foundRows.map(f => f.product_id));
          const filtered = data.filter(r => foundIds.has(r.product_id));
          if (filtered.length > 0) {
            displayedRows = filtered;
          }
        }

        setRows(displayedRows);
      } catch (err) {
        if (!unmounted) {
          setGlobalError(err.message);
        }
      } finally {
        if (!unmounted) {
          setLoading(false);
        }
      }
    }

    fetchReviewData();
    return () => {
      unmounted = true;
    };
  }, [brand, category, approvalTarget]);

  // Approved count
  const approvedCount = rows.filter(r => r.status === 'Approved').length;

  // Determine if a row needs a look
  function isRowProblem(row) {
    if (row.status === 'Blocked') return true;
    if (row.image_status && row.image_status !== 'ok') return true;
    if (row.failure_reason && row.failure_reason.trim() !== '') return true;
    if (row.flags) {
      if (Array.isArray(row.flags) && row.flags.length > 0) return true;
      if (typeof row.flags === 'string' && row.flags.trim() !== '') return true;
    }
    return false;
  }

  // Handle Approve All
  async function handleApproveAll() {
    try {
      setGlobalError('');
      const approveUrl = `/brands/${encodeURIComponent(brand)}/approve${category ? `?category=${encodeURIComponent(category)}` : ''}`;
      const res = await fetch(approveUrl, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ category: category || undefined })
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || 'Failed to approve all products');
      }
      // Update local state: all ready rows become Approved
      setRows(prev => prev.map(r => r.status !== 'Skipped' ? { ...r, status: 'Approved' } : r));
    } catch (err) {
      setGlobalError(err.message);
    }
  }

  // Handle single row Approve
  async function handleApproveRow(productId) {
    try {
      setGlobalError('');
      const res = await fetch(`/products/${encodeURIComponent(productId)}/approve`, {
        method: 'POST'
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || 'Failed to approve product');
      }
      setRows(prev => prev.map(r => r.product_id === productId ? { ...r, status: 'Approved' } : r));
    } catch (err) {
      setGlobalError(err.message);
    }
  }

  // Handle single row Skip
  async function handleSkipRow(productId) {
    try {
      setGlobalError('');
      const res = await fetch(`/products/${encodeURIComponent(productId)}/skip`, {
        method: 'POST'
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || 'Failed to skip product');
      }
      setRows(prev => prev.map(r => r.product_id === productId ? { ...r, status: 'Skipped' } : r));
    } catch (err) {
      setGlobalError(err.message);
    }
  }

  // Handle Re-run single product
  async function handleRerunRow(productId) {
    try {
      setRerunningId(productId);
      setGlobalError('');
      const res = await fetch(`/products/${encodeURIComponent(productId)}/rerun`, {
        method: 'POST'
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || 'Failed to rerun product collection');
      }
      const data = await res.json();
      if (data.updated_row) {
        setRows(prev => prev.map(r => r.product_id === productId ? { ...r, ...data.updated_row } : r));
      } else {
        // Refetch review row
        const reviewUrl = `/brands/${encodeURIComponent(brand)}/review${category ? `?category=${encodeURIComponent(category)}` : ''}`;
        const revRes = await fetch(reviewUrl);
        if (revRes.ok) {
          const revData = await revRes.json();
          const updated = revData.find(x => x.product_id === productId);
          if (updated) {
            setRows(prev => prev.map(r => r.product_id === productId ? updated : r));
          }
        }
      }
    } catch (err) {
      setGlobalError(err.message);
    } finally {
      setRerunningId(null);
    }
  }

  // Image file replacement
  function triggerImageUpload(productId) {
    activeUploadProductId.current = productId;
    if (fileInputRef.current) {
      fileInputRef.current.value = '';
      fileInputRef.current.click();
    }
  }

  async function handleFileChange(e) {
    const file = e.target.files?.[0];
    const productId = activeUploadProductId.current;
    if (!file || !productId) return;

    try {
      setUploadingImageId(productId);
      setGlobalError('');
      const formData = new FormData();
      formData.append('file', file);

      const res = await fetch(`/products/${encodeURIComponent(productId)}/image`, {
        method: 'POST',
        body: formData
      });

      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || 'Image upload failed');
      }

      const resData = await res.json();
      // Update image url with timestamp cache-buster
      const newImageUrl = resData.image_url ? `${resData.image_url}?t=${Date.now()}` : resData.image_url;

      setRows(prev => prev.map(r => {
        if (r.product_id === productId) {
          return {
            ...r,
            image_url: newImageUrl,
            image_status: 'ok',
            is_overridden: { ...r.is_overridden, image: true }
          };
        }
        return r;
      }));
    } catch (err) {
      setGlobalError(err.message);
    } finally {
      setUploadingImageId(null);
      activeUploadProductId.current = null;
    }
  }

  // Revert image override
  async function handleRevertImage(productId) {
    try {
      setGlobalError('');
      const res = await fetch(`/products/${encodeURIComponent(productId)}/image`, {
        method: 'DELETE'
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || 'Failed to revert image');
      }
      // Refetch row
      const reviewUrl = `/brands/${encodeURIComponent(brand)}/review${category ? `?category=${encodeURIComponent(category)}` : ''}`;
      const revRes = await fetch(reviewUrl);
      if (revRes.ok) {
        const revData = await revRes.json();
        const updated = revData.find(x => x.product_id === productId);
        if (updated) {
          setRows(prev => prev.map(r => r.product_id === productId ? updated : r));
        }
      }
    } catch (err) {
      setGlobalError(err.message);
    }
  }

  // Live Title Width Measurement effect
  useEffect(() => {
    if (!editingId) return;
    const currentTitle = editDraft.title || '';
    if (!currentTitle.trim()) {
      setTitleFit({ percent: 0, textWidth: 0, containerWidth: 291, overflow: false, diff: 0 });
      return;
    }
    const timer = setTimeout(async () => {
      try {
        const res = await fetch('/products/validate-title', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ title: currentTitle })
        });
        if (res.ok) {
          const data = await res.json();
          setTitleFit(data);
        }
      } catch (err) {
        // keep previous on transient error
      }
    }, 100);
    return () => clearTimeout(timer);
  }, [editingId, editDraft.title]);

  // Open inline edit
  function startEditing(row) {
    setEditingId(row.product_id);
    const initial = {
      title: row.title ?? '',
      subtitle: row.subtitle ?? '',
      bullet_1: row.bullet_1 ?? '',
      bullet_2: row.bullet_2 ?? '',
      bullet_3: row.bullet_3 ?? '',
      bullet_4: row.bullet_4 ?? '',
      dp: row.dp !== null && row.dp !== undefined ? String(row.dp) : '',
      mrp: row.mrp !== null && row.mrp !== undefined ? String(row.mrp) : ''
    };
    editInitialValues.current = initial;
    setEditDraft({ ...initial });
    setEditError('');

    // Pre-fetch title fit for initial title
    if (initial.title) {
      fetch('/products/validate-title', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title: initial.title })
      })
        .then(r => r.json())
        .then(data => setTitleFit(data))
        .catch(() => {});
    } else {
      setTitleFit({ percent: 0, textWidth: 0, containerWidth: 291, overflow: false, diff: 0 });
    }
  }

  function cancelEditing() {
    setEditingId(null);
    setEditDraft({});
    setEditError('');
  }

  // Reset a specific field to collected value (send null)
  async function handleResetField(productId, fieldName) {
    try {
      setSavingEdit(true);
      setEditError('');
      const patchBody = { [fieldName]: null };
      const res = await fetch(`/products/${encodeURIComponent(productId)}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(patchBody)
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || 'Failed to reset field');
      }
      const updatedRow = await res.json();
      setRows(prev => prev.map(r => r.product_id === productId ? updatedRow : r));
      setEditDraft(prev => ({
        ...prev,
        [fieldName]: updatedRow[fieldName] || ''
      }));
      editInitialValues.current[fieldName] = updatedRow[fieldName] || '';
    } catch (err) {
      setEditError(err.message);
    } finally {
      setSavingEdit(false);
    }
  }

  // Save inline edits: only send fields that actually changed
  async function saveEditing(productId) {
    try {
      setSavingEdit(true);
      setEditError('');

      // Measured title overflow check
      if (titleFit.overflow) {
        setEditError(
          `Title will not fit the card: rendered width (${Math.round(titleFit.textWidth)}px) exceeds the available container width (${Math.round(titleFit.containerWidth)}px) by ${Math.round(titleFit.diff)}px. Please shorten the title to fit.`
        );
        setSavingEdit(false);
        return;
      }
      if (editDraft.subtitle.length > 80) {
        setEditError(`Subtitle exceeds 80-character limit (${editDraft.subtitle.length} characters given): '${editDraft.subtitle}'`);
        setSavingEdit(false);
        return;
      }
      for (let i = 1; i <= 4; i++) {
        const b = editDraft[`bullet_${i}`] || '';
        if (b.length > 60) {
          setEditError(`Bullet_${i} exceeds 60-character wrap limit (${b.length} characters given): '${b}'`);
          setSavingEdit(false);
          return;
        }
      }

      const initial = editInitialValues.current;
      const payload = {};

      if (editDraft.title !== initial.title) {
        payload.title = editDraft.title.trim() === '' ? null : editDraft.title;
      }
      if (editDraft.subtitle !== initial.subtitle) {
        payload.subtitle = editDraft.subtitle.trim() === '' ? null : editDraft.subtitle;
      }
      for (let i = 1; i <= 4; i++) {
        const key = `bullet_${i}`;
        if (editDraft[key] !== initial[key]) {
          payload[key] = editDraft[key].trim() === '' ? null : editDraft[key];
        }
      }
      if (editDraft.dp !== initial.dp) {
        payload.dp = editDraft.dp.trim() === '' ? null : parseFloat(editDraft.dp);
      }
      if (editDraft.mrp !== initial.mrp) {
        payload.mrp = editDraft.mrp.trim() === '' ? null : parseFloat(editDraft.mrp);
      }

      // If user did not touch anything, simply close edit mode
      if (Object.keys(payload).length === 0) {
        setEditingId(null);
        setEditDraft({});
        setSavingEdit(false);
        return;
      }

      const res = await fetch(`/products/${encodeURIComponent(productId)}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });

      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        // Surface the server's rejection message verbatim
        throw new Error(err.detail || `Server error (status ${res.status})`);
      }

      const updatedRow = await res.json();
      setRows(prev => prev.map(r => r.product_id === productId ? updatedRow : r));
      setEditingId(null);
      setEditDraft({});
    } catch (err) {
      setEditError(err.message);
    } finally {
      setSavingEdit(false);
    }
  }

  return (
    <div className="stage-4-container">
      {/* Hidden file input for image replacement */}
      <input
        type="file"
        ref={fileInputRef}
        onChange={handleFileChange}
        accept="image/png,image/jpeg,image/webp"
        style={{ display: 'none' }}
      />

      {/* Global Error Banner */}
      {globalError && (
        <div className="stage-error-banner">
          <WarningIcon width={18} height={18} color="var(--red)" />
          <span style={{ flex: 1 }}>{globalError}</span>
          <button
            className="modal-close-btn"
            onClick={() => setGlobalError('')}
            style={{ color: 'var(--red)' }}
          >
            ✕
          </button>
        </div>
      )}

      {/* Amber Summary Bar */}
      <div className="stage-4-bar">
        <div className="stage-4-bar__left">
          <span className="stage-4-bar__count">
            {rows.length} rows to check
          </span>
        </div>
        <div className="stage-4-bar__right">
          <span className="stage-4-bar__approved-text">
            {approvedCount} approved
          </span>
          <button
            className="btn-approve-all"
            onClick={handleApproveAll}
            disabled={rows.length === 0}
          >
            Approve all
          </button>
        </div>
      </div>

      {/* Product Cards List */}
      {loading ? (
        <div className="stage-loading">Loading review rows...</div>
      ) : rows.length === 0 ? (
        <div className="stage-empty">No products to review.</div>
      ) : (
        <div className="cards-list">
          {rows.map(row => {
            const isApproved = row.status === 'Approved';
            const isSkipped = row.status === 'Skipped';
            const hasWarningOrFailure = isRowProblem(row);
            const isUnresolvedProblem = hasWarningOrFailure && !isApproved;

            const isEditing = editingId === row.product_id;
            const bullets = [row.bullet_1, row.bullet_2, row.bullet_3, row.bullet_4].filter(
              b => b && typeof b === 'string' && b.trim() !== ''
            );

            // Plain-language reason if problem
            const problemReason = row.failure_reason || (
              Array.isArray(row.flags) ? row.flags.join('. ') : (row.flags || 'Needs a look')
            );

            return (
              <div
                key={row.product_id}
                className={`product-card ${isUnresolvedProblem ? 'product-card--problem' : 'product-card--clean'} ${isSkipped ? 'product-card--skipped' : ''}`}
              >
                {/* Left Column: Image Thumbnail + Replace Button */}
                <div className="product-card__media">
                  <div className="card-thumb-wrap">
                    {row.image_url ? (
                      <img
                        src={row.image_url}
                        alt={row.title || row.model_name}
                        className="card-thumb"
                        onError={(e) => {
                          e.target.style.display = 'none';
                          e.target.nextSibling.style.display = 'flex';
                        }}
                      />
                    ) : null}
                    <div
                      className="card-thumb-placeholder"
                      style={{ display: row.image_url ? 'none' : 'flex' }}
                    >
                      <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="var(--grey-400)" strokeWidth="1.5">
                        <rect x="5" y="2" width="14" height="20" rx="3" />
                        <line x1="10" y1="5" x2="14" y2="5" />
                      </svg>
                    </div>
                  </div>

                  <button
                    className={`btn-thumb-replace ${isUnresolvedProblem ? 'btn-thumb-replace--problem' : 'btn-thumb-replace--clean'}`}
                    onClick={() => triggerImageUpload(row.product_id)}
                    disabled={uploadingImageId === row.product_id}
                  >
                    {uploadingImageId === row.product_id ? 'Uploading...' : 'Replace'}
                  </button>

                  {row.is_overridden?.image && (
                    <button
                      className="btn-revert-image"
                      onClick={() => handleRevertImage(row.product_id)}
                      title="Revert to collected image"
                    >
                      ↺ Revert image
                    </button>
                  )}
                </div>

                {/* Right Column: Card Content / Inline Edit */}
                <div className="product-card__body">
                  {isEditing ? (
                    /* Inline Editing Mode */
                    <div className="card-edit-form">
                      {editError && (
                        <div className="card-edit-error">
                          <WarningIcon width={16} height={16} color="var(--red)" />
                          <span style={{ flex: 1 }}>{editError}</span>
                        </div>
                      )}

                      {/* Title with live proportional width fit meter */}
                      <div className="edit-field">
                        <div className="edit-field__header">
                          <label className="edit-label">Title</label>
                          <div className="edit-field__meta">
                            {row.is_overridden?.title && (
                              <button
                                type="button"
                                className="edit-reset-btn"
                                onClick={() => handleResetField(row.product_id, 'title')}
                              >
                                ↺ Reset
                              </button>
                            )}
                            <div className="title-fit-meter">
                              <div className="title-fit-bar-wrap" title={`Card width: 291px. Current: ${Math.round(titleFit.textWidth)}px`}>
                                <div
                                  className={`title-fit-bar ${titleFit.overflow ? 'title-fit-bar--overflow' : titleFit.percent > 90 ? 'title-fit-bar--warning' : ''}`}
                                  style={{ width: `${Math.min(100, titleFit.percent || 0)}%` }}
                                />
                              </div>
                              <span
                                className={`title-fit-text ${titleFit.overflow ? 'title-fit-text--overflow' : ''}`}
                              >
                                {titleFit.overflow
                                  ? `${titleFit.percent}% (exceeds by ${Math.round(titleFit.diff)}px)`
                                  : `${titleFit.percent}% width`}
                              </span>
                            </div>
                          </div>
                        </div>
                        <input
                          type="text"
                          className={`edit-input ${titleFit.overflow ? 'edit-input--error' : ''}`}
                          value={editDraft.title}
                          onChange={(e) => setEditDraft({ ...editDraft, title: e.target.value })}
                          placeholder="Title"
                        />
                      </div>

                      {/* Subtitle with live 80-char count */}
                      <div className="edit-field">
                        <div className="edit-field__header">
                          <label className="edit-label">Subtitle</label>
                          <div className="edit-field__meta">
                            {row.is_overridden?.subtitle && (
                              <button
                                type="button"
                                className="edit-reset-btn"
                                onClick={() => handleResetField(row.product_id, 'subtitle')}
                              >
                                ↺ Reset
                              </button>
                            )}
                            <span
                              className={`char-counter ${editDraft.subtitle.length > 80 ? 'char-counter--over' : ''}`}
                            >
                              {editDraft.subtitle.length} / 80
                            </span>
                          </div>
                        </div>
                        <input
                          type="text"
                          className={`edit-input ${editDraft.subtitle.length > 80 ? 'edit-input--error' : ''}`}
                          value={editDraft.subtitle}
                          onChange={(e) => setEditDraft({ ...editDraft, subtitle: e.target.value })}
                          placeholder="Subtitle"
                        />
                      </div>

                      {/* Bullets 1 to 4 with live 60-char count */}
                      <div className="edit-bullets-grid">
                        {[1, 2, 3, 4].map(idx => {
                          const key = `bullet_${idx}`;
                          const val = editDraft[key] || '';
                          const isOver = val.length > 60;
                          return (
                            <div key={key} className="edit-field">
                              <div className="edit-field__header">
                                <label className="edit-label">Bullet {idx}</label>
                                <div className="edit-field__meta">
                                  {row.is_overridden?.[key] && (
                                    <button
                                      type="button"
                                      className="edit-reset-btn"
                                      onClick={() => handleResetField(row.product_id, key)}
                                    >
                                      ↺ Reset
                                    </button>
                                  )}
                                  <span className={`char-counter ${isOver ? 'char-counter--over' : ''}`}>
                                    {val.length} / 60
                                  </span>
                                </div>
                              </div>
                              <input
                                type="text"
                                className={`edit-input ${isOver ? 'edit-input--error' : ''}`}
                                value={val}
                                onChange={(e) => setEditDraft({ ...editDraft, [key]: e.target.value })}
                                placeholder={`Bullet ${idx}`}
                              />
                            </div>
                          );
                        })}
                      </div>

                      {/* DP and MRP */}
                      <div className="edit-prices-row">
                        <div className="edit-field" style={{ flex: 1 }}>
                          <div className="edit-field__header">
                            <label className="edit-label">DP</label>
                            {row.is_overridden?.dp && (
                              <button
                                type="button"
                                className="edit-reset-btn"
                                onClick={() => handleResetField(row.product_id, 'dp')}
                              >
                                ↺ Reset
                              </button>
                            )}
                          </div>
                          <input
                            type="number"
                            className="edit-input"
                            value={editDraft.dp}
                            onChange={(e) => setEditDraft({ ...editDraft, dp: e.target.value })}
                            placeholder="Dealer price"
                          />
                        </div>

                        <div className="edit-field" style={{ flex: 1 }}>
                          <div className="edit-field__header">
                            <label className="edit-label">MRP</label>
                            {row.is_overridden?.mrp && (
                              <button
                                type="button"
                                className="edit-reset-btn"
                                onClick={() => handleResetField(row.product_id, 'mrp')}
                              >
                                ↺ Reset
                              </button>
                            )}
                          </div>
                          <input
                            type="number"
                            className="edit-input"
                            value={editDraft.mrp}
                            onChange={(e) => setEditDraft({ ...editDraft, mrp: e.target.value })}
                            placeholder="Maximum retail price"
                          />
                        </div>
                      </div>

                      {/* Edit Actions */}
                      <div className="card-edit-actions">
                        <button
                          type="button"
                          className="btn-card-cancel"
                          onClick={cancelEditing}
                          disabled={savingEdit}
                        >
                          Cancel
                        </button>
                        <button
                          type="button"
                          className="btn-primary"
                          onClick={() => saveEditing(row.product_id)}
                          disabled={savingEdit || titleFit.overflow}
                        >
                          {savingEdit ? 'Saving...' : 'Save changes'}
                        </button>
                      </div>
                    </div>
                  ) : (
                    /* Normal View Mode */
                    <>
                      {/* Top row: Title + Status Badge */}
                      <div className="product-card__header">
                        <h3 className="product-card__title">
                          {row.title || row.model_name}
                        </h3>

                        {isApproved ? (
                          <span className="status-badge status-badge--approved">
                            Approved
                          </span>
                        ) : hasWarningOrFailure ? (
                          <span className="status-badge status-badge--problem">
                            Needs a look
                          </span>
                        ) : isSkipped ? (
                          <span className="status-badge status-badge--skipped">
                            Skipped
                          </span>
                        ) : (
                          <span className="status-badge status-badge--ready">
                            Ready
                          </span>
                        )}
                      </div>

                      {/* Subtitle */}
                      {row.subtitle && (
                        <div className="product-card__subtitle">
                          {row.subtitle}
                        </div>
                      )}

                      {/* Four Bullets (Do NOT show specs!) */}
                      {bullets.length > 0 && (
                        <div className="product-card__bullets">
                          {bullets.map((b, i) => (
                            <div key={i} className="bullet-line">
                              {b}
                            </div>
                          ))}
                        </div>
                      )}

                      {/* Problem / Advisory Warning Callout Box */}
                      {hasWarningOrFailure && (
                        <div className="problem-callout">
                          <WarningIcon width={16} height={16} color="var(--amber-dark)" />
                          <span className="problem-callout__text">
                            {problemReason}
                          </span>
                        </div>
                      )}

                      {/* Meta line and Actions */}
                      <div className="product-card__footer">
                        <div className="product-card__meta">
                          <span>
                            DP {formatCurrency(row.dp)} | MRP {formatCurrency(row.mrp)}
                          </span>
                          {row.source_url && (
                            <>
                              <span className="meta-dot">·</span>
                              <span className="meta-source">
                                {formatSourceDomain(row.source_url)}
                              </span>
                            </>
                          )}
                        </div>

                        <div className="product-card__actions">
                          {/* 1. Edit */}
                          <button
                            className="btn-card-action"
                            onClick={() => startEditing(row)}
                          >
                            Edit
                          </button>

                          {/* 2. Re-run (problem rows only) */}
                          {hasWarningOrFailure && (
                            <button
                              className="btn-card-action"
                              onClick={() => handleRerunRow(row.product_id)}
                              disabled={rerunningId === row.product_id}
                            >
                              {rerunningId === row.product_id ? (
                                <RefreshIcon className="spin" width={14} height={14} />
                              ) : null}
                              <span>Re-run</span>
                            </button>
                          )}

                          {/* 3. Skip (appears on every card regardless of status) */}
                          <button
                            className="btn-card-action"
                            onClick={() => handleSkipRow(row.product_id)}
                          >
                            Skip
                          </button>

                          {/* 4. Approve (on rows not yet approved) */}
                          {!isApproved && (
                            <button
                              className="btn-card-action btn-card-action--approve"
                              onClick={() => handleApproveRow(row.product_id)}
                            >
                              Approve
                            </button>
                          )}
                        </div>
                      </div>
                    </>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* Stage Footer */}
      <div className="stage-footer">
        <button className="btn-secondary" onClick={onBack}>
          ← Back
        </button>
        <div className="stage-footer__right">
          <button
            className="btn-primary"
            onClick={() => onContinue && onContinue({ brand, category, approvedCount, autoTrigger: true })}
            disabled={approvedCount === 0}
          >
            Build {approvedCount} {approvedCount === 1 ? 'product' : 'products'}
          </button>
        </div>
      </div>
    </div>
  );
}
