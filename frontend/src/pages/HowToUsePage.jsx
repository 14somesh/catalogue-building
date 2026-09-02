import React from 'react';

const STEPS = [
  {
    number: 1,
    title: 'Ingest price sheet',
    desc: 'Drop in a screenshot, PDF, Excel, CSV or pasted text from the brand.',
    isManual: true, // amber
  },
  {
    number: 2,
    title: 'Review parsed rows and attach brochure',
    desc: 'Verify the detected brand, edit any row in place, and optionally provide a PDF brochure.',
    isManual: true, // amber
  },
  {
    number: 3,
    title: 'Autonomous collection',
    desc: 'The pipeline extracts specifications, sources high-resolution images, and runs deterministic checks.',
    isManual: false, // grey automatic
  },
  {
    number: 4,
    title: 'Approve and edit cards',
    desc: 'Review cards as they will print, refine titles, copy or prices, upload custom images, and approve.',
    isManual: true, // amber
  },
  {
    number: 5,
    title: 'Build catalogue',
    desc: 'Compile a print-ready A4 PDF for a single brand or the entire multi-brand catalogue.',
    isManual: false, // grey automatic
  },
];

export function HowToUsePage() {
  return (
    <div className="how-to-use-page">
      <div className="content-container">
        <div className="how-to-use-card">
          <h1 className="how-to-use-header">How to use</h1>
          <p className="how-to-use-intro">
            From a raw dealer price sheet to a print-ready product catalogue in five simple stages.
          </p>

          <div className="steps-list">
            {STEPS.map((step) => (
              <div key={step.number} className="step-item">
                <div
                  className={`step-circle ${
                    step.isManual ? 'step-circle--active' : 'step-circle--auto'
                  }`}
                >
                  {step.number}
                </div>
                <div className="step-content">
                  <div className="step-title">{step.title}</div>
                  <div className="step-desc">{step.desc}</div>
                </div>
              </div>
            ))}
          </div>

          <div className="callout-box">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#BA7517" strokeWidth="2">
              <circle cx="12" cy="12" r="10" />
              <line x1="12" y1="16" x2="12" y2="12" />
              <line x1="12" y1="8" x2="12.01" y2="8" />
            </svg>
            <span>One brand at a time. If someone else is running a brand, you'll be told who.</span>
          </div>
        </div>
      </div>
    </div>
  );
}
