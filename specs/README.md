# Specification-driven development

Specification-Driven Development (SDD) is the preferred workflow for substantial
features, behavioral changes, bugs with non-trivial semantics, compatibility changes
and architecture changes in this repository:

```text
Requirements -> Specification -> Architecture impact -> Acceptance criteria
    -> Review/approval -> Implementation plan -> Implementation
    -> Automated verification -> Specification compliance review
```

Specify, review, plan, implement, verify, then review compliance. Tests are essential
evidence that intended behavior is provided; they are not the specification. TDD is an
optional implementation technique, not the primary methodology or a strict test-first
requirement. See [testing guidance](../docs/testing.md).

## Choose the right amount of process

A dedicated spec is normally appropriate for new features, behavioral or architecture
changes, migration semantics, state machines, manifest/schema/identifier changes,
external API behavior, recovery/retries, compatibility, data integrity, security, and
substantial GUI/user-flow changes.

Typos, documentation corrections, formatting, dependency metadata corrections and
obvious internal cleanup or trivial refactoring with no semantic impact usually need
no dedicated spec. Existing architecture, tests, security rules and invariants still
apply. Do not create retrospective specs for every existing feature, placeholder specs
to reserve numbers, or a spec merely to restate an obvious bug.

For a small bug with already-defined intended behavior: reproduce, identify the root
cause, add a regression where practical, make the minimal fix and verify. If correct
behavior is ambiguous, safety-sensitive, compatibility-sensitive or needs a design
decision, investigate first, specify/review/approve that behavior, plan the change,
implement, verify and review compliance. Size alone does not make a semantic decision
trivial.

## Authority and distinct responsibilities

| Artifact | Role |
| --- | --- |
| Requirements | Describe the problem, desired outcome and constraints; unresolved semantics must be surfaced |
| Specification | Defines **what** must change and **why**, intended observable behavior, scope and acceptance criteria |
| [Architecture](../docs/architecture.md) | Describes the current system, boundaries, responsibilities, invariants and data flow |
| Implementation plan | Describes **how** the approved spec will be implemented: modules, sequencing, internal APIs, tests and any migration/refactoring steps |
| Implementation | Realizes the approved behavior within the agreed boundaries |
| Tests and other verification | Supply evidence for behavior, edge cases, failure handling, compatibility, security and integrity |

Code and current architecture documentation describe existing reality. Where they
disagree about existing behavior, inspect code and tests and report the discrepancy.
An **approved spec** is authoritative for the intended target of its scoped change.
Existing tests, implementation convenience and generic agent workflows must not silently
redefine that target. Specs do not replace the architecture guide; update current
architecture/user documentation when the implemented change affects them.

When current code conflicts with an approved spec, explicitly determine whether code
is expected to change, the spec assumed something incorrectly, or compatibility was
missed. Do not silently make either side win. If implementation reveals an incorrect,
incomplete or impossible spec, stop the affected implementation, update/review the spec
and obtain approval for changed intended behavior before continuing. Record the decision
and adjust the plan/tests; do not reinterpret acceptance criteria in code.

## Identity and lifecycle

Use `specs/001-short-title.md`, `specs/002-another-title.md`, and so on, with title
`SPEC-001: Short title`. Allocate the next monotonically increasing numeric ID when
creating a real spec; check existing files and Git history so deleted/superseded IDs
are not reused. IDs remain stable if titles change. Resolve parallel numbering
collisions before merging. These filenames are examples, not reserved specs.

Keep metadata and decisions in Markdown; no status tooling is required.

| Status | Meaning |
| --- | --- |
| Draft | Proposed behavior; open questions may remain |
| Reviewed | Behavioral and architecture-impact review completed; identified questions/changes recorded |
| Approved | Material questions resolved, acceptance criteria finalized, and intended behavior approved by the responsible maintainer or requesting user |
| Implemented | Code and supporting tests/docs written; verification or compliance review may remain |
| Verified | Every acceptance criterion has evidence, required validation passed, and final compliance review is complete |
| Superseded | Replaced by a linked later spec; retain the original ID and decision history |

Record the approval date and reviewer/approver reference in metadata or the decision
log, using a public-safe name/handle or review link. A reviewed spec is not automatically
approved. A brief explicit approval is sufficient; no separate meeting or review document
is required. Do not mark work Verified solely because tests pass or code is committed.

## Review, plan and verification

Before substantial implementation, resolve material questions, define observable
acceptance criteria and review architecture impact: dependency boundaries, invariants,
domain semantics, source integrity, persistent state, compatibility, external APIs,
privacy/security and future UI/API consumers. This review may live in the spec itself.
Approval precedes the implementation plan and substantial implementation.

