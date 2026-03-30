# Requirements: YouTube Video Summarizer

**Defined:** 2026-03-29
**Core Value:** Submit a YouTube URL and get back a structured, visual-aware summary through a usable web interface.

## v1 Requirements

Requirements for completing the frontend milestone. Each maps to roadmap phases.

### Backend Updates

- [x] **BACK-01**: Channel videos endpoint accepts visibility filter (all/public/members_only) replacing boolean members_only
- [x] **BACK-02**: Channel videos endpoint accepts date_from and date_to params (YYYYMMDD format) for date range filtering
- [x] **BACK-03**: Browse router endpoint params updated to match new service signature
- [x] **BACK-04**: ytdlp and browse tests updated for new visibility and date range params

### Browse Tab

- [ ] **BRWS-01**: User can enter a YouTube URL (video, channel, or playlist) and fetch video metadata
- [ ] **BRWS-02**: User can filter channel videos by visibility (All / Public / Members Only)
- [ ] **BRWS-03**: User can filter channel videos by date range (from / to date pickers)
- [ ] **BRWS-04**: User can select multiple videos via checkboxes with Select All / Deselect All
- [ ] **BRWS-05**: User can add selected videos to the processing queue

### Queue Tab

- [ ] **QUEU-01**: User can view all jobs with their current status (pending, downloading, transcribing, extracting, summarizing, done, failed)
- [ ] **QUEU-02**: Each job displays a status badge reflecting its pipeline step
- [ ] **QUEU-03**: Each job shows a progress bar indicating pipeline step progress
- [ ] **QUEU-04**: User can cancel pending or in-progress jobs
- [ ] **QUEU-05**: Queue view auto-refreshes via polling (every 2-3 seconds)

### Summaries Tab

- [ ] **SUMM-01**: User can view a list of all completed summaries with video title and date
- [ ] **SUMM-02**: User can expand a summary to view the full structured content
- [ ] **SUMM-03**: User can copy a summary to clipboard
- [ ] **SUMM-04**: User can export a summary as markdown file
- [ ] **SUMM-05**: User can delete individual summaries

### Settings Tab

- [ ] **SETT-01**: User can upload a cookies.txt file via drag-and-drop or file picker
- [ ] **SETT-02**: User can see cookie status (loaded / not loaded) and clear cookies
- [ ] **SETT-03**: User can configure LLM model selection and custom prompt
- [ ] **SETT-04**: User can see Claude OAuth authentication status

### Frontend Core

- [x] **CORE-01**: Tab navigation works with Pico CSS dark theme default
- [x] **CORE-02**: Responsive layout using semantic HTML and Pico CSS patterns
- [x] **CORE-03**: All API calls use fetch() with proper error handling and loading states

## v2 Requirements

Deferred to future release. Tracked but not in current roadmap.

### Enhanced Features

- **ENH-01**: Theme toggle (dark/light) in nav bar
- **ENH-02**: SSE-based real-time queue updates (replace polling)
- **ENH-03**: Test Cookies button to probe members-only access
- **ENH-04**: Multi-provider LLM support (OpenAI, Google, Ollama)
- **ENH-05**: Estimated time remaining for processing jobs
- **ENH-06**: Worker settings UI (processing mode, batch size)

## Out of Scope

| Feature | Reason |
|---------|--------|
| Docker deployment | Local-only for MVP |
| Mobile app | Web-first |
| User accounts / multi-user | Single-user self-hosted |
| Real-time chat | Not relevant to summarization |
| Video playback in UI | Summarization tool, not a player |

## Traceability

Which phases cover which requirements. Updated during roadmap creation.

| Requirement | Phase | Status |
|-------------|-------|--------|
| BACK-01 | Phase 1 | Complete |
| BACK-02 | Phase 1 | Complete |
| BACK-03 | Phase 1 | Complete |
| BACK-04 | Phase 1 | Complete |
| CORE-01 | Phase 2 | Complete |
| CORE-02 | Phase 2 | Complete |
| CORE-03 | Phase 2 | Complete |
| BRWS-01 | Phase 3 | Pending |
| BRWS-02 | Phase 3 | Pending |
| BRWS-03 | Phase 3 | Pending |
| BRWS-04 | Phase 3 | Pending |
| BRWS-05 | Phase 3 | Pending |
| QUEU-01 | Phase 4 | Pending |
| QUEU-02 | Phase 4 | Pending |
| QUEU-03 | Phase 4 | Pending |
| QUEU-04 | Phase 4 | Pending |
| QUEU-05 | Phase 4 | Pending |
| SUMM-01 | Phase 5 | Pending |
| SUMM-02 | Phase 5 | Pending |
| SUMM-03 | Phase 5 | Pending |
| SUMM-04 | Phase 5 | Pending |
| SUMM-05 | Phase 5 | Pending |
| SETT-01 | Phase 6 | Pending |
| SETT-02 | Phase 6 | Pending |
| SETT-03 | Phase 6 | Pending |
| SETT-04 | Phase 6 | Pending |

**Coverage:**
- v1 requirements: 22 total
- Mapped to phases: 22
- Unmapped: 0

---
*Requirements defined: 2026-03-29*
*Last updated: 2026-03-29 after roadmap creation*
