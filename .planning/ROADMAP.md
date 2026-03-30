# Roadmap: YouTube Video Summarizer

## Overview

The backend is fully implemented. This milestone delivers the frontend SPA and one backend update (visibility/date filters). Phase 1 updates the backend API, Phase 2 builds the frontend shell and shared infrastructure, then Phases 3-6 build each tab as a vertical slice with full functionality.

## Phases

**Phase Numbering:**
- Integer phases (1, 2, 3): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked with INSERTED)

Decimal phases appear between their surrounding integers in numeric order.

- [ ] **Phase 1: Backend Filter Update** - Replace boolean members_only with visibility enum and add date range filtering
- [ ] **Phase 2: Frontend Shell** - Tab navigation, dark theme, responsive layout, and shared API/fetch utilities
- [ ] **Phase 3: Browse & Queue Tab** - URL input, video fetching, filtering, multi-select, and queue submission
- [ ] **Phase 4: Queue & Progress Tab** - Job list with status badges, progress bars, cancel, and auto-refresh polling
- [ ] **Phase 5: Summaries Tab** - Summary list, expandable detail view, copy, markdown export, and delete
- [ ] **Phase 6: Settings Tab** - Cookie upload/management, LLM configuration, and Claude auth status

## Phase Details

### Phase 1: Backend Filter Update
**Goal**: Channel video listing supports flexible visibility and date range filtering
**Depends on**: Nothing (first phase)
**Requirements**: BACK-01, BACK-02, BACK-03, BACK-04
**Success Criteria** (what must be TRUE):
  1. Calling the channel videos endpoint with visibility=members_only returns only members-only videos
  2. Calling the channel videos endpoint with date_from and date_to returns only videos within that range
  3. All existing browse and ytdlp tests pass with the updated signatures
**Plans**: 1 plan

Plans:
- [ ] 01-01-PLAN.md — Update ytdlp service and browse router for visibility/date filtering, update all tests

### Phase 2: Frontend Shell
**Goal**: Users see a working app shell with tab navigation, dark theme, and all shared UI infrastructure in place
**Depends on**: Nothing (independent of Phase 1)
**Requirements**: CORE-01, CORE-02, CORE-03
**Success Criteria** (what must be TRUE):
  1. User sees a 4-tab navigation bar (Browse, Queue, Summaries, Settings) and can switch between tabs
  2. App renders in Pico CSS dark theme by default with responsive layout on desktop and mobile viewports
  3. A shared API helper function exists that handles fetch calls with loading states and error display
**Plans**: TBD
**UI hint**: yes

Plans:
- [ ] 02-01: TBD

### Phase 3: Browse & Queue Tab
**Goal**: Users can discover videos and submit them for processing
**Depends on**: Phase 1, Phase 2
**Requirements**: BRWS-01, BRWS-02, BRWS-03, BRWS-04, BRWS-05
**Success Criteria** (what must be TRUE):
  1. User can paste a YouTube URL (video, channel, or playlist) and see video metadata displayed
  2. User can filter displayed channel videos by visibility and date range
  3. User can select individual videos or use Select All, then add them to the queue with a single action
**Plans**: TBD
**UI hint**: yes

Plans:
- [ ] 03-01: TBD

### Phase 4: Queue & Progress Tab
**Goal**: Users can monitor processing jobs and manage the queue
**Depends on**: Phase 2
**Requirements**: QUEU-01, QUEU-02, QUEU-03, QUEU-04, QUEU-05
**Success Criteria** (what must be TRUE):
  1. User can see all jobs with status badges showing the current pipeline step
  2. Each job displays a progress bar that advances through pipeline stages
  3. User can cancel a pending or in-progress job
  4. Queue view updates automatically without manual refresh
**Plans**: TBD
**UI hint**: yes

Plans:
- [ ] 04-01: TBD

### Phase 5: Summaries Tab
**Goal**: Users can review, share, and manage their completed summaries
**Depends on**: Phase 2
**Requirements**: SUMM-01, SUMM-02, SUMM-03, SUMM-04, SUMM-05
**Success Criteria** (what must be TRUE):
  1. User can see a list of completed summaries showing video title and date
  2. User can expand any summary to read the full structured content
  3. User can copy a summary to clipboard or download it as a .md file
  4. User can delete a summary and it disappears from the list
**Plans**: TBD
**UI hint**: yes

Plans:
- [ ] 05-01: TBD

### Phase 6: Settings Tab
**Goal**: Users can configure cookies, LLM settings, and verify auth status
**Depends on**: Phase 2
**Requirements**: SETT-01, SETT-02, SETT-03, SETT-04
**Success Criteria** (what must be TRUE):
  1. User can upload a cookies.txt file via drag-and-drop or file picker and see confirmation
  2. User can view cookie status (loaded/not loaded) and clear cookies
  3. User can configure LLM model and custom prompt, with changes persisted
  4. User can see whether Claude OAuth is authenticated
**Plans**: TBD
**UI hint**: yes

Plans:
- [ ] 06-01: TBD

## Progress

**Execution Order:**
Phases execute in numeric order: 1 -> 2 -> 3 -> 4 -> 5 -> 6

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Backend Filter Update | 0/1 | Not started | - |
| 2. Frontend Shell | 0/0 | Not started | - |
| 3. Browse & Queue Tab | 0/0 | Not started | - |
| 4. Queue & Progress Tab | 0/0 | Not started | - |
| 5. Summaries Tab | 0/0 | Not started | - |
| 6. Settings Tab | 0/0 | Not started | - |
