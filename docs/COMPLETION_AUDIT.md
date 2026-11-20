# Completion audit — 2026-09-19

Scope: all latest UI, persistence, batch, candidate-count, retry and quality-statistics requests; preserve original script, generated files, LoRA settings and human selection.

| Requirement | Evidence | Status |
|---|---|---|
| One-click whole-book storyboards, then collective review/edit | storyboard_tools.py; full/partial draft and conflict tests | Implemented; model-backed whole-book draft path still to verify |
| Each modification visible, Apply/Cancel on right | Browser isolated fixture and per-unit mutation tests | Verified |
| 16 candidates for every panel; all rendering before review | phased_drawing.py; 16-slot and stage-order tests | Offline verified; full real run pending |
| Skip per-image errors, retry after others | fault injection; bounded retry; separate review retry | Verified offline |
| Recover ambiguous submissions without duplicate jobs | comfy_recovery.py; success-import and targeted-interrupt tests | Verified offline |
| Reduce model churn; real progress and estimated remaining time | phase residency, real sampling/token events | Real phase timing pending |
| Selecting image does not jump to top | real browser at scroll 2051.20 before/after/reload | Verified |
| Manga bubbles, narrow gutters, image fill | layout export visually inspected; pixel tests | Verified; user refinement remains editable |
| Reopen current editing, including unsaved/new work | workspace persistence; original UI tests; new-project fix | Verified in isolated browser; story/mode/preferences and open character draft survive reload |
| Preserve all existing work | 545 files, exact project JSON/settings unchanged before real run | Verified after user stop; no-gpu-preservation.json |
| Historical failures-per-pass | stats endpoint: 538:1 old + 5:1 diagnostic | Verified; approved current recipe sample pending |
| Finish with owned inference processes unloaded | finally cleanup; ports/model ownership checks | Verified after user stop: no listeners on 8091/8188; web remains live |

113 offline tests pass in data/test-results/completion-audit-tests.txt. This file is a checklist, not proof of real-model completion. Do not mark the active goal complete while pending items remain.

## User restriction — 2026-09-19

User explicitly requested no GPU calls. Real job e510bbde119a4afab71cc02b33f88008 was stopped during drawing-description preparation (7/30 processed); no new image rendering had started. Existing outputs remain saved. Inference processes were stopped and ports 8091/8188 are no longer listening. Do not resume generation, visual review, or GPU performance tests without new authorization. Continue UI/code/offline validation only; real-model pending checks remain unverified.

114 CPU-only offline tests passed. Per-panel statistics now expose zero-pass panels and separate pending/uncertain outcomes; browser table verified with three fixture panels. SVG SFX outline now matches PNG. Current remaining gates are real-model execution/performance/quality, which must remain unverified under the user no-GPU constraint.