Keep the plan proportional and linked to the spec. It may be a section in the spec or
a linked Markdown document; do not introduce a second numbering system or tool. It may
choose implementation details but must not silently change approved behavior. Optional
implementation suggestions belong in Implementation notes, not acceptance criteria.

Use unique, stable criterion IDs within each spec: `AC-01`, `AC-02`, etc. Describe
explicit, observable, verifiable outcomes independently of implementation details where
practical. Do not renumber existing criteria merely to reorder them. Verification must
identify evidence for **each** criterion: automated/integration tests, static checks or
justified manual checks. Prefer automation where practical. Important tests, commits or
PR descriptions may reference `SPEC-NNN`/`AC-NN`; not every unit test needs an AC label,
and one criterion may need several checks. Markdown and Git history are sufficient;
no database, generated traceability matrix or custom automation is required.

Implement the smallest coherent change and add tests from the acceptance criteria,
edge cases and failure/compatibility/security/integrity requirements. Tests may precede,
accompany or follow a small implementation step. Appropriate final automated coverage
and the [complete repository checks](../docs/testing.md#setup-and-required-checks)
remain required. Do not weaken tests to hide failures; intentionally changed expectations
must follow reviewed behavior, not make implementation details into requirements.

## Final specification compliance review

Before declaring specified work complete, review the complete diff and check:

- Every acceptance criterion is satisfied and has recorded verification evidence.
- Implementation matches intended behavior; any added behavior is justified and reviewed.
- No criterion was silently reinterpreted, dropped or replaced by a test detail.
- Architecture boundaries, data integrity, compatibility and security/privacy requirements
  are preserved or changed only as explicitly approved.
- Relevant current architecture/user documentation is updated.
- Intentional deviations are recorded and approved where they change intended behavior;
  remaining follow-up work and limitations are explicit.

An unmet criterion means the feature is incomplete unless the spec is intentionally
revised, reviewed and approved. Recording a deviation alone does not waive a criterion.
Record commits, criterion status/evidence and results in Completion. If the implementation
commit hash is unavailable until commit, add it in a later documentation update or use
a stable Git/PR reference; do not try to embed a commit's own hash inside itself.

## Reusable template

Copy this template into a real numbered spec when needed. Scale detail to the change;
mark non-applicable impacts explicitly as none. Resolve material open questions before
approval. The template is not a feature specification.

```markdown
# SPEC-NNN: Title

Status: Draft
Created: YYYY-MM-DD
Approval: Pending (record date and public-safe approver/review reference)
Implementation plan: Pending (section or document link after approval)
Supersedes: None

## Problem
What problem are we solving?

## Motivation
Why does this change matter?

## Current behavior
Describe existing behavior; reference implementation and current documentation.

## Intended behavior
Describe precisely what the system must do after this change.

## Scope
What is included?

## Non-goals
What is intentionally excluded?

## Terminology
Define ambiguous domain terms, or state that no additional definitions are needed.

## Architecture impact
Identify components, dependencies, invariants and future UI/API consumers affected.
Record architecture-impact review decisions before implementation.

## Data and state impact
Describe domain, manifest, file, persistence, state-machine, identifier and schema
impacts. State explicitly when there are none.

## Detailed behavior
Describe flows and rules. Use examples, tables or diagrams when they add precision.

## Error and recovery behavior
Define failures and interruptions, especially around APIs, uploads, files and state.

## Data integrity
Explain source-data and migration-integrity preservation.

## Security and privacy
Describe relevant implications, or explicitly state none.

## Compatibility
Describe backward, workspace, manifest, state and CLI compatibility as applicable.

## Edge cases
List significant boundary cases and expected outcomes.

## Acceptance criteria
- AC-01: Observable outcome.
- AC-02: Observable failure or edge-case outcome.

## Verification
For every AC, describe the planned check and where evidence will be recorded.
Prefer automated tests; justify manual checks and respect API/privacy safeguards.

## Implementation notes
Optional suggestions, not behavioral requirements. Link the implementation plan
created after approval, or add its section here.

## Open questions
List decisions requiring resolution before approval; write None when resolved.

## Decision log
- YYYY-MM-DD: Decision, rationale and review/approval reference.

## Completion
- Implementation commit(s) or stable reference:
- Per-AC satisfaction and verification evidence:
- Focused and complete validation results:
- Specification compliance review outcome:
- Intentional deviations and approval references (or None):
- Architecture/user documentation updates:
- Follow-up work and limitations (or None):
```
