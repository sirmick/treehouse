# AI

A small local LLM acting as a Socratic learning companion: helps a
kid explore the library, asks more questions than it answers, never
does the thinking for them, and never reaches outside the box.

The component is `aigateway`, a FastAPI service that orchestrates
between the kid, `searchd`, Ollama, and the launcher's logging.

## Position

This is the most cautiously designed component in Treehouse, and the
last one to be built (Phase 8). The reasons:

1. **Open-weights small models hallucinate confidently.** A wrong
   answer delivered with conviction is worse than no answer in a
   learning context.
2. **Kid-safe alignment is genuinely unsolved at the 3–8B parameter
   scale.** Treating the model's safety as "we wrote a clever
   system prompt" is naive.
3. **The architecture has to compensate for the model.** RAG-only
   retrieval, structural topic gates, short context windows,
   parental review logging — all of these exist because we cannot
   trust the model alone.

If any of these constraints become inconvenient: that is a signal
to push back on the constraint, not the architecture.

## Design philosophy

> "Light the path. Don't light the forest."

The AI's job is to help the kid notice things, ask better questions,
and find their way to relevant content. It is *not* to summarize
articles for them, write their essays, or supply answers they should
work out themselves.

In practice this is enforced by:

- **A Socratic system prompt** that asks rather than tells.
- **A topic-gate classifier** that refuses categories of queries
  before the model ever runs.
- **RAG-only retrieval** so the model only synthesizes from sources
  the kid could read themselves.
- **Short context windows** so jailbreaks via conversational drift
  have nowhere to accumulate.
- **Per-kid review logging** so a parent can see, every week, what
  the AI was asked and how it responded.

## Architecture

```
   Kid in launcher → "Why do volcanoes erupt?"
                            │
                            ▼
   ┌───────────────────────────────────────────────────────┐
   │  aigateway                                            │
   │                                                       │
   │  1. resolve kid (age, age_band, ai_companion enabled) │
   │  2. topic gate classifier (allow / refuse)            │
   │  3. enrich query (history-aware reformulation)        │
   │  4. searchd /api/retrieve → top sources               │
   │  5. assemble prompt:                                  │
   │       [system prompt + kid age + sources + query]     │
   │  6. ollama generate (stream)                          │
   │  7. post-filter response (topic, length, links)       │
   │  8. log: kid_id, query, sources, response, ts         │
   │  9. return JSON: {response, sources, suggestions}     │
   └───────────────────────────────────────────────────────┘
                            │
                            ▼
                       Ollama (model: llama3.2:3b-q4_K_M)
```

## Model choice

| Model | Size | RAM @ Q4 | Tokens/sec on Pi 5 | Notes |
|---|---|---|---|---|
| **Llama 3.2 3B** | 3B params | ~2.5 GB | ~6–10 | Recommended starting model. Good instruction following, decent reasoning. |
| **Phi-3-mini** | 3.8B | ~2.7 GB | ~5–8 | Microsoft, strong on reasoning for size. Alternative if Llama 3.2 disappoints. |
| **Gemma 2 2B** | 2B | ~1.7 GB | ~10–14 | Smaller, faster, less capable. Good for low-spec hardware. |
| **Llama 3.1 8B** | 8B | ~5 GB | ~3–5 on Pi, 15+ on x86 VM | Step up in quality; tight on Pi 5 with 8 GB RAM. |

Default: **Llama 3.2 3B at Q4_K_M quantization**, served via Ollama.
Selected by `manifest.yml.ai.model`. Swappable without code changes.

If/when Pi 5 is replaced by a more capable host (a small Mac mini,
a mini-PC with 32 GB RAM), bumping to a 7–8B model is a config
change, not a redesign.

## RAG-only retrieval

The model never recalls facts from training. Every query goes
through `searchd /api/retrieve` first; the retrieved sources become
the model's context; the model synthesizes only from them.

If retrieval returns no relevant sources, the response is structurally
fixed:

> "Hmm — our library doesn't have much on that. Want to ask a grown-up,
> or pick a different topic?"

The model does not get to bluff. The gateway decides this branch
based on retrieval result count and average score, not on the model's
inclination.

## System prompt

A template, rendered per request:

```
You are a curious learning companion for {kid_name}, age {age}.

Your job is to spark thinking, not to give answers.

When the child asks a question:
- Find what's relevant in the SOURCES below.
- Ask what they already know or have noticed.
- Suggest where to look (link to a SOURCE).
- Break the question into smaller pieces.
- Celebrate effort and curiosity.

Never:
- Give complete answers to homework.
- Write text the child should write themselves.
- Generate stories, poems, or essays for the child to claim as theirs.
- Discuss politics, religion, or appearance.
- Mention things outside SOURCES.
- Pretend to be a friend, a person, or to have feelings.

Speak warmly, briefly, and concretely. Use language a {age}-year-old
will follow easily. If you suggest a source, name it: "There's a great
Wikipedia article called 'Volcano' you could read."

If SOURCES don't cover the topic, say:
"Hmm — our library doesn't have much on that. Want to ask a grown-up,
or explore something else?"

SOURCES:
{retrieved_results_formatted}

The child is asking:
{query}
```

