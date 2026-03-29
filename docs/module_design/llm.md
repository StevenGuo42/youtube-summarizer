# llm — `app/services/llm.py`

LLM client for generating video summaries using Claude via the Agent SDK.

## Responsibilities

- Send transcript + keyframe images to Claude for summarization
- Support custom prompts (user-defined or default)
- Return structured summary (title, TL;DR, full summary)
- Check Claude authentication status

## Key Design Decisions

- **Claude-only via Agent SDK** — uses `claude-agent-sdk` which bundles the Claude Code CLI as a native binary (no Node.js required for the SDK itself)
- **Auth via OAuth** — user logs in with their Claude Max plan via `claude auth login`; the SDK picks up stored credentials automatically from `~/.claude/.credentials.json`
- **Keyframe viewing** — Claude reads keyframe images from disk via the Agent SDK's built-in `Read` tool (supports image files natively), so images don't need base64 encoding
- **Transcript grouped by keyframes** — transcript segments are merged into blocks bounded by keyframe timestamps. Each block starts with `[KEYFRAME: path]` followed by the merged transcript for that section. This gives Claude natural topic boundaries matching visual changes on screen.
- **Prompt includes**: video metadata (title, channel, duration), keyframe-grouped timestamped transcript
- **Custom prompt support** — stored in `llm_settings.custom_prompt`; if set, overrides the default system prompt
- **JSON output** — default prompt asks Claude to return `{"title", "tldr", "summary"}` as JSON; parser handles code blocks and falls back to raw text on parse failure
- **Model selection** — configurable via `llm_settings.model`, defaults to `claude-sonnet-4-20250514`

## Interface

```python
class KeyframeMode(str, Enum):
    IMAGE, OCR, OCR_IMAGE, OCR_INLINE, OCR_INLINE_IMAGE, NONE

@dataclass
class SummaryResult:
    raw_response: str
    title: str
    tldr: str
    summary: str

async def summarize(transcript: TranscriptResult, keyframes: list[KeyFrame], video_meta: dict, custom_prompt: str | None = None, model: str | None = None, keyframe_mode: KeyframeMode = KeyframeMode.IMAGE, ocr_paths: list[Path | None] | None = None) -> SummaryResult
async def get_auth_status() -> dict
async def get_llm_settings() -> dict
```

## Transcript Format

Transcript segments are grouped by keyframe boundaries and sent to Claude as:

```
[KEYFRAME: /path/to/frame1.png]
[0:00 - 0:58] Merged transcript text for this section...

[KEYFRAME: /path/to/frame2.png]
[0:58 - 1:55] Merged transcript text for next section...
```

Segments before the first keyframe form their own group without a keyframe header.
Without keyframes, all segments merge into a single timestamped block.

## Keyframe Modes

Controlled by `KeyframeMode` enum, derived from CLI flags `--no-keyframes` and `--ocr {none,file,inline}`:

| Mode | Images? | OCR? | `Read` tool? |
|---|---|---|---|
| IMAGE | `[KEYFRAME: path.png]` | No | Yes |
| OCR | No | `[OCR: path.txt]` | Yes |
| OCR_IMAGE | `[KEYFRAME: path.png]` | `[OCR: path.txt]` | Yes |
| OCR_INLINE | No | Injected into transcript | No |
| OCR_INLINE_IMAGE | `[KEYFRAME: path.png]` | Injected into transcript | Yes |
| NONE | No | No | No |

The `Read` tool is only enabled when Claude needs to read files from disk.

## Auth Flow

1. User runs `claude auth login` (or is already logged in from Claude Code usage)
2. `GET /api/settings/auth/claude` checks status via bundled CLI (`claude auth status`)
3. `query()` calls use stored credentials automatically

## Dependencies

- `claude-agent-sdk` (bundles Claude Code CLI binary)
- `app.database` for reading LLM settings
- `app.services.keyframes` for `KeyFrame` type
- `app.services.transcript` for `TranscriptResult` type
