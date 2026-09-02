import React, { useState } from 'react';
import { StageStepper } from '../components/StageStepper';

export function BuildFlowPage() {
  const [currentStage, setCurrentStage] = useState(1);

  return (
    <div className="flow-container">
      {/* 5-Item Stepper */}
      <StageStepper
        currentStage={currentStage}
        onSelectStage={(stageId) => setCurrentStage(stageId)}
      />

      {/* Stage Container Placeholder for Step 1 */}
      <div className="stage-panel">
        <div style={{ fontSize: '15px', fontWeight: 500, marginBottom: '8px' }}>
          Stage {currentStage}
        </div>
        <p style={{ color: 'var(--amber-mid)', fontSize: '14px' }}>
          Ready to implement in Step 2: Stage 1 and 2 (Upload, Ingest, Parsed Rows, Brochure).
        </p>
      </div>
    </div>
  );
}
