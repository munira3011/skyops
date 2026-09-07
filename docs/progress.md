# Build progress

Read this before making changes. Update it at the end of every session.

## Day 1
- Scaffolded `backend/`, `frontend/`, `litellm-proxy/` as independent uv projects.
- Added mocked data: `backend/app/data/{flights.json,pnr.json}` and
  `backend/app/data/policies/*.md` (4 policy docs).

## Day 2 (done)
- Fixed structure drift: moved `backend/data/*` -> `backend/app/data/*` to match
  CLAUDE.md; removed stray root `main.py`/`pyproject.toml`/`.python-version`
  left over from initial scaffolding.
- Defined `AgentState`, `Flight`, `Booking` in `backend/app/graph/state.py`,
  matching the mocked data shapes exactly (verified against the JSON files).
- Built the graph:
  - `backend/app/graph/nodes/flight_status_agent.py` - looks up
    `booking.flight_number` or a flight number regex-matched from the latest
    message against `flights.json`.
  - `backend/app/graph/supervisor.py` - keyword/regex intent routing (flight
    status vs. policy vs. no match); ends the turn once the last message is
    an AIMessage. Marked as a placeholder for LLM-based classification once
    nodes are wired to the gateway.
  - `backend/app/graph/graph.py` - `StateGraph` wiring supervisor <-> each
    specialist in `_SPECIALISTS`, routed via `next`. Includes a dev REPL
    (`uv run python -m app.graph.graph`).
- Built RAG for policy docs:
  - `backend/app/rag/ingest.py` - chunks the 4 policy markdown docs by `##`
    heading, upserts into a persistent Chroma collection
    (`backend/chroma_db/`, gitignored).
  - `backend/app/rag/retriever.py` - semantic query against that collection,
    with a distance cutoff (`_MAX_DISTANCE = 1.6`) so off-topic/gibberish
    queries correctly return no match instead of a confident wrong answer.
  - `backend/app/graph/nodes/rag_policy_agent.py` - calls the retriever,
    returns the matched chunk + citation. Wired into `supervisor`/`graph.py`
    alongside `flight_status_agent`.
- Set up LangGraph Studio for visual testing: `backend/langgraph.json` +
  `langgraph-cli[inmem]` dev dependency. Verified via the local API
  (`langgraph dev`, confirmed routing + state updates over HTTP).
- Built the LLM gateway path (not yet used by any node):
  - `backend/app/gateway/client.py` - `chat()`/`get_chat_model()` wrapping
    `ChatOpenAI` pointed at the LiteLLM proxy via `LITELLM_BASE_URL`/
    `LITELLM_API_KEY`/`LITELLM_MODEL` env vars (never a provider directly,
    per CLAUDE.md). `backend/.env.example` documents these.
  - `litellm-proxy/config.yaml` - `primary` (openai/gpt-4o-mini) with
    `fallback` (anthropic/claude-3-5-haiku-20241022), cross-provider so a
    single provider outage doesn't take the assistant down. Verified the
    fallback chain actually triggers (tested with dummy keys - primary auth
    fails, router retries fallback, reports both). `litellm-proxy/.env.example`
    added.
  - Fixed a pre-existing bug: `litellm-proxy/.python-version` and
    `pyproject.toml` said Python `3.1` (should be `3.12`, matching the other
    two projects) - this broke `uv run` outright. Corrected both.
- Known rough edges, not blocking:
  - `rag/ingest.py` chunks only on `##` headings, so a `##` section with
    multiple `###` sub-headings (e.g. loyalty tier benefits by tier) returns
    as one chunk - a "platinum benefits" query can surface the Silver
    sub-section first. Revisit chunking granularity if this matters.
  - LiteLLM's proxy returns a 500 (not 401) for a request with zero
    `Authorization` header, because its error handler imports the optional
    `prisma` package (not installed, no DB configured) to check for a DB
    error. Doesn't affect us - `gateway/client.py` always sends a key.
  - Windows-specific: both `langgraph-cli` and `litellm`'s own startup
    banners crash under the default `cp1252` console encoding; run with
    `PYTHONUTF8=1` (or `setx PYTHONUTF8 1` once, persistently).

## Day 3 (in progress)
- Wired `gateway/client.py` into both existing nodes, LLM-first with graceful
  fallback to the previous deterministic logic if the gateway is unavailable
  (`GatewayError`):
  - `backend/app/graph/supervisor.py` - `_llm_classify` asks the model to
    pick one of `flight_status_agent`/`rag_policy_agent`/`none` from the
    latest message; falls back to the old keyword/regex `_keyword_classify`
    on `GatewayError`. `handoff_reason` now records which path was taken
    (e.g. `"llm classified as ..."` vs. `"gateway unavailable (...), keyword
    fallback matched ..."`).
  - `backend/app/graph/nodes/rag_policy_agent.py` - `_synthesize` asks the
    model to answer the user's question using only the retrieved chunk's
    text (explicitly told not to add outside info); falls back to returning
    the raw chunk + citation on `GatewayError`, same as before.
  - Verified: full graph run with no gateway configured (confirms no
    regression - same routing/answers as Day 2, `handoff_reason` shows
    fallback used); mocked `chat()` to verify the LLM-response parsing and
    prompt-building code paths for both nodes.
  - Not yet verified: a real end-to-end completion through a live
    `litellm-proxy` with real `OPENAI_API_KEY`/`ANTHROPIC_API_KEY` - no real
    keys available in the dev environment used so far. Do this once you have
    keys: run the proxy, set `LITELLM_BASE_URL`/`LITELLM_API_KEY` in
    `backend/.env`, re-run `uv run python -m app.graph.graph`.

