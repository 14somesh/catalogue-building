import React from 'react';

/**
 * Powerbank icon glyph matching screenshots and UI_SPEC.md.
 * Rounded rectangle with top notch/slot and indicator.
 */
export function PowerbankIcon({
  width = 16,
  height = 20,
  fill = 'currentColor',
  slotFill = 'rgba(0,0,0,0.2)',
  dotFill = 'rgba(0,0,0,0.25)',
  showDot = false,
  className = ''
}) {
  return (
    <svg
      width={width}
      height={height}
      viewBox="0 0 24 32"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className={className}
      style={{ display: 'inline-block', verticalAlign: 'middle', flexShrink: 0 }}
    >
      {/* Outer rounded powerbank body */}
      <rect x="0" y="0" width="24" height="32" rx="4" fill={fill} />
      {/* Top horizontal notch / connector port */}
      <rect x="4" y="3.5" width="10" height="2" rx="1" fill={slotFill} />
      {/* Optional bottom indicator */}
      {showDot && (
        <rect x="15" y="24" width="4" height="4" rx="1" fill={dotFill} />
      )}
    </svg>
  );
}

/**
 * Centered hero glyphs for landing page:
 * Left pale amber, middle tall teal, right pale cream.
 */
export function LandingPowerbankTrio() {
  return (
    <div className="landing-glyphs">
      {/* Left powerbank: smaller, pale amber */}
      <PowerbankIcon
        width={48}
        height={64}
        fill="#FAC775"
        slotFill="#633806"
        showDot={false}
      />
      {/* Center powerbank: taller, teal */}
      <PowerbankIcon
        width={58}
        height={82}
        fill="#0F6E56"
        slotFill="#9FE1CB"
        dotFill="#073B2E"
        showDot={true}
      />
      {/* Right powerbank: smaller, cream */}
      <PowerbankIcon
        width={48}
        height={64}
        fill="#FAEEDA"
        slotFill="#BA7517"
        showDot={false}
      />
    </div>
  );
}
