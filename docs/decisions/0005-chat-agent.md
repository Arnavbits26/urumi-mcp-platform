# 0005 - Chat agent

**What it is.** A chat page in the dashboard backed by `POST /api/chat`. The server runs the tool loop:
send the conversation plus the tool list to an LLM; if the model asks for tools, run them; feed the
results back; repeat until it answers or the step limit (6) is reached. The response contains the
answer and the list of tools used (name, arguments, outcome, duration, a result preview), which the
page shows under each answer.

**One code path for tools.** Listing and running tools lives in `ToolService`. The MCP endpoint
(`/mcp`, used by Claude Code, Cursor and so on) and the chat agent both call it. So the chat acts as the
signed-in person (their identity is passed in), gets the same routing, timeouts and error messages as
external clients, and every call lands in the audit log under that person, marked `source = chat`.
Nothing in the chat code can reach an upstream server directly. Alternatives considered: calling our own
`/mcp` over loopback (needs a new internal credential type and an extra network hop for no gain), and
giving the chat its own upstream connections (a second path to audit and secure).

**Any OpenAI-compatible AI service.** The provider is three settings: `LLM_BASE_URL`, `LLM_MODEL` and
`LLM_API_KEY`. The default is Groq's free tier; Gemini's compatible endpoint or a local Ollama only need
different values. The key is read from a Kubernetes Secret (`llm-credentials`), is entered through a
hidden prompt by `scripts/llm-secret.sh`, is never returned by any endpoint, and is never in a Helm value.

**Free-tier realities.** Limits are low (tokens per minute and per day), so the agent: trims tool schemas
(drops titles, turns "X or null" into X), caps tool output at 6,000 characters and history at 20
messages, waits and retries on HTTP 429 (honouring Retry-After, up to 15 s), retries (up to twice) when the model
emits a malformed tool call, and limits each person to 12 chats per minute so one user cannot spend the
team's quota. The limit counter is in memory per replica.

**Untrusted tool output (prompt injection).** Anything an upstream returns, and any tool description,
could be written to manipulate the model. Mitigations, none of which is a guarantee:
- results reach the model only as `tool` messages, never as user or system text;
- the system prompt says results are data, not instructions, and that changes need an explicit request;
- clients cannot send `system` messages (only `user` and `assistant` roles are accepted);
- at most 4 tool calls per model turn and 6 turns per chat, with arguments validated as JSON objects;
- tool descriptions are length-capped and prefixed with the server name;
- every action is attributed to the signed-in person in the audit log.
Residual risk: a model can still be fooled into calling a write tool the user is allowed to use.
Per-tool permissions (members read, admins write) are the planned stronger control.

**Failures are explained, not hidden.** No key configured: 503 with what to do. Key rejected: 502 naming
the fix. Rate limited: a clear message. Slow or dead upstream: the tool result says so and the model
reports it. A chat that runs too long is cut off at 120 s.

**Stateless server.** The browser sends the recent messages with each request; nothing about a
conversation is stored server-side, which keeps replicas interchangeable and avoids storing chat text.
Tool calls are stored, in the audit log, as before.
