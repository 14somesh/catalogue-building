import React, { useState } from 'react';
import { StageStepper } from '../components/StageStepper';
import { Stage1Ingest } from '../components/Stage1Ingest';
import { Stage2Brochure } from '../components/Stage2Brochure';
import { Stage3Collect } from '../components/Stage3Collect';

export function BuildFlowPage() {
  const [currentStage, setCurrentStage] = useState(1);
  const [ingestResult, setIngestResult] = useState(null);
  const [collectionTarget, setCollectionTarget] = useState(null);
  const [approvalTarget, setApprovalTarget] = useState(null);

  const handleIngestComplete = (result) => {
    setIngestResult(result);
    setCurrentStage(2);
  };

  const handleStartCollecting = (target) => {
    setCollectionTarget(target);
    setCurrentStage(3);
  };

  const handleCollectionContinue = (target) => {
    setApprovalTarget(target);
    setCurrentStage(4);
  };

  React.useEffect(() => {
    window.__SET_STAGE_2 = (result) => {
      setIngestResult(result);
      setCurrentStage(2);
    };
    window.__SET_STAGE_3 = (target) => {
      setCollectionTarget(target);
      setCurrentStage(3);
    };
    return () => {
      delete window.__SET_STAGE_2;
      delete window.__SET_STAGE_3;
    };
  }, []);

  return (
    <div className="flow-page">
      <div className="content-container">
        {/* 5-Item Stepper */}
        <StageStepper
          currentStage={currentStage}
          onSelectStage={(stageId) => {
            // Allow navigating backwards to completed stages
            if (stageId <= currentStage) {
              setCurrentStage(stageId);
            }
          }}
        />

        {/* Stage 1 — Ingest */}
        {currentStage === 1 && (
          <Stage1Ingest onIngestComplete={handleIngestComplete} />
        )}

        {/* Stage 2 — Brochure & Parsed Rows */}
        {currentStage === 2 && (
          <Stage2Brochure
            ingestResult={ingestResult}
            onBack={() => setCurrentStage(1)}
            onStartCollecting={handleStartCollecting}
          />
        )}

        {/* Stage 3 — Collect */}
        {currentStage === 3 && (
          <Stage3Collect
            collectionTarget={collectionTarget}
            onBack={() => setCurrentStage(2)}
            onContinue={handleCollectionContinue}
          />
        )}

        {/* Stage 4 Placeholder for Step 4 */}
        {currentStage === 4 && (
          <div className="stage-panel">
            <div style={{ fontSize: '17px', fontWeight: 500, marginBottom: '8px' }}>
              Stage 4 — Approve
            </div>
            <p style={{ color: 'var(--amber-mid)', fontSize: '15px' }}>
              Ready to implement in Step 4: Approval cards and inline editing.
            </p>
          </div>
        )}

        {/* Stage 5 Placeholder for Step 5 */}
        {currentStage === 5 && (
          <div className="stage-panel">
            <div style={{ fontSize: '17px', fontWeight: 500, marginBottom: '8px' }}>
              Stage 5 — Build
            </div>
            <p style={{ color: 'var(--amber-mid)', fontSize: '15px' }}>
              Ready to implement in Step 5: PDF download and build history.
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
