# Change process and specifications

How much process a change needs depends on its risk. The delivery workflow is the one in
[AGENTS.md](../AGENTS.md): implement, test, open a pull request, Codex review, fix, owner
merges. This page only decides how much has to be written down **before** implementing.

## Three tiers

| Tier | Typical changes | What is needed |
| --- | --- | --- |
| **Routine** | Bug fixes with clear intended behavior, refactors without semantic impact, documentation, dependency metadata, tests, tooling, internal cleanup, UI or reporting tweaks that touch no safety area | Implement, test (add a regression for a fix) and open a PR. No spec, no approval gate. |
| **Normal feature** | New commands or options, new reports, performance work, new user-visible behavior outside the safety areas | A GitHub issue with concise acceptance criteria, then implementation, tests and Codex review. No separate spec or approval step unless material behavior is still unresolved; in that case settle the question in the issue first. |
| **Safety-critical** | Upload recovery and retries, duplicate safety, FIT/TCX output integrity, timezone and timestamp handling, credentials and token storage, and compatibility-sensitive changes (manifest, workspace, state database, identifiers, CLI, JSON Lines contract) | A written spec with acceptance criteria, approved by the owner before implementation, and full verification (below). |

When unsure, take the higher tier only if a wrong decision could lose or corrupt data,
upload something wrongly, expose credentials or break compatibility. Size alone does not
decide the tier; a one-line change to retry logic is safety-critical, a large report
redesign is not.

Do not create retrospective specs, placeholder specs, or a spec merely to restate an
obvious bug.

## Safety-critical changes

1. **Spec first.** Copy the [template](#template) to the next `specs/NNN-short-title.md`
   (numbers increase monotonically; check existing files and Git history so deleted or
   superseded IDs are not reused). Describe intended behavior, scope and non-goals, error
   and recovery behavior, data integrity, compatibility and edge cases, and number the
   acceptance criteria (`AC-01`, ...).
2. **Owner approval of intended behavior** before implementing. A brief explicit approval
   is enough; record the date and a public-safe reference in the spec. Resolve material
   open questions before approval.
3. **Implement the smallest coherent change** and cover every acceptance criterion, edge
   case and failure path with automated tests where practical. Justify any manual check.
4. **If the spec turns out wrong or impossible**, stop the affected work, revise the spec,
   and get the changed behavior approved. Do not reinterpret a criterion in code.
5. **Verify** with the full repository checks ([testing](../docs/testing.md)) and a short
   completion note in the spec: the PR reference, that the criteria are covered, and any
   deviations or follow-ups. A criterion that is not met means the work is incomplete
   unless the spec is revised and approved.

There is no separate compliance review stage and no per-criterion evidence table; the PR,
its tests and the Codex review are the verification record.

## Specifications in this repository

SPEC-001 to SPEC-004 and their decisions, requirements and regression tests stay in force
and are not rewritten. Their metadata may use the older status words (Draft, Reviewed,
Approved, Implemented, Verified, Superseded); they keep their meaning. New specs use
Draft, Approved, Implemented and Superseded.

Code and current [architecture](../docs/architecture.md) describe existing behavior. An
approved spec defines the intended target of its scoped change. When they disagree,
decide explicitly whether the code should change, the spec assumed something wrong, or
compatibility was missed; do not let either win silently. Update architecture or user
documentation when the implemented change affects it.

## Template

Scale the detail to the change; write "none" for sections that do not apply.

```markdown
# SPEC-NNN: Title

Status: Draft
Created: YYYY-MM-DD
Approval: Pending (date and public-safe approver reference)
Supersedes: None

## Problem and motivation

## Current behavior

## Intended behavior

## Scope and non-goals

## Data, state and compatibility impact
Manifest, workspace, state database, identifiers, CLI and contract compatibility.

## Error and recovery behavior

## Data integrity, security and privacy

## Edge cases

## Acceptance criteria
- AC-01: Observable outcome.
- AC-02: Observable failure or edge-case outcome.

## Open questions
None, or decisions needed before approval.

## Decision log
- YYYY-MM-DD: Decision and rationale.

## Completion
PR reference, validation result, deviations (or None) and follow-up work (or None).
```