- Built and wired `booking_disruption_agent.py`:
  - Reads `booking`/`flight` from state, or looks up a PNR
    (`[A-Z0-9]{5,7}`) in the latest message against `pnr.json`, then
    `flights.json` for that booking's flight.
  - Applies `rebooking_policy.md`: eligible only if cancelled or delayed
    >90min; compensation only for causes classified within airline control
    (`technical_inspection`, `crew_scheduling`) per the delay-length tiers
    (3-6h/$150, 6-12h/$300, >12h-or-cancelled/$500); `requires_approval`
    when compensation is strictly above $300, matching policy's "above 300
    USD" wording (so the $300 tier itself does NOT require approval).
  - Does not evaluate the policy's other two approval triggers (cabin-class
    change, partner-airline rebooking) - this node only ever proposes a
    same-cabin Zenith Air rebooking, documented in a code comment.
  - Sets `pending_approval` (or clears it to `None`) - not yet consumed by
    anything since `human_approval` doesn't exist yet.
  - Verified standalone against all 6 mocked PNRs plus two synthetic
    bookings/flights to exercise the >$300-approval and delay-tier paths
    (none of the mocked flights have delay_minutes >= 180, so real data
    alone can't reach the compensation tiers above $0/eligible-no-comp).
  - Wired into `graph.py` (`_SPECIALISTS`) and `supervisor.py`: added as a
    third LLM-classifier route (distinguished from `rag_policy_agent` as
    "a passenger with a specific booking asking what happens to them" vs.
    general policy questions), plus a keyword-fallback path
    (`rebook`/`compensation`/`disrupted`/etc., checked before the flight-
    number check so "my flight ZX431 was delayed, what happens now" routes
    to disruption, not status). Verified full graph runs end-to-end via the
    keyword fallback (no gateway configured) for all three specialists.

- Built and wired `human_approval.py`:
  - Reads `pending_approval`; calls LangGraph's `interrupt()` with the
    approval payload (pnr/flight/compensation/reason), pausing the graph
    until resumed with `Command(resume={"approved": bool, "agent_note":
    Optional[str]})`. Writes the approve/reject outcome as an AIMessage and
    clears `pending_approval` back to `None`.
  - `graph.py`: `booking_disruption_agent` now routes conditionally
    (`_route_from_disruption`) to `human_approval` when it set
    `pending_approval`, else straight back to `supervisor` as before;
    `human_approval` -> `supervisor`.
  - `interrupt()`/resume requires a checkpointer. The module-level `graph`
    (used by `langgraph.json`/Studio) is compiled with none, since
    Studio/the platform injects its own automatically - passing one
    ourselves there would conflict. `build_graph()` now takes an optional
    `checkpointer` param; the dev REPL (`uv run python -m app.graph.graph`)
    passes an `InMemorySaver` and drives the interrupt/resume loop
    (`__interrupt__` in the invoke result -> prompt for y/n -> resume).
  - Verified: full interrupt -> resume(approved) -> confirmed-reply flow,
    interrupt -> resume(rejected) -> rejection-reply flow, and confirmed a
    normal turn (no approval needed) doesn't interrupt at all. Also
    re-verified the unchanged module-level `graph` (no checkpointer, what
    Studio actually loads) still compiles and runs normal turns.
  - Not yet tested inside LangGraph Studio itself (`langgraph dev`) -
    should verify the interrupt surfaces correctly through Studio's UI,
    since that's the real path this was built for.

- Verified real LLM calls end-to-end through a live `litellm-proxy` with
  real `OPENAI_API_KEY`/`ANTHROPIC_API_KEY` (both added to
  `litellm-proxy/.env` and `backend/.env` outside this session):
  - Found and fixed a real bug this surfaced: `config.yaml`'s fallback
    model, `anthropic/claude-3-5-haiku-20241022`, is deprecated/retired -
    Anthropic returns a 404 `not_found_error` for it now. Replaced with
    `anthropic/claude-haiku-4-5-20251001` (current fast Claude model).
  - Verified with real keys: a direct `chat()` call to `primary`; a direct
    call to `fallback`; `supervisor._llm_classify` correctly routing all
    three specialists plus the no-match case; `rag_policy_agent._synthesize`
    producing a grounded, cited answer; the full graph end-to-end for all
    three specialist paths; and - using a throwaway proxy instance with a
    deliberately invalid primary key - that a `primary` request genuinely
    falls through to `fallback` and returns a real completion (not just the
    dummy-key auth-failure fallback test from Day 2).
  - The port-4000 `litellm-proxy` instance from this session is left
    running for continued local dev (`curl localhost:4000/health/readiness`
    to check; started via `PYTHONUTF8=1 uv run litellm --config config.yaml
    --port 4000` from `litellm-proxy/`).

- Switched `supervisor`'s LLM classification from freeform-text parsing to
  Pydantic-validated structured output:
  - `gateway/client.py` - added `chat_structured(messages, schema, model=None)`,
    using `.with_structured_output(schema)` (tool-calling) instead of raw
    `.invoke()`; raises `GatewayError` on proxy failure or a non-conforming
    reply, same error contract as `chat()`.
  - `supervisor.py` - added `_RouteDecision(BaseModel)` with
    `route: Literal[*_ROUTES, "none"]`; `_llm_classify` now calls
    `chat_structured(...)` and reads `.route` directly, replacing the old
    `token = reply.content.strip().lower(); route in token` substring check.
    Reworded `_CLASSIFIER_PROMPT` slightly since it no longer needs to ask
    for "exactly one word" - the schema enforces the shape now.
  - Verified with the live proxy: all three specialist routes plus the
    no-match case classify correctly via structured output; confirmed the
    keyword-fallback path (`GatewayError`) and full graph are unaffected.
  - Not yet applied elsewhere - `rag_policy_agent._synthesize` intentionally
    stays freeform prose (not state), but the upcoming `input_guard`/
    `output_guard` nodes are good candidates for the same pattern (e.g. a
    `GuardrailVerdict(flagged: bool, reason: str)` schema).
  - **Flagging, not fixing**: while reading `supervisor.py` for this change,
    found `_keyword_classify` no longer checks `_DISRUPTION_KEYWORDS` before
    routing (the disruption-agent keyword-fallback wiring from earlier this
    session is gone - `_DISRUPTION_KEYWORDS` is defined but unused, and the
    function only returns `flight_status_agent`/`rag_policy_agent`/`None`).
    Didn't touch it since it wasn't part of this task and the file had
    changed outside this session - flagging for a decision on whether to
    restore the disruption branch.

- Restored the flagged `_keyword_classify` regression: it now checks
  `_DISRUPTION_KEYWORDS` first and routes to `booking_disruption_agent`
  again, matching the LLM classifier's three routes. Reverified via the
  keyword-fallback path (rebook/status/policy phrasing all route correctly).

- Built and wired `input_guard.py`/`output_guard.py` (Day 3's last item),
  both using `chat_structured` + a shared `GuardrailVerdict` schema
  (`guardrails/schema.py`: `flagged: bool`, `category: Literal[...]`,
  `reason: str`) rather than free-text parsing:
  - `input_guard` (new graph entry point, `START -> input_guard`): flags
    `prompt_injection`, `abusive_or_toxic`, `pii_or_social_engineering` in
    the latest user message; on a flag, returns a refusal `AIMessage` and
    short-circuits straight to `END` (via `_route_from_input_guard`,
    reusing the existing `isinstance(messages[-1], AIMessage)` idiom from
    `supervisor` instead of overloading `next`); otherwise no-ops through
    to `supervisor`.
  - `output_guard` (new node between `supervisor`'s end-of-turn and `END`):
    flags `internal_leak`/`abusive_or_toxic` in the draft AI reply; on a
    flag, appends a safe replacement `AIMessage` (overrides what's shown,
    since consumers read `messages[-1]`, no `RemoveMessage` needed).
  - Both have a deterministic keyword-fallback path on `GatewayError`,
    matching the existing supervisor/rag_policy_agent convention.
  - Added `guardrail_flags: Annotated[list[str], operator.add]` to
    `AgentState` (`state.py`) - CLAUDE.md had already anticipated this
    field/reducer before any guardrail code existed. Confirmed the
    `operator.add` channel initializes correctly with no explicit starting
    value (first node call that appends to it works with no error).
  - Found and fixed a real false positive during live testing:
    `output_guard` initially flagged `rag_policy_agent`'s citation format
    (`(Source: loyalty_tiers, section "...")`) as an `internal_leak` -
    reproducible 4/4 times against the real synthesized reply. Root cause:
    the prompt's "reveals internal instructions" wording was ambiguous
    enough that the model read "cites a policy doc" as "leaks internal
    docs." Fixed by explicitly telling the guardrail that citing which
    Zenith Air policy doc an answer came from is expected/required and
    must never be flagged, only the assistant's own
    system-prompt/model/provider details count as a leak. Reverified 4/4
    clean afterward, and that a genuine leak (mentioning gpt-4o-mini/
    LiteLLM/its own system prompt) is still caught.
  - Verified end-to-end via the live proxy: clean flight/policy/disruption
    queries pass through untouched; a roleplay jailbreak and an indirect
    PII request (phrasing a keyword filter would miss) are both correctly
    blocked by `input_guard`; `human_approval`'s interrupt/resume still
    works with the guard nodes now wrapping the graph. Also reverified the
    keyword-fallback path and the unchanged Studio-facing module-level
    `graph`.
  - Also verified real abusive/threatening input against the live LLM
    check (not just the keyword fallback): both correctly flagged, a
    benign flight-status question stays clean.

- Tested `human_approval`'s interrupt inside actual LangGraph Studio
  (`langgraph dev`), not just the manually-checkpointed dev graph:
  - **Found and fixed an environment problem, not a code bug**: a
    `langgraph dev --port 2024` process from a previous session (2026-09-02)
    was still running. Starting a new one today bound to the same port
    2024 without erroring (Windows allowed both sockets to listen), so
    requests were routing unpredictably between old/new code - the first
    test run showed a routing decision (`"keyword match routed to..."`)
    that doesn't exist anywhere in the current codebase, which is what
    exposed this. Killed the stale process tree; confirmed via `netstat`
    that only one process listens on 2024 afterward, and that the
    assistant's `created_at` matches today's server start. Worth killing
    any leftover `langgraph dev`/`litellm` background processes at the
    start of a session if routing looks stale.
  - Verified via the real LangGraph API (the same one Studio's UI calls -
    `POST /threads`, `/threads/{id}/runs/wait` with `input`, then again
    with `command: {"resume": {...}}`), using a synthetic booking/flight
    passed directly in the run input to hit the >$300 approval tier (no
    mocked flight reaches it): the interrupt fires with the correct
    `pending_approval` payload under Studio's own platform-managed
    checkpointer (not the dev-REPL's manual `InMemorySaver`); resuming
    with `{"approved": true, "agent_note": ...}` produces the confirmed
    reply and clears `pending_approval`; a second thread resumed with
    `{"approved": false, ...}` produces the rejection reply. Both match
    the earlier manually-checkpointed test exactly.
  - The `langgraph dev` server (port 2024) and the `litellm-proxy`
    instance (port 4000) from this session are both left running for
    continued local dev/Studio exploration - open
    `https://smith.langchain.com/studio/?baseUrl=http://127.0.0.1:2024` in
    a browser to see the same runs visually (thread IDs from this test:
    see terminal history, or just start a new one from the UI).
  - Also noted in passing: `langgraph-api` 0.7.27 (currently pinned) is
    flagged End-of-Life by its own startup banner, two-plus minor versions
    behind (0.13.x latest) - not blocking, but worth a `uv add
    --dev langgraph-cli[inmem]` version bump at some point.

## Day 4 (done)
- Added `fastapi`/`uvicorn[standard]` via `uv add` and re-exported
  `requirements.txt`.
- `app/graph/runtime.py` - one shared, checkpointed (`InMemorySaver`) graph
  instance for the whole API process. The existing module-level `graph` in
  `graph.py` stays checkpointer-less (reserved for Studio, which injects
  its own), so this is a separate instance `routes/chat.py` and
  `routes/ops.py` both invoke/resume against - they have to share it,
  since an approval started via chat must be resumable via ops.
- `app/middleware/`:
  - `auth.py` - `X-API-Key` (== `SKYOPS_API_KEY`) required on every route
    except `/health`/docs; `/ops/*` additionally requires `X-Ops-API-Key`
    (== `SKYOPS_OPS_API_KEY`), so a leaked customer-chat key alone can't
    reach approvals. Both vars added to `.env.example`; dev-only random
    values generated locally into `.env` (gitignored) for testing.
  - `rate_limit.py` - fixed-window in-memory limit per client IP (30
    req/60s), single-process/resets-on-restart by design, matching the
    project's no-external-DB stance elsewhere.
  - `logging.py` - method/path/status/latency per request via a
    `skyops.request` logger.
  - `error_handler.py` - `GatewayError` -> 502, unhandled `Exception` ->
    500 with a safe generic message (logs the real traceback server-side).
    Defense-in-depth only - every node already catches `GatewayError`
    internally with a keyword fallback, so this shouldn't fire in normal
    operation.
  - `main.py` wires them in order Auth -> RateLimit -> Logging (Starlette
    executes the LAST-added middleware outermost, so Logging is added
    last to see every request/response, including ones Auth/RateLimit
    reject).
- `app/routes/chat.py` - `POST /chat/message` (`message`, optional
  `thread_id`), runs one graph turn, returns `{thread_id, reply,
  pending_approval}`. Deliberately does NOT accept a `booking`/`flight`
  override in the request body - a customer being able to inject
  arbitrary booking data would be a real auth hole.
- `app/routes/ops.py` - `GET /ops/approvals/{thread_id}` (reads
  `pending_approval`/last reply via `api_graph.get_state`), `POST
  /ops/approvals/{thread_id}/decision` (`approved`, optional
  `agent_note`) - resumes via `Command(resume=...)`, 404s if nothing is
  pending for that thread (also correctly 404s on a second decision
  attempt against an already-resolved thread).
- **Found and fixed two real bugs during live HTTP testing** (both via
  `uv run uvicorn app.main:app --port 8000` against the running
  `litellm-proxy`, plus a temporary test booking/flight added to
  `pnr.json`/`flights.json` to reach the >$300 approval tier over real
  HTTP, then removed after - fixture files verified back to their
  original 10 flights/6 bookings):
  - `human_approval.py` was appending the ops agent's `agent_note`
    directly onto the **customer-facing** reply
    (`reply += f" ({note})"`). Caught because `output_guard` correctly
    flagged a test note ("approved via HTTP API test") as an
    `internal_leak` - the guardrail was right, the bug was upstream.
    Fixed: `agent_note` is now only logged (`skyops.human_approval`
    logger), never shown to the passenger. Reverified the full approve
    flow shows the clean confirmation with no leaked note.
  - `input_guard`'s LLM check had a real, if low-rate (~1/9 in testing),
    false-positive path: a customer citing **their own** PNR
    ("My PNR is X, my flight was cancelled...") was occasionally flagged
    as `pii_or_social_engineering`. Root cause: the prompt described the
    category but never said sharing one's own PNR is normal, leaving it
    to per-sample LLM judgment. Fixed by adding an explicit example to
    `_CHECK_PROMPT`; reverified 0/15 false positives afterward and that a
    genuine other-passenger request is still caught.
  - Also found and fixed a related gap while debugging: the app never
    called `logging.basicConfig(...)`, so `LoggingMiddleware`'s output
    was being silently dropped (Python's logging drops INFO records with
    no handler configured) - `middleware/logging.py`'s log lines were
    invisible in the live server despite the middleware itself working
    correctly (confirmed via `TestClient` with a handler configured).
    Fixed in `main.py`; confirmed `skyops.request` log lines now appear
    in the live server's output.
  - Not a code bug, but a real testing gotcha worth remembering: testing
    the rate limiter via `curl http://localhost:8000/...` in a loop
    looked broken (no 429 across 35 requests) because `localhost`
    resolved inconsistently between IPv4/IPv6 loopback per-request on
    this Windows/Git-Bash setup, splitting requests across different
    rate-limit buckets. Using `127.0.0.1` explicitly (and a `TestClient`-
    based check) confirmed the middleware itself is correct (429 kicks in
    at request 31).
  - **Known limitation, not fixed (flagging for later)**: `rate_limit.py`
    keys on `request.client.host`, which is fine for local dev but would
    collapse to one shared bucket for all real users once this runs
    behind a reverse proxy (Docker/Azure Container Apps, per the Day 6
    plan) unless `X-Forwarded-For` handling is added then - revisit when
    that infra actually exists, not speculatively now.
- Verified end-to-end over real HTTP against the live `litellm-proxy`:
  health check; auth rejection (no key/wrong key -> 401, customer-key-only
  on `/ops` -> 403, both keys -> 200); multi-turn chat on one `thread_id`
  (flight status, then a policy question, context preserved); guardrail
  block via HTTP; full ops approve flow (chat triggers the $500 tier ->
  `GET` shows `pending_approval` -> `POST decision` approves -> clean
  confirmation, no leaked note -> repeat decision 404s); full ops reject
  flow (fresh thread); normal flows against the real (non-test) fixture
  data all still correct after cleanup.
- The `litellm-proxy` (port 4000), `langgraph dev` (port 2024), and this
  session's `skyops` API (port 8000, `uv run uvicorn app.main:app --port
  8000`) are all left running for continued local dev.

## Day 5 (done)
- Added `streamlit`/`requests` to `frontend/` via `uv add` and re-exported
  `requirements.txt`.
- `frontend/.env.example` - `SKYOPS_API_BASE_URL` (default
  `http://localhost:8000`) and `SKYOPS_API_KEY` (must match the same var
  in `backend/.env` - the frontend authenticates as a normal customer
  client, nothing special). `frontend/.env` populated locally by copying
  the backend's key so the two authenticate against the same server.
- `frontend/app.py` - single-page Streamlit chat UI:
  - Calls only `POST /chat/message` - never the graph/gateway directly,
    keeping the frontend a thin client over the one HTTP contract.
  - `st.session_state.thread_id`/`messages` persist across reruns within
    a browser session so multi-turn context carries via the backend's
    checkpointed thread, not frontend-side history replay.
  - Shows an `st.info` banner when a reply comes back with
    `pending_approval` set, so a customer sees their disruption request
    is awaiting a Zenith Air agent's sign-off (resolving it is still an
    ops-side action - out of scope for this customer-facing page, same
    as `routes/chat.py`'s own boundary).
  - Sidebar "New conversation" button resets `thread_id`/`messages` to
    start a fresh checkpointed thread.
  - Missing `SKYOPS_API_KEY` stops the app before rendering chat input
    (clear config error, not a confusing 401 on first message); a 4xx/5xx
    from the backend or a connection failure both render as `st.error`
    inline rather than crashing the page.
- Verified using Streamlit's `AppTest` headless testing framework (the
  correct way to exercise real script execution without a browser) against
  the live backend/proxy stack from Days 3-4, plus an actual `streamlit
  run` dev server confirmed serving real HTTP on port 8501:
  - Golden path (flight status question) end-to-end against the live
    LLM.
  - Multi-turn continuity: two messages in one session share a
    `thread_id` and the second correctly has context of the booking flow
    already routed.
  - Guardrail block renders as a normal assistant message (transparent to
    the frontend, as designed - no special-casing needed).
  - Pending-approval banner: using the same temporary-test-fixture
    technique as Day 4 (added a booking/flight that hits the $500 tier,
    removed after - fixtures reverified back to 10 flights/6 bookings),
    confirmed the `st.info` banner renders on a real `pending_approval`
    response.
  - "New conversation" button correctly resets both `thread_id` and
    `messages`.
  - Error paths: wrong API key -> inline 401 error message; unreachable
    backend -> inline connection-error message; missing API key entirely
    -> app stops before showing chat input. None of these raise an
    unhandled exception in the app.
- No bugs found this round - Day 4's guardrail/auth/logging work meant
  the backend contract the frontend needed was already solid.
- The `litellm-proxy` (4000), `langgraph dev` (2024), `skyops` API (8000),
  and this session's `streamlit run` (8501) are all left running for
  continued local dev.

## Post-Day-5 fix: added `booking_agent` (separate from `booking_disruption_agent`)
- User-reported gap: asking the chat app for booking details (PNR + flight
  number, cabin/seat/fare/baggage/loyalty) got no useful answer -
  `flight_status_agent` only reports status, `booking_disruption_agent`
  only handles delay/cancellation eligibility and compensation. Neither
  answers "what are my booking details."
- Built `backend/app/graph/nodes/booking_agent.py` - new, separate node
  (not folded into `booking_disruption_agent`, per the user's ask to keep
  them distinct): reads booking (state, or PNR looked up in the message,
  mirroring `booking_disruption_agent`'s existing `_find_booking`
  pattern), reports passenger/cabin/seat/fare/booking status/baggage/
  loyalty/special requests, plus current flight status as a convenience.
  Verified standalone against 3 real PNRs (with/without loyalty, waitlisted
  + special requests) and the not-found case.
- Wired into `graph.py` (`_SPECIALISTS`) - a plain edge back to
  `supervisor`, no approval interrupt needed (this node never touches
  compensation).
- Wired into `supervisor.py`: added as a 4th LLM-classifier route
  (explicitly distinguished in the prompt from both
  `flight_status_agent` - "not tied to a specific passenger's booking" -
  and `booking_disruption_agent` - "not about a delay/cancellation
  impact"); added a keyword-fallback list (`_BOOKING_KEYWORDS`: "my
  booking", "my seat", "my cabin", "my fare", etc.).
- **Found and fixed a real ordering bug while testing the fallback path**:
  an early draft of `_BOOKING_KEYWORDS` included `"my pnr"`, which matched
  almost any PNR-referencing message regardless of actual intent -
  "My PNR is X, my flight was cancelled, what happens now?" (no `rebook`/
  `compensation` keyword) got misrouted to `booking_agent` instead of
  `booking_disruption_agent`. Fixed by dropping `"my pnr"` (too generic to
  be a useful discriminator).
  - Residual, accepted limitation of the keyword-fallback path only
    (confirmed the live LLM path classifies all these cases correctly):
    that same phrasing, with the bad keyword removed, now falls through
    to `flight_status_agent` in fallback mode - which can't resolve a
    bare PNR (no flight number) and gives an unhelpful reply. This is a
    pre-existing gap in `flight_status_agent` (PNR resolution was never
    added there, unlike `booking_agent`/`booking_disruption_agent`), only
    reachable when the gateway is down. Not fixing now - consistent with
    how the RAG chunking and other fallback-only rough edges are already
    documented rather than engineered away; the LLM path (normal
    operation) handles it correctly.
  - Reverified `input_guard` doesn't false-positive on a booking-details
    request citing the passenger's own PNR (0/6 in testing).
- Verified end-to-end: full graph via keyword fallback and live LLM for
  all four specialist routes; real HTTP through the running backend API
  (`POST /chat/message`); the actual Streamlit chat app via `AppTest`
  (the exact path the user hit the original gap through); regression
  check that disruption-approval, guardrail-blocking, and flight-status
  all still work unchanged. Restarted the backend API (8000) and
  `langgraph dev` (2024) so both are serving the current code, not the
  pre-fix version.

## Post-Day-5 fix: streaming chat replies (UX - "screen goes white for long")
- User-reported: the chat app blocks silently for the full backend
  processing time (guardrail check + classification + specialist +
  guardrail check again = up to 4 sequential LLM calls), then the whole
  reply pops in at once.
- **Design decision, not just an implementation detail**: did NOT stream
  raw LLM tokens from `rag_policy_agent`'s synthesis call directly to the
  client in real time. Reason: `output_guard` needs the COMPLETE reply
  text to decide whether to block/replace it (e.g. the internal-leak false
  positive fixed on Day 4) - if tokens were forwarded to the customer as
  the model generates them, by the time `output_guard` finishes checking,
  the (possibly-flagged) content would already be visible, defeating the
  guardrail entirely. Given this project's explicit purpose includes
  demonstrating guardrails properly (CLAUDE.md), chose safety over
  marginally lower perceived latency.
- Instead, built two-phase streaming that's genuinely responsive without
  that gap:
  - **Phase 1 - real progress, not decorative**: `backend/app/routes/
    chat.py`'s new `POST /chat/stream` (SSE) runs the graph via
    `api_graph.stream(..., stream_mode="updates")` and emits a "status"
    event with a friendly label as each node actually completes
    (`_STATUS_BY_NODE`: "Checking your message..." ->
    "Figuring out how to help..." -> e.g. "Checking policy details..." ->
    "Finalizing your answer..."). This is tied to real execution, not a
    fake timer.
  - **Phase 2 - reveal**: once the graph finishes, reads the authoritative
    final state via `api_graph.get_state(config).values` (same pattern
    already used in `routes/ops.py`) and reveals the already-approved
    reply word-by-word as "token" events (~20ms apart) for a typewriter
    effect. A final "done" event carries `thread_id`/`reply`/
    `pending_approval`, same shape as `/chat/message`'s response.
  - `/chat/message` (non-streaming) is unchanged and still exists for
    other API consumers.
  - `frontend/app.py`: replaced `_send_message`/`st.markdown` with
    `_stream_message`, a generator consumed by `st.write_stream` -
    "status" events update a placeholder in place (cleared on the first
    real token), "token" events are yielded for progressive rendering,
    "done" populates `thread_id`/`pending_approval` via a `result` dict
    passed by reference (a generator's local state isn't otherwise
    reachable after `st.write_stream` exhausts it).
- **Found and fixed a real bug during testing**: `supervisor` runs twice
  per turn (route, then end-of-turn), so the naive "skip if same as last
  label" dedup still showed "Figuring out how to help..." twice whenever
  another status appeared in between. Fixed by tracking a `shown_labels`
  set (each label shown at most once per turn) instead of just the last
  one.
- **Not a bug, a testing artifact** (same category as the Day-4 `localhost`
  rate-limit issue): `curl -N` against `/chat/stream` hung for the full
  30s timeout with only 2 of ~60 events received, looking like a broken
  stream. Direct `api_graph.stream()` calls and Python's `requests`
  library (`iter_lines`, matching what the frontend actually does) both
  received all events progressively in real time (~9s total, matching
  direct graph timing) - confirmed this curl behavior is specific to this
  Windows/Git-Bash curl build, not the backend. Use `requests`-based
  scripts, not curl -N, to manually check SSE endpoints in this repo.
- Verified end-to-end: raw SSE stream inspection (status/token/done
  ordering, timing) for a policy question, an interrupt/approval-needed
  disruption request (`pending_approval` correctly present in "done" even
  though the graph halted mid-way), and a guardrail-blocked message
  (stream correctly short-circuits to one status + the refusal, skipping
  supervisor/specialist/output_guard entirely). Full frontend regression
  via `AppTest` on the new streaming path: golden path, multi-turn,
  guardrail block, pending-approval banner (temporary test fixture, since
  properly restored - see below), and all three error paths (wrong key,
  unreachable backend, missing key) - no unhandled exceptions.
- Fixture-file note: used a Python script (not the usual Edit-based
  add/remove) to add temporary test bookings this round, which reformatted
  the whole file's JSON indentation as a side effect - caught this and
  restored `pnr.json`/`flights.json` with `Write` using their exact
  original content (verified back to 10 flights/6 bookings), not just a
  diff-remove on the reformatted version.
- Restarted the backend API (8000) and `streamlit run` (8501) so both
  serve the current streaming code; `langgraph dev` (2024) untouched
  since `graph.py`/`supervisor.py` weren't changed this round.

## Latency investigation: tried and reverted a model-routing "fix"
- Diagnosed the "why is it slow" complaint: every turn makes up to 4
  sequential LLM round trips (`input_guard`, `supervisor`, the specialist
  if it calls one, `output_guard`) that can't run in parallel - each
  depends on the previous step's output. Measured trace for a policy
  question: input_guard 5.2s in, supervisor +0.7s, rag_policy_agent +2.2s,
  output_guard +0.8s -> ~8.9s total (that first number included this
  session's usual first-call-in-a-fresh-process connection warmup, not
  pure model latency - see below).
- **Hypothesis tried and disproven by direct measurement**: assumed
  `litellm-proxy`'s `fallback` model (`claude-haiku-4-5-20251001`) would
  be faster than `primary` (`gpt-4o-mini`) for the three short structured-
  output classification calls (`input_guard`/`supervisor`/`output_guard`),
  since classification doesn't need a large model. Set `model="fallback"`
  on all three and measured - it made things WORSE, not better.
  - Direct A/B (same warm connection, 4 calls each, after a warmup call
    to rule out cold-start): `primary` averaged ~0.7s per structured-
    output call; `fallback` averaged ~1.9s - **primary is ~2.5x faster**
    for this specific workload/proxy setup, the opposite of the
    assumption.
  - Reverted all three `model="fallback"` changes back to the default
    (unset -> `primary`) in `supervisor.py`, `input_guard.py`,
    `output_guard.py`.
  - Confirmed on the warm, already-running backend (not a fresh process -
    fresh-process cold-start was confounding earlier readings): 3
    consecutive real policy-question requests averaged ~4.9s after
    reverting, vs. 6.8-7.8s with the (wrong) fallback-model change in
    place. Full regression (flight status, booking, disruption, guardrail
    block) reverified clean.
  - Takeaway for future latency work on this proxy: don't assume
    `fallback`'s name implies "smaller/faster" - it's just the second
    entry in `litellm-proxy/config.yaml`'s failover chain, and empirically
    slower here. Any future model-routing change should be measured with
    a warm-connection A/B test the same way, not assumed.
- Given #1 (route classifiers to a faster model) didn't pan out, the
  other options raised are still on the table if wanted: #2 (merge
  `input_guard` + `supervisor` into one structured-output call, cutting a
  full round trip), #3 (proxy-level per-request timeout + fallback-on-
  timeout to bound tail latency), #4 (keyword pre-filter fast path before
  the LLM guardrail call, with real false-positive/negative risk to weigh
  first).

