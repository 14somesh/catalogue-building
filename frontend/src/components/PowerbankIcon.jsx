import React from 'react';

/**
 * Powerbank icon glyph matching screenshots and UI_SPEC.md.
 * Rounded rectangle with top notch/slot and indicator.
 */
export function PowerbankIcon({
  width = 16,
  height = 20,
  fill = 'currentColor',
  slotFill = '#412402',
  dotFill = '#E1F5EE',
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
      <rect x="2" y="5" width="20" height="25" rx="4" fill={fill} />
      {/* Top connector notch / slot */}
      <rect x="7" y="2" width="10" height="4" rx="1.5" fill={slotFill} />
      {/* Single subtle charge indicator dot */}
      {showDot && (
        <circle cx="12" cy="12" r="1.5" fill={dotFill} />
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
    <div className="landing-powerbank-trio">
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
        dotFill="#E1F5EE"
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
