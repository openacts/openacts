import type { ActRecord } from "./contracts";

const TEXT_KIND_LABELS: Record<string, string> = {
  as_enacted: "As enacted",
  consolidated: "Consolidated",
};

export function actTextKind(act: ActRecord): string {
  return act.text_kind ?? "as_enacted";
}

export function textKindLabel(textKind: string): string {
  return TEXT_KIND_LABELS[textKind] ?? textKind;
}

export function currentnessText(act: ActRecord): string {
  const checked = act.checked_through_date
    ? `checked through ${act.checked_through_date}`
    : "currentness not established";
  return `${textKindLabel(actTextKind(act))} · status ${act.status} · ${checked}`;
}
