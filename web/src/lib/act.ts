import type { ActRecord } from "./contracts";

const TEXT_KIND_LABELS: Record<string, string> = {
  as_enacted: "As enacted",
  consolidated: "Consolidated",
};

/**
 * The canonical act record omits `text_kind` when it holds the default value,
 * but the projection stores it NOT NULL via `COALESCE(text_kind, 'as_enacted')`
 * (see `api/sql/001_projection.sql`). Applying the same default here keeps the
 * Act detail page from contradicting the list endpoint for the same Act.
 */
export function actTextKind(act: ActRecord): string {
  return act.text_kind ?? "as_enacted";
}

export function textKindLabel(textKind: string): string {
  return TEXT_KIND_LABELS[textKind] ?? textKind;
}

/**
 * Currentness is deliberately three separate claims. Never collapse them into a
 * single "verified" signal: text kind, recorded status, and how far the text has
 * been checked are independent and independently unreliable.
 */
export function currentnessText(act: ActRecord): string {
  const checked = act.checked_through_date
    ? `checked through ${act.checked_through_date}`
    : "currentness not established";
  return `${textKindLabel(actTextKind(act))} · status ${act.status} · ${checked}`;
}