## Staff approval tab: SQLite-backed staff auth + a Streamlit ops UI
- Closed the "no ops-approval UI" gap open since Day 4: booking-disruption
  approvals could previously only be approved/rejected via curl/API calls.
  Built real staff authentication (SQLite, password-hashed, role-checked)
  and a second Streamlit tab so a staff member can log in and act on
  pending requests without touching the API directly.
- `backend/app/db.py` (new) - a small local SQLite DB (`backend/staff.db`,
  gitignored, sibling to `chroma_db/` - a runtime artifact, not committed
  fixture data), two tables:
  - `staff_users`: username (unique), PBKDF2-HMAC-SHA256 password hash +
    per-user random salt (260k iterations, OWASP's 2023 minimum;
    stdlib `hashlib`/`secrets` only - no new dependency for something
    Python's standard library already does adequately for this scope),
    `role` (defaults to `"staff"`, checked not just stored - see below).
    `verify_staff_login` uses `secrets.compare_digest` for the hash
    comparison to avoid a timing side-channel.
  - `pending_approvals`: a queue table (`thread_id` primary key, pnr,
    flight_number, compensation_usd, reason, created_at) - exists because
    the graph's checkpointer has no "list every thread with a pending
    interrupt" API, only per-thread lookups. `routes/chat.py` writes a row
    whenever a turn's `pending_approval` comes back set (both
    `/chat/message` and `/chat/stream`); `routes/ops.py` deletes the row
    once a decision is made. This is what makes the staff tab a real
    queue instead of requiring someone to already know a `thread_id`.
  - `if __name__ == "__main__"` CLI: `uv run python -m app.db <username>
    <password>` to create a staff account - no auto-seeded default
    credentials anywhere in code (a hardcoded demo password is exactly
    the kind of thing that leaks into "demo" -> "actually deployed
    somewhere"). Created a `test_staff` account this session for testing.
  - `backend/app/sessions.py` (new) - in-memory bearer-token session
    store (8h TTL), same "no external infra for a demo" choice already
    made for the checkpointer/rate-limiter - restarting the backend logs
    everyone out, which is fine for this scope and consistent with how
    the customer chat's own state already resets.
- `backend/app/routes/ops_auth.py` (new) - `POST /ops/auth/login`
  (username/password -> session token) and `POST /ops/auth/logout`.
  Deliberately NOT gated by `X-API-Key` - staff prove identity with their
  own credentials, not a shared client key; protected by the existing
  `RateLimitMiddleware` instead, same as any public login endpoint.
- `backend/app/middleware/auth.py`: `/ops/*` now accepts EITHER the
  existing `X-Ops-API-Key` (kept for service-to-service/API use, e.g. the
  curl workflows from Day 4) OR a valid staff bearer token whose session
  role is `"staff"` - an explicit role check, not just "any logged-in
  session," so a future non-approval role wouldn't silently get approval
  rights. Added `/ops/auth/login`+`/ops/auth/logout` to the public-paths
  allowlist.
- `backend/app/routes/ops.py`: added `GET /ops/approvals` (the queue
  listing, backed by `db.list_pending_approvals`); `decide_approval` now
  also calls `db.resolve_pending_approval` after a successful resume.
- `frontend/app.py`: split into `st.tabs(["Customer Chat", "Staff
  Approval"])`. The Staff Approval tab: a login form when no session
  exists; once logged in, the pending-approvals queue as expanders (PNR,
  flight, compensation, reason, requested-at), each with an Approve/
  Reject button and an "agent note (internal only)" field. A 401/403 from
  the backend (e.g. an expired 8h session) clears the stored session and
  prompts re-login rather than showing a raw error.
- Verified end-to-end, including the full path a real user would take
  (not just curl): triggered a $500 disruption approval through the
  actual chat tab (`AppTest`), confirmed it appeared in the staff queue,
  logged in and approved it through the actual staff tab UI (button
  clicks, not direct API calls), confirmed the queue cleared. Also
  verified: wrong password (login fails cleanly, no session created);
  the legacy `X-Ops-API-Key` path still works unchanged (backward
  compat); customer-key-only access to `/ops/*` still 403s; logout
  invalidates the token immediately; reject path; full regression on
  unrelated routes (flight status, policy, auth-required 401). As usual,
  used temporary test fixtures (PNR/flight combos hitting the >$300 tier,
  since none of the real mocked flights do) and restored `pnr.json`/
  `flights.json` to their original 10 flights/6 bookings afterward.
- Restarted the backend API (8000) and `streamlit run` (8501) to serve
  the new code; `langgraph dev` (2024) and `litellm-proxy` (4000)
  reverified healthy, unaffected by this change.
- Known limitations, acceptable for this scope, not fixed: sessions are
  in-memory (lost on backend restart); no staff user management UI (CLI
  only); no audit history of past decisions (each resolved approval's row
  is deleted, not archived) - if a compliance trail matters later, that's
  a real gap to revisit, but wasn't asked for here.

## User-reported bug: customer chat didn't reflect a staff approval decision
- User's report: after approving a disruption request from the Staff
  Approval tab, the customer's own chat showed no update; asking "is it
  approved" got the generic "could you rephrase?" fallback, and the chat
  visually "started from below the textbox."
- **Found two distinct, unrelated bugs bundled in one report:**
  1. **Real bug, fixed**: the frontend's `st.session_state.messages` is a
     purely local, client-side cache - it only ever grows by appending
     whatever THIS browser session sends/receives. When staff resume a
     thread from the Staff Approval tab, `human_approval` correctly
     appends the outcome to that thread's server-side history (verified -
     the data was never wrong), but the customer's own browser has no way
     to know that happened; nothing pushes updates to it. So "no update to
     the chat" and "history didn't work" were the same root cause: the
     client's local copy was stale and never resynced with the
     authoritative server state.
     - Fix: added `GET /chat/history/{thread_id}` (`routes/chat.py`),
       reading straight from `api_graph.get_state(...)` - the same
       authoritative source `routes/ops.py` already uses. The Customer
       Chat tab now calls this and overwrites `st.session_state.messages`
       from it every time the tab renders (plus a manual "Refresh"
       button), so any out-of-band change - a staff decision, or in
       principle any future actor writing into the same thread - becomes
       visible the next time the customer's page reruns, without them
       needing to ask.
     - Verified via `AppTest`, reproducing the exact report: triggered a
       $500 approval in the chat tab, approved it from the staff tab
       (same backend, matching how staff and customer share server state
       regardless of being different browser sessions), then confirmed
       clicking "Refresh" in the chat tab reveals the "approved and
       confirmed" message that was previously invisible.
  2. **Real, separate limitation, NOT fixed (flagging, not silently
     patching)**: asking "is it approved" as a literal chat message
     (rather than clicking Refresh) still returns the generic fallback -
     reproduced and confirmed still present after the fix above.
     Root cause: `supervisor`'s classifier (`_llm_classify`/
     `_CLASSIFIER_PROMPT`) only ever looks at the single latest message in
     isolation - `_latest_user_text` extracts just that one string, and
     the LLM call sends nothing else, no prior conversation. "Is it
     approved" is genuinely ambiguous with zero context, so classifying
     it as "none" isn't wrong given what the classifier can see - it's a
     structural limitation (no specialist or route exists for "check the
     status of my previous request" at all, and the classifier has no
     conversation history to work with even if one did). Real fix would
     need either passing recent conversation context into classification,
     or a dedicated status-check intent/specialist - a genuinely separate
     feature, not a quick patch, so left open rather than bolted on here.
     With fix #1 in place, this matters less in practice (the customer
     would typically just see the outcome via history sync rather than
     needing to ask) but the gap is real if they ask before refreshing.
