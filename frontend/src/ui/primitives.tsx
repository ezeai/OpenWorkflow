import type { ReactNode } from "react";

// Shared presentation primitives. No data fetching here: pages pass real data in.

const ICONS: Record<string, ReactNode> = {
  home: <path d="M4 10.5 12 4l8 6.5V20a1 1 0 0 1-1 1h-4v-6H9v6H5a1 1 0 0 1-1-1z" />,
  discover: <><circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" /></>,
  create: <><rect x="3" y="3" width="18" height="18" rx="4" /><path d="M12 8v8M8 12h8" /></>,
  pipelines: <><rect x="3" y="4" width="7" height="6" rx="1.5" /><rect x="14" y="14" width="7" height="6" rx="1.5" /><path d="M6.5 10v3a2 2 0 0 0 2 2H14" /></>,
  projects: <path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />,
  library: <><path d="M12 3 20 7.5v9L12 21l-8-4.5v-9z" /><path d="M4 7.5 12 12l8-4.5M12 12v9" /></>,
  history: <><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></>,
  settings: <><path d="M4 6h10M18 6h2M4 12h4M12 12h8M4 18h12" /><circle cx="16" cy="6" r="2" /><circle cx="10" cy="12" r="2" /><circle cx="18" cy="18" r="2" /></>,
  character: <><circle cx="12" cy="8" r="4" /><path d="M4 21c0-4.4 3.6-7 8-7s8 2.6 8 7" /></>,
  world: <><circle cx="12" cy="12" r="9" /><path d="M3 12h18M12 3c2.5 2.7 3.8 5.7 3.8 9s-1.3 6.3-3.8 9c-2.5-2.7-3.8-5.7-3.8-9S9.5 5.7 12 3z" /></>,
  director: <><rect x="3" y="9" width="18" height="12" rx="2" /><path d="m3 9 17-4.5.8 3M8 7.7l2.2-3.4M13.5 6.2l2.2-3.4" /></>,
  canvas: <><rect x="3" y="3" width="18" height="18" rx="2" /><path d="m3 16 5-5 4 4 3-3 6 6" /><circle cx="15.5" cy="8.5" r="1.5" /></>,
  poster: <><path d="M4 4h16v16H4z" /><path d="M8 9h8M8 13h8M8 17h5" /></>,
  film: <><rect x="3" y="4" width="18" height="16" rx="2" /><path d="M7 4v16M17 4v16M3 9h4M3 15h4M17 9h4M17 15h4" /></>,
  play: <path d="M7 5v14l11-7z" />,
  arrow: <path d="M5 12h14M13 6l6 6-6 6" />,
  menu: <path d="M4 7h16M4 12h16M4 17h16" />,
  close: <path d="M6 6l12 12M18 6 6 18" />,
  check: <path d="m5 12 5 5 9-10" />,
  alert: <><path d="M12 3 2 20h20z" /><path d="M12 10v4M12 17v.5" /></>,
  upload: <><path d="M12 16V4M7 9l5-5 5 5" /><path d="M4 16v3a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-3" /></>,
  chip: <><rect x="6" y="6" width="12" height="12" rx="2" /><path d="M9 2v4M15 2v4M9 18v4M15 18v4M2 9h4M2 15h4M18 9h4M18 15h4" /></>,
};

export type IconName = keyof typeof ICONS;

export function Icon({ name, size = 18, className = "icon" }: { name: IconName; size?: number; className?: string }) {
  return (
    <svg className={className} width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.75}
      strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      {ICONS[name]}
    </svg>
  );
}

export function PageHeader({ eyebrow, title, lede, actions }: { eyebrow?: string; title: string; lede?: ReactNode; actions?: ReactNode }) {
  return (
    <header className="page-head">
      <div>
        {eyebrow && <p className="eyebrow">{eyebrow}</p>}
        <h1>{title}</h1>
        {lede && <p className="lede">{lede}</p>}
      </div>
      {actions && <div className="actions">{actions}</div>}
    </header>
  );
}

export function SectionHeader({ id, title, sub, action }: { id: string; title: string; sub?: ReactNode; action?: ReactNode }) {
  return (
    <div className="section-head">
      <div>
        <h2 id={id} className="section-title">{title}</h2>
        {sub && <p className="section-sub">{sub}</p>}
      </div>
      {action}
    </div>
  );
}

export function EmptyState({ icon = "create", title, children, action }: { icon?: IconName; title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className="empty-state" role="status">
      <span className="empty-icon"><Icon name={icon} size={22} /></span>
      <h3>{title}</h3>
      {children && <p className="muted">{children}</p>}
      {action}
    </div>
  );
}

export type Tone = "good" | "warn" | "bad" | "info" | "neutral";

/** A status that never relies on colour alone: the label is always text. */
export function StatusBadge({ tone = "neutral", children }: { tone?: Tone; children: ReactNode }) {
  return <span className={`status-badge ${tone}`}><span className="status-dot" aria-hidden="true" />{children}</span>;
}

export function Stepper({ steps, current }: { steps: { title: string; sub: string }[]; current: number }) {
  return (
    <ol className="stepper" aria-label="Steps">
      {steps.map((s, i) => (
        <li key={s.title} className={i === current ? "now" : i < current ? "done" : ""} aria-current={i === current ? "step" : undefined}>
          <span className="step-num" aria-hidden="true">{i < current ? <Icon name="check" size={14} /> : i + 1}</span>
          <span><strong>{s.title}</strong><span className="small muted">{s.sub}</span></span>
        </li>
      ))}
    </ol>
  );
}
