# 0025 - Local operator console

**Status:** Proposed
**Date:** 28 August 2026

## Context

The pipeline is driven by seven make targets. Each takes the artifact path the
previous one printed, so an operator copies content-addressed filenames between
commands and keeps the state of every Act in their head. Two stages run for
minutes to hours and report progress only as JSON lines on stderr.

Review has no interface at all. `openacts review` records a whole-candidate
assertion and, as `pipeline/README.md` states, does not perform or infer the
review. Per-Provision `double_reviewed` and `source_conflict` verdicts are hand
edited in JSONL. The reviewer reads the PDF beside a text editor.

Deferring audit findings to review made that gap load-bearing. The audit now
completes with recorded findings and variances, each carrying a page, a line, the
source excerpt and the draft text, precisely so a human can adjudicate extraction
damage rather than have the run thrown away. No command presents them.

## Decision

Build a local operator console over the existing pipeline. Pages are semantic
HTML with no stylesheet; JavaScript is used only where it earns its place.

Stages are close enough in shape to describe rather than hand-write. Each takes
an input artifact and produces one; four of the seven have a separate execute,
`candidate` takes a second input, and `review` takes a verdict. A registry entry
records that variation -- input folder, extra parameters, whether an execute
exists, whether the stage writes the corpus -- and forms, dispatch and the
overview are generated from it. The claim is that a new stage costs an entry, not
that the stages are identical.

Stages run as the ordinary command line in a subprocess. The console cannot drift
from the CLI, and it inherits the dry-run and execute discipline unchanged rather
than reimplementing it.

Progress streams over Server-Sent Events reading the job's progress file, and a
job's state lives on disk so it survives a console restart.

Only `structure` emits progress today. `extract` emits none. That was recorded
here as a second prerequisite on the strength of an OCR run of roughly forty-five
minutes on a forty-seven page scan. Measuring the sources actually in the cache
retired it: every extraction completes in under sixteen seconds, the longest
being a 253-page Act at 15.8s. The slow run was the PLAC scan, which has since
been removed as an unofficial source, and the only remaining OCR candidate is
blocked at classification. Extraction is instrumented when a scan re-enters the
corpus, not before.

The console is a separate application from `api/`. That serves the public reader
and must never carry a control that writes to `corpus/`. The console binds to
loopback only.

Review is a first-class page rather than a form over the existing command. Each
Provision is shown with the source excerpt it claims, the audit findings and
variances recorded against it, and its own fidelity verdict.

That page has nothing to call today. `openacts review` records one fidelity for a
whole candidate and accepts only `single_reviewed`; `double_reviewed` and
`source_conflict` have no command. The console therefore depends on the review
command first gaining a single-Provision verdict. Writing candidate records from
the console instead would abandon the rule that the console never restates
pipeline logic, on the one page where the stakes are highest.

The console is served with FastAPI and Jinja, as `api/` already is, and ships as
a supported tool with its own tests rather than as unreviewed scaffolding.

A workflow orchestrator was considered and rejected. Dagster and Prefect are the
standard answer for pipelines with long stages and artifacts, but both want a
daemon, a database and the stages restated in their own vocabulary, and this
pipeline already owns its checkpointing, resume and retry. Neither supplies the
review interface, which is the part that does not exist.

## Delivery

The console is built in three parts, each useful on its own.

1. Artifact browser and stage runner. What exists for each Source, what stage it
   has reached, and a form that selects an input rather than pasting a path.
   Depends on nothing.
2. Job pages, streaming `structure` progress. Extraction joins if it is ever
   instrumented; the measurement above says it does not need to be.
3. The review interface. Depends on the review command accepting a
   single-Provision verdict.

All three are built. `openacts review` now takes `--provision`, and the console
renders findings against the Provisions they concern.

## Consequences

An operator stops transcribing digests between commands, and the state of every
Act is visible in one place. Long stages become observable without a polling
loop. Adding a pipeline stage costs one registry entry.

The console holds corpus write access, so it is loopback-only, is never deployed
with the reader, and keeps the dry run as the default for every stage that has
one. Running stages as subprocesses means a job outlives the request that started
it and is cancelled through the console rather than by closing a browser tab.

Recording per-Provision verdicts moves fidelity decisions out of hand-edited
JSONL and into an interface that shows the evidence beside the claim.

## Least certain

What reviewing a Provision actually requires. This is the weakest part of the
plan and the most valuable part of the console. The audit records the source line
it compared against, which is plainly enough for a two-character variance and
plainly not enough for a disputed Schedule, and nobody has yet reviewed an Act
this way to find out where the line falls. Rendering the PDF page beside the text
is the obvious answer and is a materially larger build than the rest of phase 3.

Building phase 3 settled one part of that. The audit reports findings against the
Source, not against the draft, so no artifact links a finding to the Provision a
reviewer would edit. That link has to be rebuilt in the console, and how well it
can be depends on the finding: a variance and a claim-derived issue both quote
the draft's own text, so the Provision containing that quote is found exactly,
but `missing_source` reports text the draft never produced and nothing in it can
name a Provision. On the NITDA Act that splits nine issues and five variances
into seventeen located findings and six that only carry a page. The located ones
attach to their Provision; the rest are grouped by page with that page's
Provisions offered as leads. Copying them onto every Provision sharing a page
instead would have flagged 74 of 229 Provisions for six findings.

Whether a job should be recoverable after the console restarts. Its progress and
result are on disk and readable, but a subprocess whose parent has gone is no
longer attached to anything that can report its exit or stop it. Treating a job
as abandoned is honest and cheap; reattaching is neither.

How concurrent edits behave. `review` rewrites a candidate's Provision records in
place, so two console tabs recording verdicts on one candidate can lose a write.
The CLI never had this problem because a person ran one command at a time.

Whether generating pages from a registry survives contact with the stages that do
not fit it. `candidate` needs a second input chosen from a different folder, and
`promote` needs a confirmation the others do not. If the exceptions outgrow the
rule, hand-written pages are the cheaper answer and this decision should be
revisited rather than defended.
