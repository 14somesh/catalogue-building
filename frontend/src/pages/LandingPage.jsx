import React, { useEffect, useState } from 'react';
import { LandingPowerbankTrio } from '../components/PowerbankIcon';

export function LandingPage({ onStartBuilding }) {
  const [stats, setStats] = useState({
    brandsLive: 0,
    products: 0,
    pagesBuilt: 0,
    loading: true,
  });

  useEffect(() => {
    let isMounted = true;

    async function fetchStats() {
      try {
        const [brandsRes, buildsRes] = await Promise.all([
          fetch('/brands'),
          fetch('/builds'),
        ]);

        let brandsData = [];
        let buildsData = [];

        if (brandsRes.ok) {
          brandsData = await brandsRes.json();
        }
        if (buildsRes.ok) {
          buildsData = await buildsRes.json();
        }

        if (isMounted) {
          const brandsLive = new Set(brandsData.map((b) => b.brand)).size;
          // Count approved products across brands
          const approvedProducts = brandsData.reduce(
            (sum, b) => sum + (b.status_counts?.Approved || 0),
            0
          );
          const totalProducts = brandsData.reduce(
            (sum, b) => sum + (b.total_rows || 0),
            0
          );
          const displayProducts = approvedProducts > 0 ? approvedProducts : totalProducts;

          // Latest compiled catalogue pages
          const latestBuild = buildsData.find((b) => b.brand === 'Combined') || buildsData[0];
          const pagesBuilt = latestBuild?.page_count || 0;

          setStats({
            brandsLive,
            products: displayProducts,
            pagesBuilt,
            loading: false,
          });
        }
      } catch (err) {
        console.error('Failed to load landing stats:', err);
        if (isMounted) {
          setStats((prev) => ({ ...prev, loading: false }));
        }
      }
    }

    fetchStats();
    return () => {
      isMounted = false;
    };
  }, []);

  return (
    <section className="landing-section">
      <div className="content-container">
        <main className="landing-content">
          {/* Centered Trio of Powerbank Glyphs */}
          <LandingPowerbankTrio />

          {/* Heading & Subline */}
          <h1 className="landing-heading">Welcome to catalogue builder</h1>
          <p className="landing-subline">
            Hand over a price sheet. Get back a print-ready catalogue.
          </p>

          {/* One Teal Action Button */}
          <button
            type="button"
            className="btn-primary"
            onClick={onStartBuilding}
          >
            Start building
          </button>

          {/* Three Cream Stat Tiles */}
          <div className="landing-stats">
            <div className="stat-tile">
              <div className="stat-tile__number">
                {stats.loading ? '—' : stats.brandsLive}
              </div>
              <div className="stat-tile__label">brands live</div>
            </div>

            <div className="stat-tile">
              <div className="stat-tile__number">
                {stats.loading ? '—' : stats.products}
              </div>
              <div className="stat-tile__label">products</div>
            </div>

            <div className="stat-tile">
              <div className="stat-tile__number">
                {stats.loading ? '—' : stats.pagesBuilt}
              </div>
              <div className="stat-tile__label">pages built</div>
            </div>
          </div>
        </main>
      </div>
    </section>
  );
}