- Also fixed in passing: `st.chat_input` was called INSIDE `st.tabs()`,
  which is a known Streamlit limitation - a chat_input nested in a tab
  (or column, or other container) doesn't pin to the bottom of the page
  and renders inline instead, which is almost certainly what "started
  from below the textbox" describes. Moved the `st.chat_input(...)` call
  to the top level of the script (outside `st.tabs`), keeping its return
  value scoped to `tab_chat`'s logic - this is Streamlit's documented
  requirement for chat_input to pin correctly. Not independently
  verifiable via `AppTest` (it doesn't simulate visual/CSS layout) - worth
  a quick look in an actual browser to confirm the fix, though the root
  cause matches the symptom precisely.
- Verified no regression on unrelated paths (flight status via chat,
  compile checks across all touched files). Restarted backend (8000) and
  `streamlit run` (8501). The `ZXDEMO1`/`ZXDEMO` test fixture from the
  previous message is still in place for further hands-on testing -
  remove it (see `pnr.json`/`flights.json`) once done.

## Day 6 (done)
- `docs/architecture.md` (new): graph topology diagram (guardrails +
  supervisor + specialists + human_approval routing), the `AgentState`
  reducer table (with the "why" for each), the gateway-first/
  keyword-fallback pattern, why two `build_graph()` instances exist
  (Studio's checkpointer-less `graph` vs. `runtime.py`'s checkpointed
  `api_graph`), the HTTP API surface, and the Day-6 deployment shape.
  Written from the actual code (`state.py`, `graph.py`, `supervisor.py`,
  `gateway/client.py`, `human_approval.py`, `runtime.py`), not from memory
  of what it should be.
- `.claude/skills/langgraph-node/SKILL.md` (new): captures the node
  convention already consistently followed by every existing node/
  guardrail - signature/docstring shape, state-first-then-message-parsing
  input pattern, the gateway-first/keyword-fallback shape (with a real
  code template), the reducer-audit checklist before adding a state
  field, and wiring steps into `graph.py`/`supervisor.py` (including a
  callback to the real `"my pnr"` keyword-shadowing bug from Day 5 as a
  concrete "check for this" item). Ends with "test standalone before
  wiring in," per CLAUDE.md.
- `.claude/agents/deploy-helper.md` and `code-reviewer.md` (new): scoped
  subagent definitions, not generic templates.
  - `deploy-helper`: for infra/Docker/CI work only (not application code);
    checklist centers on port/env-var/image-name consistency across
    compose/Bicep/workflow (flagged as the single most common failure
    class for this kind of setup, and one no test catches), no literal
    secrets in `infra/`, and not fighting the three-separate-images
    design with a shared base image.
  - `code-reviewer`: ordered by *this repo's* actual past bugs, not a
    generic style checklist - reducer correctness first, then node
    shape, then the gateway-fallback pattern, then keyword-shadowing,
    then secrets, then the ops-role-check, then guardrail placement
    (citing the real `agent_note`-leak and `"my pnr"` incidents from
    `docs/progress.md` as the concrete "why this matters here"). Told
    explicitly to skip generic style nits and speculative-abstraction
    complaints, matching CLAUDE.md's own anti-over-engineering stance.
- `backend/Dockerfile`, `frontend/Dockerfile`, `litellm-proxy/Dockerfile`
  (new, one per independent uv project, no shared base image): all build
  from each project's `requirements.txt` (`pip install -r
  requirements.txt`, hash-checked since `uv export` includes hashes) per
  CLAUDE.md's stated Docker-build convention, rather than `uv sync`
  inside the image.
  - Generated `litellm-proxy/requirements.txt` for the first time this
    session (`uv export --format requirements-txt --no-dev`) - it didn't
    exist yet, unlike backend's/frontend's.
  - `backend/Dockerfile` also runs `python -m app.rag.ingest` at build
    time, baking the Chroma `policies` collection into the image - found
    while writing this that `rag/retriever.py`'s `_collection()` calls
    `get_collection` (not `get_or_create_collection`), so
    `rag_policy_agent` would hard-crash on first query in a container
    that never ran ingestion. Policy docs are static fixture data, so
    baking at build time (vs. a persistent volume or a runtime init step)
    is the simplest correct fix.
- `docker-compose.yml` (new): all three services, `depends_on` +
  healthchecks so backend waits for a healthy litellm-proxy and frontend
  waits for a healthy backend. Each service's real `.env` supplies
  secrets via `env_file`; `LITELLM_BASE_URL`/`SKYOPS_API_BASE_URL` are
  overridden in compose to the service DNS names (`http://litellm-proxy:
  4000`, `http://backend:8000`) since those vars' `.env` values are
  `localhost`, which doesn't resolve between containers. Validated with
  `docker compose config` (daemon itself wasn't running in this
  environment, so `docker compose up` was NOT run - image builds/runtime
  wiring are unverified, only YAML/interpolation syntax).
  - **Security incident during this session**: validating with `docker
    compose config` fully resolves `env_file` secrets and prints them in
    plaintext with no redaction. Assumed the `.env` files were empty and
    ran it directly - they were not, and real `OPENAI_API_KEY`/
    `ANTHROPIC_API_KEY`/`LITELLM_MASTER_KEY`/`SKYOPS_API_KEY`/
    `SKYOPS_OPS_API_KEY` values got printed into the session output.
    Flagged to the user immediately with a recommendation to rotate the
    provider keys (OpenAI/Anthropic) and regenerate the local-only ones.
    **If you're picking this up and haven't rotated yet, do that before
    anything else.** Lesson for future sessions: never run `docker
    compose config`/`up`/any command that resolves `env_file` against
    real `.env` files without first confirming they're empty/placeholder
    - don't assume.
- `infra/bicep/` (new): `main.bicep` (Log Analytics workspace + one
  Container Apps Environment + three Container Apps via a shared
  module), `modules/container-app.bicep` (generic, parameterized - same
  shape for all three services), `main.parameters.json` (placeholder
  template, `REPLACE_ME` values only, safe to commit).
  - `litellm-proxy` deployed with `externalIngress: false` (environment-
    internal only - matches "the only service holding real provider
    keys, nothing but the backend should call it" from
    `docs/architecture.md`); `backend`/`frontend` external.
  - Secrets (`openaiApiKey`, `anthropicApiKey`, `litellmSharedKey`,
    `skyopsApiKey`, `skyopsOpsApiKey`) are all `@secure()` deployment
    parameters, never literals - supplied by the GitHub Actions workflow
    from repo secrets.
  - Each Container App pulls its image via system-assigned managed
    identity (`registries: [{ identity: 'system' }]`), with explicit
    `AcrPull` role assignments in `main.bicep` for all three apps against
    the registry - no registry password stored anywhere. Assumes the ACR
    lives in the same resource group as the deployment; documented as a
    comment (would need a separately-scoped module otherwise).
  - **Not verified against a real Azure subscription** - no `az` CLI
    available in this session's environment, so this was written and
    manually reviewed for syntax/resource-shape correctness, not
    `az deployment group validate`'d or actually deployed. Do that before
    trusting it fully.
- `.github/workflows/deploy.yml` (new): matrix build (`backend`,
  `frontend`, `litellm-proxy`) → push to ACR → deploy `infra/bicep/
  main.bicep`, gated on push to `main` (path-filtered to the relevant
  dirs) or manual dispatch.
  - Azure auth via OIDC/workload-identity-federation
    (`azure/login@v2` with `client-id`/`tenant-id`/`subscription-id`,
    `permissions: id-token: write`) - no long-lived Azure credential
    stored in GitHub, per the "never hardcode secrets" instinct extended
    to CI config, not just application code.
  - Each matrix job re-runs `uv export --format requirements-txt --no-dev`
    and diffs it against the committed `requirements.txt`, failing the
    build if they've drifted - catches exactly the "forgot to re-export
    after `uv add`" mistake CLAUDE.md's package-management section warns
    about, before it ships a stale image.
  - Listed required repo secrets in a header comment (`AZURE_CLIENT_ID`/
    `AZURE_TENANT_ID`/`AZURE_SUBSCRIPTION_ID`/`AZURE_RESOURCE_GROUP`/
    `AZURE_CONTAINER_REGISTRY`/`AZURE_CONTAINER_REGISTRY_RESOURCE_ID`/
    `OPENAI_API_KEY`/`ANTHROPIC_API_KEY`/`LITELLM_SHARED_KEY`/
    `SKYOPS_API_KEY`/`SKYOPS_OPS_API_KEY`) since none of this exists yet
    (no Azure resource group/registry/federated app registration set up
    this session) - the workflow is written and YAML-validated
    (`python -c "import yaml; ..."`) but has never actually run.
- Backend/`streamlit run` restarted at the start of this session (see
  chat history above) - unrelated to Day 6's infra work, `langgraph dev`
  (2024) and `litellm-proxy` (4000) untouched.

## Key rotation, first real git history, and docker-compose verification (2026-09-07)
- **Closed out the Day-6 `docker compose config` secret-leak incident**:
  rotated all five exposed secrets this session -
  `OPENAI_API_KEY`/`ANTHROPIC_API_KEY` (user rotated via the OpenAI/
  Anthropic consoles - not something this session can do itself),
  `LITELLM_MASTER_KEY` (+ matching `LITELLM_API_KEY` in `backend/.env`),
  `SKYOPS_API_KEY` (updated in both `backend/.env` and `frontend/.env` -
  they must match), and `SKYOPS_OPS_API_KEY`. Each rotation was verified
  live, not just written to the `.env` files: old keys confirmed rejected
  (401/403), new keys confirmed working, including real completions
  through both `primary` (OpenAI) and `fallback` (Anthropic) via a
  restarted `litellm-proxy`.
  - **Caught a false "done" claim mid-rotation**: after the user said "all
    my keys are rotated," diffing `litellm-proxy/.env` against its prior
    content showed `ANTHROPIC_API_KEY` had actually changed but
    `OPENAI_API_KEY` was byte-for-byte identical to the old, leaked value -
    flagged instead of trusting the claim; user then rotated it for real
    and the diff (and a live completion) confirmed it. Worth always
    diffing against the known-old value rather than trusting "I rotated
    it," since a rotation can silently fail to take (e.g. pasting into the
    wrong line, or a dashboard action that didn't actually save).
  - **Found a real gotcha while restarting the backend**: `backend/app/
    main.py` has no dotenv loading - `middleware/auth.py`/`gateway/
    client.py` read straight from `os.environ`, so `uv run uvicorn`
    only sees `backend/.env` values if they're exported into the shell
    first (`set -a && source <(tr -d '\r' < .env) && set +a`, since the
    file has CRLF line endings that break a plain `source`). The first
    restart attempt 401'd on a correct key for exactly this reason.
    `litellm-proxy`'s own CLI loads its `.env` automatically, so that
    service didn't have this problem. Not fixed (no dotenv dependency
    added) since this only affects manual local restarts, not
    `docker compose`/Bicep, which supply env vars directly - but worth
    remembering for the next local restart.
