import React, { useState } from 'react';
import { WarningTriangleIcon } from './Icons';

/**
 * Translates raw backend / provider error payloads into user-friendly messages.
 * Never displays raw provider JSON or Python dicts to the end user.
 */
export function translateErrorMessage(rawError) {
  if (!rawError) return '';
  const str = typeof rawError === 'string' ? rawError : JSON.stringify(rawError);
  const lower = str.toLowerCase();

  // 1. 503 / Unavailable / High demand / Overloaded
  if (
    lower.includes('503') ||
    lower.includes('unavailable') ||
    lower.includes('high demand') ||
    lower.includes('overloaded') ||
    lower.includes('spikes in demand') ||
    lower.includes('busy right now')
  ) {
    return 'The AI service is unavailable. Try again in a few minutes.';
  }

  // 2. 429 / Quota / Rate limit
  if (
    lower.includes('429') ||
    lower.includes('quota') ||
    lower.includes('resource_exhausted') ||
    lower.includes('rate limit') ||
    lower.includes('ratelimit')
  ) {
    return 'The AI service request quota was reached. Please try again later.';
  }

  // 3. Timeout / Network
  if (
    lower.includes('timeout') ||
    lower.includes('timed out') ||
    lower.includes('deadlineexceeded') ||
    lower.includes('connectionerror') ||
    lower.includes('connection reset')
  ) {
    return 'The request timed out while contacting the AI service. Please try again.';
  }

  // 4. Raw Python dict or JSON payload
  if (
    str.includes("{'error'") ||
    str.includes('{"error"') ||
    str.startsWith('{')
  ) {
    return 'The AI service encountered an error processing this file. Please try again.';
  }

  // Clean "Job failed: " prefix if present
  if (str.startsWith('Job failed: ')) {
    return str.replace(/^Job failed:\s*/, '');
  }

  return str;
}

/**
 * Clean ErrorDisplay component:
 * - Single, friendly error message in red (#A32D2D).
 * - Collapsible "Details ▾" toggle revealing raw technical details on demand.
 */
export function ErrorDisplay({ error, technicalDetails, showIcon = false, className = '' }) {
  const [isOpen, setIsOpen] = useState(false);

  if (!error) return null;

  const friendlyMessage = translateErrorMessage(error);
  // Extract raw details if available, or if the original error was a raw stack/dict
  const rawDetails =
    technicalDetails ||
    (typeof error === 'string' && (error.includes('{') || error.length > 90) ? error : null);

  return (
    <div className={`error-display ${className}`}>
      <div className="error-display__main">
        {showIcon && <WarningTriangleIcon width={16} height={16} color="#A32D2D" />}
        <span className="error-display__text">{friendlyMessage}</span>
        {rawDetails && (
          <button
            type="button"
            className="error-display__toggle"
            onClick={(e) => {
              e.stopPropagation();
              setIsOpen((prev) => !prev);
            }}
            title="Toggle technical details"
          >
            {isOpen ? 'Details ▴' : 'Details ▾'}
          </button>
        )}
      </div>

      {isOpen && rawDetails && (
        <div className="error-display__details">
          <pre>{typeof rawDetails === 'object' ? JSON.stringify(rawDetails, null, 2) : rawDetails}</pre>
        </div>
      )}
    </div>
  );
}
