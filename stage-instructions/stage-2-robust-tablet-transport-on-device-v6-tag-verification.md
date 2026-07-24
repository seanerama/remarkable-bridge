# Stage 2: Robust tablet transport + on-device v6 tag verification

- **Type:** chore
- **Depends on:** 1

## Objectives

Turn Stage 1's minimal `TabletClient` into a robust, efficient scanner, and **close the
empirical gate** the architecture flagged: confirm the real v6 tag JSON shape on the
user's device and reconcile the `tablet-document` reader to it. Also lay the
groundwork for graceful unreachability.

## What to build

- **Efficient change detection:** replace naive full-listing with a single
  `find <xochitl> -name '*.metadata' -o -name '*.content' -newer <marker>` + batched
  `cat` over one SSH connection; parse into `TabletDoc`s. Avoid rsyncing the tree.
- **Tag-shape reconciliation:** read one **real** tagged `.content` file from the
  device; confirm document-level `tags[]` and page-level `cPages.pages[].tags[]`
  against the `tablet-document` contract. Make the reader tolerate the alternate/older
  shapes the contract lists (bare `["review"]`, `pageTags`); unknown fields ignored.
- **Reachability handling (groundwork):** `tablet.scan()` distinguishes "unreachable"
  (SSH connect/timeout failure) from "reachable, nothing new"; unreachable → logged,
  no state change, retried next cycle (never crash). Full backoff is a later ops stage.
- Content-hash (`sha256` over `.content` + page `.rm` bytes) computed without pulling
  whole notebooks when only metadata changed, where feasible.

## Interface contracts

- **Consumes (frozen):** `tablet-document`. If the empirical check reveals a shape the
  contract cannot express **additively**, STOP and return to `/verity:plan` for a new
  contract — do not edit the frozen one.
- **Exposes:** hardened `TabletClient` scan/pull; same interface as Stage 1 (additive).

## Testing requirements

- Unit tests for the reader across **all** documented tag shapes (fixtures), including
  page-level-only tags and mixed doc+page tags.
- A test asserting `scan()` maps an SSH failure to an "unreachable" outcome (injected
  failing client) with no state mutation.
- Record the verified real-device `.content` sample (redacted) as a fixture + a note in
  the contract's "verify empirically" section marking the gate closed.

## Acceptance conditions

- [ ] v6 tag shape confirmed on-device; reader handles it + documented variants.
- [ ] `tablet-document` "empirical-verification gate" marked closed (or a new contract
      opened via plan if additive expression was impossible).
- [ ] Unreachable tablet → logged, no reprocessing, recovers next cycle (accept. test #4, partial).
- [ ] Existing suite stays green; CI all-green.

## Pipeline test: NO