- **Committed and pushed the entire project for the first time**: nothing
  had been committed since the initial uv scaffold (`3b5497c`) despite
  Days 2-6 of work sitting in the working tree. Split into 6 logical
  commits rather than one dump - core LangGraph agent (state, specialists,
  RAG, gateway, guardrails, fixtures), FastAPI backend (routes,
  middleware, staff auth/queue), Streamlit frontend, Docker/Bicep/CI
  infra, docs + `.claude/` config, then a `.dockerignore` follow-up (see
  below) - and pushed all of it to `origin/main`
  (`github.com/munira3011/skyops`).
  - Before pushing, ran a full safety pass: scanned every commit's diff
    and the entire git history (`git log --all -p`/`git grep` across all
    revisions) for secret-shaped strings (`sk-proj-`, `sk-ant-api03-`,
    `LITELLM_MASTER_KEY=sk-`, etc.) and confirmed `.env`/`staff.db`/
    `chroma_db` were never tracked at any point - all clean. Re-verified
    `docker-compose.yml` and all three `Dockerfile`s only reference
    secrets via `env_file`/`os.environ`, never a literal value, and none
    use `COPY . .` that could pull `.env` into an image.
  - Added `.dockerignore` to `backend/`, `frontend/`, `litellm-proxy/` as
    a hardening measure (not a fix for an active leak - the Dockerfiles'
    explicit `COPY`s already made this safe) so a future switch to
    `COPY . .` wouldn't silently start baking `.env` into an image.
