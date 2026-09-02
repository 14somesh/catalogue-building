import React from 'react';
import { PowerbankIcon } from './PowerbankIcon';

export function TopNav({ currentRoute, onNavigate }) {
  return (
    <header className="top-nav">
      <div className="top-nav__inner">
        <div
          className="top-nav__logo"
          onClick={() => onNavigate('/')}
          role="button"
          tabIndex={0}
          title="Return to landing page"
        >
          <PowerbankIcon width={16} height={22} fill="#412402" slotFill="#EF9F27" />
          <span>Catalog builder</span>
        </div>

        <nav className="top-nav__links">
          <a
            className={`top-nav__link ${currentRoute === '/build' ? 'top-nav__link--active' : ''}`}
            onClick={(e) => {
              e.preventDefault();
              onNavigate('/build');
            }}
            href="#/build"
          >
            Build catalogue
          </a>
          <a
            className={`top-nav__link ${currentRoute === '/how-to-use' ? 'top-nav__link--active' : ''}`}
            onClick={(e) => {
              e.preventDefault();
              onNavigate('/how-to-use');
            }}
            href="#/how-to-use"
          >
            How to use
          </a>
        </nav>
      </div>
    </header>
  );
}
