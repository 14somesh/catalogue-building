import React, { useState, useEffect } from 'react';
import { TopNav } from './components/TopNav';
import { LandingPage } from './pages/LandingPage';
import { BuildFlowPage } from './pages/BuildFlowPage';
import { HowToUsePage } from './pages/HowToUsePage';

export default function App() {
  const [route, setRoute] = useState(() => {
    const path = window.location.pathname;
    if (path.startsWith('/build')) return '/build';
    if (path.startsWith('/how-to-use')) return '/how-to-use';
    return '/';
  });

  useEffect(() => {
    const handlePopState = () => {
      const path = window.location.pathname;
      if (path.startsWith('/build')) setRoute('/build');
      else if (path.startsWith('/how-to-use')) setRoute('/how-to-use');
      else setRoute('/');
    };

    window.addEventListener('popstate', handlePopState);
    return () => window.removeEventListener('popstate', handlePopState);
  }, []);

  const navigate = (newRoute) => {
    if (newRoute !== route) {
      window.history.pushState({}, '', newRoute);
      setRoute(newRoute);
    }
  };

  return (
    <div className="app-shell">
      {/* Persistent Full-Width Top Nav */}
      <TopNav currentRoute={route} onNavigate={navigate} />

      {/* Screen Routing */}
      {route === '/' && (
        <LandingPage onStartBuilding={() => navigate('/build')} />
      )}

      {route === '/build' && (
        <BuildFlowPage />
      )}

      {route === '/how-to-use' && (
        <HowToUsePage />
      )}
    </div>
  );
}
