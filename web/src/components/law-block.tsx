import type { ReactNode } from "react";

// The statute's own device: the label sits outside the text block. Inline at
// narrow widths, pulled into the left margin from lg up. One DOM either way,
// so nothing is duplicated or hidden. The hairline marks the reading edge and
// is the only persistent rule on the page (decision 0023).
export function LawBlock({
  label,
  children,
}: {
  label: string | null;
  children: ReactNode;
}) {
  return (
    <div className="relative pb-7 lg:ml-20 lg:border-l lg:border-line lg:pl-7">
      {label ? (
        <span className="font-reading text-[1.1875rem] font-medium leading-[1.75] text-action-strong lg:absolute lg:-left-20 lg:top-0 lg:w-16 lg:text-right">
          {label}{" "}
        </span>
      ) : null}
      {children}
    </div>
  );
}
