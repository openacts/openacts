import type { ReactNode } from "react";

// Decision 0023. Provenance is first in the DOM because it qualifies the wording
// that follows, which keeps reading order correct at every width; grid placement
// alone moves it into the right rail on wide screens. No CSS reordering, so
// keyboard and screen-reader order match what is seen.
export function ReadingLayout({
  provenance,
  children,
}: {
  provenance: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="grid items-start gap-x-14 gap-y-8 lg:grid-cols-[minmax(0,1fr)_15rem]">
      <aside className="flex flex-col gap-6 lg:col-start-2 lg:row-start-1">
        {provenance}
      </aside>
      <div className="lg:col-start-1 lg:row-start-1">{children}</div>
    </div>
  );
}

export function RailBlock({
  title,
  children,
}: {
  title: string;
  children: ReactNode;
}) {
  return (
    <div>
      <h2 className="text-[0.6875rem] font-bold uppercase tracking-[0.12em] text-muted">
        {title}
      </h2>
      <div className="id mt-1.5 text-muted">{children}</div>
    </div>
  );
}
