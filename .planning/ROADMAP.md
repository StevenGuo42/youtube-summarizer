# Roadmap: YouTube Video Summarizer

## Milestones

- ✅ **v1.0 MVP** — Phases 1-8 (shipped 2026-03-30)
- 🚧 **v1.1 Bugfix & Polish** — Phases 9-12 (in progress)

## Phases

<details>
<summary>✅ v1.0 MVP (Phases 1-8) — SHIPPED 2026-03-30</summary>

- [x] Phase 1: Backend Filter Update (1/1 plan) — completed 2026-03-30
- [x] Phase 2: Frontend Shell (1/1 plan) — completed 2026-03-30
- [x] Phase 3: Browse & Queue Tab (1/1 plan) — completed 2026-03-30
- [x] Phase 4: Queue & Progress Tab (1/1 plan) — completed 2026-03-30
- [x] Phase 5: Summaries Tab (1/1 plan) — completed 2026-03-30
- [x] Phase 6: Settings Tab (1/1 plan) — completed 2026-03-30
- [x] Phase 7: Integration Fixes (1/1 plan) — completed 2026-03-30
- [x] Phase 8: Fix Tech Debt (1/1 plan) — completed 2026-03-30

</details>

### 🚧 v1.1 Bugfix & Polish (In Progress)

**Milestone Goal:** Fix frontend bugs from v1.0 launch and add missing polish features (queue management, batch mode UI, custom prompts per job).

- [ ] **Phase 9: Settings Tab Fixes** - Fix cookie upload, auth status, save persistence, and value reload
- [ ] **Phase 10: Browse Tab Fixes** - Fix URL input styling, add channel pagination, add per-job custom prompt
- [x] **Phase 11: Queue Improvements** - Add clear button, eliminate polling flash, add batch processing toggle (completed 2026-03-31)
- [ ] **Phase 12: Summaries Fixes** - Fix processed video display and responsive view styles

## Phase Details

### Phase 9: Settings Tab Fixes
**Goal**: Users can reliably configure the app through a working settings tab
**Depends on**: Nothing (first phase of v1.1)
**Requirements**: SET-01, SET-02, SET-03, SET-04, SET-05, SET-06
**Success Criteria** (what must be TRUE):
  1. User can upload a cookies file via paste, file browser, or drag-and-drop and sees it reflected on the server
  2. User sees Claude auth status (authenticated or not) without console errors or loading failures
  3. User clicks save on LLM settings and sees confirmation feedback (not silence)
  4. User refreshes the page and their saved model and custom prompt values are still populated
**Plans**: 1 plan
**UI hint**: yes

Plans:
- [x] 09-01-PLAN.md — Fix backend auth status distinction and all frontend settings bugs

### Phase 10: Browse Tab Fixes
**Goal**: Users can comfortably browse channels and attach custom prompts before queueing jobs
**Depends on**: Phase 9
**Requirements**: BRW-01, BRW-02, BRW-03
**Success Criteria** (what must be TRUE):
  1. User sees the full URL input box without right-side clipping on standard viewport widths
  2. User can page through a large channel's video list (not forced to load all at once)
  3. User can write a custom prompt and attach it to selected videos before adding them to the queue
**Plans**: TBD
**UI hint**: yes

Plans:
- [ ] 10-01: TBD

### Phase 11: Queue Improvements
**Goal**: Users have smooth, responsive queue management with batch processing support
**Depends on**: Phase 9
**Requirements**: QUE-01, QUE-02, QUE-03
**Success Criteria** (what must be TRUE):
  1. User can clear all jobs from the queue with a single button click
  2. Queue list updates in place without visible flash or full re-render when polling detects changes
  3. User can toggle batch processing mode on or off from the UI and the backend respects the setting
**Plans**: TBD
**UI hint**: yes

Plans:
- [x] 11-01-PLAN.md -- Bulk delete endpoints and polling flash fix
- [ ] 11-02-PLAN.md -- Queue UI (clear button, batch toggle)

### Phase 12: Summaries Fixes
**Goal**: Users can view and browse all their completed summaries in any view style
**Depends on**: Nothing (independent of other v1.1 phases)
**Requirements**: SUM-01, SUM-02
**Success Criteria** (what must be TRUE):
  1. User sees summaries for all videos that completed processing (no missing entries)
  2. User can switch between compact, list, and full view styles and each renders correctly at different viewport widths
**Plans**: 1 plan
**UI hint**: yes

Plans:
- [ ] 12-01-PLAN.md — Fix code fence parsing, responsive layout, and spinner shift

## Progress

| Phase | Milestone | Plans Complete | Status | Completed |
|-------|-----------|----------------|--------|-----------|
| 1. Backend Filter Update | v1.0 | 1/1 | Complete | 2026-03-30 |
| 2. Frontend Shell | v1.0 | 1/1 | Complete | 2026-03-30 |
| 3. Browse & Queue Tab | v1.0 | 1/1 | Complete | 2026-03-30 |
| 4. Queue & Progress Tab | v1.0 | 1/1 | Complete | 2026-03-30 |
| 5. Summaries Tab | v1.0 | 1/1 | Complete | 2026-03-30 |
| 6. Settings Tab | v1.0 | 1/1 | Complete | 2026-03-30 |
| 7. Integration Fixes | v1.0 | 1/1 | Complete | 2026-03-30 |
| 8. Fix Tech Debt | v1.0 | 1/1 | Complete | 2026-03-30 |
| 9. Settings Tab Fixes | v1.1 | 0/1 | Planning | - |
| 10. Browse Tab Fixes | v1.1 | 0/0 | Not started | - |
| 11. Queue Improvements | v1.1 | 1/2 | Complete    | 2026-03-31 |
| 12. Summaries Fixes | v1.1 | 0/1 | Planning | - |

---
*Full v1.0 details: .planning/milestones/v1.0-ROADMAP.md*
