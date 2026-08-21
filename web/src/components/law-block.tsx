import type { ReactNode } from "react";

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