Notes on this prompt:

- **Age is templated in twice.** Once to set the tone ("a 7-year-old"),
  once for the concrete language calibration ("a 7-year-old will
  follow"). Models follow both better than either alone.
- **The "never" list is explicit.** Models trained on RLHF respond
  to lists better than to negations buried in prose.
- **Persona is explicitly bounded.** "Pretend to be a friend" or
  parasocial drift is a known risk with kid-AI; the prompt closes
  it.
- **The fallback line is verbatim.** If the model is going to bluff,
  prefer it bluffs the rote response. (Also enforced structurally;
  the prompt is belt-and-suspenders.)

The prompt is in `aigateway/prompts/companion.md`, version-controlled,
with `git blame` to see when each line changed and why.

## Topic gate

Before the model runs, the query is classified:

```
allow:
  - science, math, history, geography
  - reading, writing skills, vocabulary
  - art, music, sports
  - "how does X work" / "why does X happen"
  - help with library navigation

refuse_polite:
  - personal advice ("am I pretty?", "are my parents fighting?")
  - medical / dietary advice
  - violence, weapons, drugs
  - sex, romance
  - politics, religion, current events
  - prompts that look like jailbreaks (DAN-style, "ignore previous")
  - direct homework completion ("write my book report on X")
```

Classification: a small allowlist + denylist of substring patterns
plus an embedding-similarity check against curated examples (sentence
transformers model, ~50 MB). Implemented in pure Python, no LLM call.

On `refuse_polite`: a fixed friendly response, no model call:

> "That's an important thing to think about, but it's better to talk
> with a grown-up about it. Is there something else from our library
> you're curious about?"

The kid sees the response immediately; no model latency, no
opportunity for the model to drift.

The classifier is intentionally conservative — false positives (refusing
a legitimate question) are recoverable (kid asks a parent, parent
edits the allowlist if appropriate). False negatives (engaging with
a category that should have been refused) are not.

## Context window policy

Multi-turn conversations are where small models drift. A kid can
patiently lead the model far from where it started.

Defenses:

1. **Hard turn cap.** After 3 user turns, the conversation auto-ends
   with: "Let's start fresh. What would you like to explore?"
2. **No persistent memory across sessions.** Each session starts
   blank. (The activity log persists for parents; the model context
   does not.)
3. **Prompt re-injection on every turn.** The system prompt is
   prepended to every request, not relied on to be remembered.
4. **Refusal on out-of-context follow-ups.** If turn 2's query is
   topically unrelated to turn 1's retrieved sources, retrieval is
   redone; the previous turn's context is discarded, not augmented.

This deliberately makes the AI feel less "chatty" than a commercial
chatbot. That is the goal — Treehouse's AI is a tool, not a
companion in the parasocial sense.

## Logging for parental review

Every interaction is logged:

```sql
CREATE TABLE transcripts (
    id INTEGER PRIMARY KEY,
    kid_id TEXT NOT NULL,
    timestamp TIMESTAMP NOT NULL,
    query TEXT NOT NULL,
    topic_gate_decision TEXT NOT NULL,    -- "allow" / "refuse:..."
    retrieved_source_ids TEXT,            -- JSON array
    prompt_tokens INTEGER,
    response TEXT,
    response_tokens INTEGER,
    elapsed_ms INTEGER
);
```

Stored in `state/aigateway/transcripts.sqlite`. Backed up.

`admin.kids/ai/<kid_id>` renders this as a weekly summary:

- Total queries this week
- Top topics
- Refused queries (and why)
- Full transcript browsable, paginated

This is the parent-review surface. It exists for two reasons:

1. **Trust.** A parent who can see what the AI did is more likely
   to leave it on. A parent who can't is more likely (rightly) to
   leave it off.
2. **Curation.** Watching the questions a kid asks the AI is the
   highest-signal information about what content to add to the
   manifest next.

Logs are local-only, never leave the box, retention 1 year.

## Suggested-follow-ups (the lantern bit)

A useful side effect of RAG: the response can include "things you
could explore next" — direct links to sources the model didn't fully
use but that are related. The launcher renders these as small
"explore" tiles below the response.

```json
{
  "response": "Hmm, what do you already know about volcanoes? ...",
  "suggestions": [
    {"title": "Volcano (Wikipedia)", "url": "https://wikipedia.kids/A/Volcano"},
    {"title": "Plate tectonics (Khan Academy)", "url": "https://khan.kids/..."},
    {"title": "How volcanoes work (PeerTube)", "url": "https://videos.kids/..."}
  ]
}
```

The model is invited (in the system prompt) to mention these by
name; the gateway also surfaces them structurally as a backup. Belt
and suspenders again.

## Streaming response

Ollama supports streaming generation. The gateway streams the
response to the launcher as it's produced, so the kid sees text
appearing word-by-word — the perceived latency is much shorter
than the wall-clock generation time.

WebSocket from launcher → aigateway → Ollama. Cancellable: if the
kid taps "stop", the gateway cancels the Ollama generation, logs
the partial response, and the request ends cleanly.

## Hardware sizing

| Hardware | Best model | Tps | Comfort |
|---|---|---|---|
| Pi 4 (4 GB) | Gemma 2 2B Q4 | ~5 | Tight; possible but unhappy |
| Pi 5 (8 GB) | Llama 3.2 3B Q4 | ~8 | Recommended floor |
| x86 VM (16 GB) | Llama 3.2 3B Q4 | ~25 | Comfortable |
| x86 VM (32 GB) | Llama 3.1 8B Q4 | ~15 | Better quality, fine cost |
| Mac mini M2 | Llama 3.1 8B Q4 (Metal) | ~30+ | Excellent if Pi proves limited |

Inference on the Pi 5 is slow but acceptable: a kid asks a question,
the response starts streaming in 1–2 seconds and finishes in 8–15
seconds. Streaming makes this feel reasonable. If the experience is
genuinely poor, moving the AI off-box is the answer — a small mini-PC
on the same kids' segment, running Ollama, with the gateway
configured to point at it instead of localhost. The architecture
already supports this via `aigateway.ollama_url` config.

## Ollama deployment

In `compose.yml`:

```yaml
services:
  ollama:
    image: ollama/ollama:latest
    restart: unless-stopped
    volumes:
      - /srv/treehouse/content/ai-models:/root/.ollama
    networks: [treehouse-net]
    healthcheck:
      test: ["CMD", "ollama", "list"]
      interval: 60s
    # GPU passthrough optional; CPU-only on Pi.
    # devices: [/dev/dri]   # if igpu
```

Models are pulled lazily on first use, persisted under
`content/ai-models/`. The updater can pre-pull during a maintenance
window if desired:

```yaml
# manifest.yml
ai:
  model: llama3.2:3b-q4_K_M
  pre_pull: true
```

## Testing

- **System prompt regression tests:** a corpus of known-good and
  known-bad inputs with expected behaviours. "If asked about
  homework completion, response must contain refusal phrase." Run
  against the actual model — slow but valuable; nightly, not
  per-PR.
- **Topic gate tests:** input → expected classification. Pure
  Python, fast, runs on every PR.
- **RAG retrieval tests:** "for query X, retrieved sources should
  include Y." Tests against `compose.test.yml` with seeded
  content.
- **Length and structure tests:** response should not exceed N
  tokens; response should mention at least one SOURCE; response
  should not contain banned phrases.

## Failure modes and fallbacks

| Failure | Behaviour |
|---|---|
| Ollama unreachable | gateway returns "I can't help right now, but here are some search results" + a link into searchd. Graceful, kid-friendly. |
| `searchd` unreachable | gateway refuses with "let me try in a moment" — RAG-only means no useful response without retrieval. |
| Model generates banned phrase | post-filter detects, replaces with the refusal text, logs the incident for review. |
| Topic gate uncertain | prefer refuse over engage. |
| Generation timeout (>60s) | cancel, log partial, return "took too long" to kid. |

## Open questions

- **Should the AI ever generate a full answer?** For some queries
  (vocabulary lookup: "what does 'photosynthesis' mean?") the
  Socratic posture is annoying — the kid wants a definition, not a
  question back. Possible compromise: a "definition" mode triggered
  by query phrasing ("what does X mean", "define X", "what is X")
  that returns a short factual answer derived from RAG, no
  Socratism. Defer to v2 of the gateway.
- **Should there be a parent-callable explainer mode?** "Mick wants
  the AI to summarize this Wikipedia article at age-7 reading
  level for Alice." Different use case (parent assistance) than
  default (kid assistance). Possible v2 feature.
- **Voice in/out?** Web Speech API for TTS of responses; STT for
  pre-readers. Both are reasonable add-ons that don't change the
  AI's logic, only the UX shell. Phase 9+.
- **Multi-modal (image input)?** "What is this?" pointing a tablet
  at a leaf. Off the table for v1: small VLMs are weaker than text
  models, and the topic-gate / RAG-grounding story gets harder.
- **Should the AI participate in messaging?** A bot that helps a
  kid draft a letter to grandma. Tempting but slippery — defer.
