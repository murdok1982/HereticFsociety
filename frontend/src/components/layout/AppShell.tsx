import { useEffect, useState } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import { api, API_LINKS } from "../../api/client";
import { useApiQuery } from "../../hooks/useApiQuery";
import { toneForHealth } from "../../lib/status";
import { Drawer } from "../ui/Drawer";
import { Icon } from "../ui/Icon";
import { findNavItem, NAV_ITEMS, type NavItem } from "./navigation";

function navLinkClass({ isActive }: { isActive: boolean }): string {
  return ["nav-link", isActive ? "nav-link--active" : ""].filter(Boolean).join(" ");
}

function NavList({
  items,
  onNavigate,
}: {
  items: NavItem[];
  onNavigate?: () => void;
}) {
  return (
    <ul className="nav-list">
      {items.map((item) => (
        <li key={item.to}>
          <NavLink
            to={item.to}
            end={item.to === "/"}
            className={navLinkClass}
            onClick={onNavigate}
            title={item.description}
          >
            <Icon name={item.icon} size={18} />
            <span className="nav-label">{item.label}</span>
          </NavLink>
        </li>
      ))}
    </ul>
  );
}

function Brand() {
  return (
    <div className="brand">
      <span className="brand-mark" aria-hidden="true">
        F
      </span>
      <span className="brand-text">
        <span className="brand-name">FSOCIETY AI</span>
        <span className="brand-tagline">Model Engineering Platform</span>
      </span>
    </div>
  );
}

export function AppShell() {
  const location = useLocation();
  const [navOpen, setNavOpen] = useState(false);
  const health = useApiQuery((signal) => api.getHealth(signal), [], {
    pollMs: 30_000,
  });

  useEffect(() => {
    window.scrollTo(0, 0);
  }, [location.pathname]);

  useEffect(() => {
    setNavOpen(false);
  }, [location.pathname]);

  const current = findNavItem(location.pathname);
  const healthTone = health.error
    ? "danger"
    : health.data
      ? toneForHealth(health.data.status)
      : "neutral";
  const healthLabel = health.error
    ? "API unreachable"
    : health.data
      ? health.data.status === "ok"
        ? "API online"
        : "API degraded"
      : "Connecting…";
  const healthDetail = health.error
    ? health.error.code
    : health.data
      ? `v${health.data.version}`
      : undefined;

  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">
        Skip to main content
      </a>

      <aside className="sidebar" aria-label="Sidebar">
        <Brand />
        <nav className="nav" aria-label="Primary navigation">
          <p className="nav-section-label">Workspace</p>
          <NavList items={NAV_ITEMS} />
        </nav>
        <div className="sidebar-footer">
          <button
            type="button"
            className="api-status-button"
            onClick={health.reload}
            title={
              health.error
                ? `${health.error.message} Click to retry.`
                : "Refresh API status"
            }
          >
            <span className={`dot dot--${healthTone}`} aria-hidden="true" />
            <span className="api-status-copy">
              <span className="api-status-label">{healthLabel}</span>
              {healthDetail ? (
                <span className="api-status-detail mono">{healthDetail}</span>
              ) : null}
            </span>
          </button>
          <a
            className="sidebar-link"
            href={API_LINKS.openapi}
            target="_blank"
            rel="noreferrer"
          >
            <span>API reference</span>
            <Icon name="external" size={14} />
          </a>
        </div>
      </aside>

      <div className="app-main">
        <header className="topbar">
          <button
            type="button"
            className="icon-button topbar-menu"
            onClick={() => setNavOpen(true)}
            aria-label="Open navigation"
            aria-expanded={navOpen}
            aria-haspopup="dialog"
          >
            <Icon name="menu" size={20} />
          </button>
          <div className="topbar-context">
            <span className="topbar-section">{current?.label ?? "Console"}</span>
            <span className="topbar-description">
              {current?.description ?? "FSOCIETY AI operator console"}
            </span>
          </div>
          <div className="topbar-status" title={healthLabel}>
            <span className={`dot dot--${healthTone}`} aria-hidden="true" />
            <span className="sr-only">{healthLabel}</span>
            {healthDetail ? (
              <span className="mono topbar-version">{healthDetail}</span>
            ) : null}
          </div>
        </header>

        <main id="main-content" className="page" tabIndex={-1}>
          <Outlet />
        </main>
      </div>

      <Drawer
        open={navOpen}
        onClose={() => setNavOpen(false)}
        title="Navigation"
        subtitle="All operator console sections"
      >
        <nav aria-label="Mobile navigation">
          <NavList items={NAV_ITEMS} onNavigate={() => setNavOpen(false)} />
        </nav>
        <div className="drawer-api-status">
          <span className={`dot dot--${healthTone}`} aria-hidden="true" />
          <span>{healthLabel}</span>
          {healthDetail ? <span className="mono muted">{healthDetail}</span> : null}
        </div>
      </Drawer>
    </div>
  );
}
