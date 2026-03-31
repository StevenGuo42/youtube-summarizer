# Requirements: YouTube Video Summarizer

**Defined:** 2026-03-30
**Core Value:** Submit a YouTube URL and get back a structured, visual-aware summary through a usable web interface.

## v1.1 Requirements

Requirements for Bugfix & Polish milestone. Each maps to roadmap phases.

### Settings

- [x] **SET-01**: User can upload cookies via paste content button and it saves to server
- [x] **SET-02**: User can upload cookies via file browser and it saves to server
- [x] **SET-03**: User can upload cookies via drag-and-drop and it saves to server
- [x] **SET-04**: User sees Claude authentication status without errors when server is running
- [x] **SET-05**: User sees confirmation feedback after clicking save settings
- [x] **SET-06**: User's model and custom prompt values persist across page refresh

### Browse

- [ ] **BRW-01**: User sees properly styled URL input box without right-side clipping
- [ ] **BRW-02**: User can load channel videos with pagination (not all at once)
- [ ] **BRW-03**: User can set a custom prompt per job or group of jobs from the browse tab

### Queue

- [x] **QUE-01**: User can clear all jobs from the queue with a single button
- [x] **QUE-02**: Queue tab updates without full-page flash (diff-based updates, skip if no changes)
- [ ] **QUE-03**: User can toggle batch processing mode from the queue or settings UI

### Summaries

- [ ] **SUM-01**: User sees summaries for successfully processed videos
- [ ] **SUM-02**: User can switch between compact/list/full view styles and they render responsively

## Future Requirements

### v2 (Deferred)

- **MULTI-01**: Multi-provider LLM support (OpenAI, Google, Ollama)
- **DEPLOY-01**: Docker deployment configuration
- **SEARCH-01**: Channel search functionality (orphaned endpoint exists)

## Out of Scope

| Feature | Reason |
|---------|--------|
| Mobile app | Web-first, single-user self-hosted |
| Multi-user accounts | Single-user self-hosted app |
| Real-time SSE for queue updates | Polling with diff-check sufficient for v1.1 |
| New pipeline features | Backend pipeline is complete; v1.1 is frontend polish |

## Traceability

| Requirement | Phase | Status |
|-------------|-------|--------|
| SET-01 | Phase 9 | Complete |
| SET-02 | Phase 9 | Complete |
| SET-03 | Phase 9 | Complete |
| SET-04 | Phase 9 | Complete |
| SET-05 | Phase 9 | Complete |
| SET-06 | Phase 9 | Complete |
| BRW-01 | Phase 10 | Pending |
| BRW-02 | Phase 10 | Pending |
| BRW-03 | Phase 10 | Pending |
| QUE-01 | Phase 11 | Complete |
| QUE-02 | Phase 11 | Complete |
| QUE-03 | Phase 11 | Pending |
| SUM-01 | Phase 12 | Pending |
| SUM-02 | Phase 12 | Pending |

**Coverage:**
- v1.1 requirements: 14 total
- Mapped to phases: 14
- Unmapped: 0

---
*Requirements defined: 2026-03-30*
*Last updated: 2026-03-30 after roadmap creation*