- **Verified `docker compose up` for real** (a Docker daemon wasn't
  running in the Day 6 session, so this had only been YAML-validated
  until now): built and started all three services, confirmed
  `depends_on`/healthcheck ordering works (backend waited for a healthy
  litellm-proxy, frontend for a healthy backend), and confirmed real
  inter-container networking - backend's logs showed it calling
  `http://litellm-proxy:4000` (the compose DNS override), not
  `localhost`. Verified over HTTP against the running containers: health
  checks, 401 on a missing API key, a flight-status query, and a RAG
  policy query with a correct grounded citation - all through the
  containerized stack, live LLM calls included. No errors or leaked
  secrets in container logs. Brought the stack down cleanly afterward
  (`docker compose down`).

## Next up
- `az bicep build`/`az deployment group validate` (or an actual
  `az deployment group create` against a scratch resource group) for the
  Bicep templates - still unverified against a real Azure subscription.
- A real run of `.github/workflows/deploy.yml` once the Azure resources/
  secrets it expects exist.
- Move on to Day 7: `evals/` - `datasets/{golden_qa.jsonl,
  adversarial_prompts.jsonl}`, `test_agent_accuracy.py`,
  `test_guardrails.py`, `run_ragas_eval.py`. Per CLAUDE.md, run these
  before considering a day's work done from here on.
- Also still open: the "is it approved"-style status-check classifier gap
  noted above (Day 5's user-reported-bug section), if wanted.

## Looking further ahead (rough, adjust as we go)
- **Day 6**: `docs/architecture.md`, `.claude/skills/langgraph-node/SKILL.md`,
  `.claude/agents/{deploy-helper.md,code-reviewer.md}`, `infra/bicep/`,
  `.github/workflows/deploy.yml`, `docker-compose.yml`.
- **Day 7**: `evals/` - `datasets/{golden_qa.jsonl,adversarial_prompts.jsonl}`,
  `test_agent_accuracy.py`, `test_guardrails.py`, `run_ragas_eval.py`. Per
  CLAUDE.md, run these before considering a day's work done from here on.
