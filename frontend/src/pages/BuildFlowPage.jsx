import React, { useState } from 'react';
import { StageStepper } from '../components/StageStepper';
import { Stage1Ingest } from '../components/Stage1Ingest';
import { Stage2Brochure } from '../components/Stage2Brochure';
import { Stage3Collect } from '../components/Stage3Collect';
import { Stage4Approve } from '../components/Stage4Approve';
import { Stage5Build } from '../components/Stage5Build';

export function BuildFlowPage() {
  const [currentStage, setCurrentStage] = useState(1);
  const [ingestResult, setIngestResult] = useState(null);
  const [collectionTarget, setCollectionTarget] = useState(null);
  const [approvalTarget, setApprovalTarget] = useState(null);
  const [buildTarget, setBuildTarget] = useState(null);

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

  const handleApproveContinue = (target) => {
    setBuildTarget(target);
    setCurrentStage(5);
  };

  const handleStartNewBrand = () => {
    setIngestResult(null);
    setCollectionTarget(null);
    setApprovalTarget(null);
    setBuildTarget(null);
    setCurrentStage(1);
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
    window.__SET_STAGE_4 = (target) => {
      setApprovalTarget(target);
      setCurrentStage(4);
    };
    window.__SET_STAGE_5 = (target) => {
      setBuildTarget(target);
      setCurrentStage(5);
    };
    return () => {
      delete window.__SET_STAGE_2;
      delete window.__SET_STAGE_3;
      delete window.__SET_STAGE_4;
      delete window.__SET_STAGE_5;
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

        {/* Stage 4 — Approve */}
        {currentStage === 4 && (
          <Stage4Approve
            approvalTarget={approvalTarget}
            onBack={() => setCurrentStage(3)}
            onContinue={handleApproveContinue}
          />
        )}

        {/* Stage 5 — Build */}
        {currentStage === 5 && (
          <Stage5Build
            buildTarget={buildTarget}
            onBack={() => setCurrentStage(4)}
            onStartNewBrand={handleStartNewBrand}
          />
        )}
      </div>
    </div>
  );
}
