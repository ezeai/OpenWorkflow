import { useRef, type KeyboardEvent, type ReactNode } from "react";

export type TabItem<T extends string> = { id: T; label: ReactNode; panelId?: string };

/**
 * A segmented tab set that follows the usual keyboard pattern: Left and Right move between tabs (wrapping),
 * Home and End jump to the ends, and only the selected tab sits in the Tab order. Selection follows focus.
 */
export function Tabs<T extends string>({ items, value, onChange, label, className = "segmented" }: {
  items: TabItem<T>[]; value: T; onChange: (id: T) => void; label: string; className?: string;
}) {
  const refs = useRef<Record<string, HTMLButtonElement | null>>({});

  const move = (to: number) => {
    const next = items[(to + items.length) % items.length];
    onChange(next.id);
    refs.current[next.id]?.focus();
  };

  const onKey = (e: KeyboardEvent<HTMLButtonElement>, index: number) => {
    if (e.key === "ArrowRight") { e.preventDefault(); move(index + 1); }
    else if (e.key === "ArrowLeft") { e.preventDefault(); move(index - 1); }
    else if (e.key === "Home") { e.preventDefault(); move(0); }
    else if (e.key === "End") { e.preventDefault(); move(items.length - 1); }
  };

  return (
    <div className={className} role="tablist" aria-label={label}>
      {items.map((it, i) => {
        const selected = it.id === value;
        return (
          <button
            key={it.id}
            ref={(el) => { refs.current[it.id] = el; }}
            type="button"
            role="tab"
            id={`tab-${it.id}`}
            aria-selected={selected}
            aria-controls={it.panelId}
            tabIndex={selected ? 0 : -1}
            className={selected ? "seg active" : "seg"}
            onClick={() => onChange(it.id)}
            onKeyDown={(e) => onKey(e, i)}
          >
            {it.label}
          </button>
        );
      })}
    </div>
  );
}
