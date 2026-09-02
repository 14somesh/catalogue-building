import React from 'react';

const STAGES = [
  { id: 1, name: 'Ingest' },
  { id: 2, name: 'Brochure' },
  { id: 3, name: 'Collect' },
  { id: 4, name: 'Approve' },
  { id: 5, name: 'Build' },
];

export function StageStepper({ currentStage = 1, onSelectStage }) {
  return (
    <div className="stepper-bar">
      {STAGES.map((stage, index) => {
        const isCurrent = stage.id === currentStage;
        const isCompleted = stage.id < currentStage;
        const isFuture = stage.id > currentStage;

        return (
          <React.Fragment key={stage.id}>
            <div
              className={`stepper-item ${
                isCurrent
                  ? 'stepper-item--current'
                  : isCompleted
                  ? 'stepper-item--completed'
                  : 'stepper-item--future'
              }`}
              onClick={() => {
                if (isCompleted && onSelectStage) {
                  onSelectStage(stage.id);
                }
              }}
              style={{ cursor: isCompleted ? 'pointer' : 'default' }}
            >
              {isCompleted ? (
                <>
                  <svg
                    width="13"
                    height="13"
                    viewBox="0 0 16 16"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="2.5"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    style={{ marginRight: 2 }}
                  >
                    <polyline points="3 8.5 6.5 12 13 4" />
                  </svg>
                  <span>{stage.name}</span>
                </>
              ) : isCurrent ? (
                <span>{stage.id} {stage.name}</span>
              ) : (
                <span>{stage.id} {stage.name}</span>
              )}
            </div>

            {index < STAGES.length - 1 && (
              <span className="stepper-separator">›</span>
            )}
          </React.Fragment>
        );
      })}
    </div>
  );
}
