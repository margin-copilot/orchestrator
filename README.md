# Margin Copilot: orchestrator

Margin Copilot orchestrator agent: chat API that calls the specialist agents over A2A.

Part of **Margin Copilot**, a secure multi-agent pricing assistant on Azure, built as a personal learning project.

**Start here:** [margin-copilot/docs](https://github.com/margin-copilot/docs) explains how the whole system works and how the repos fit together.

## Contract

- `POST /chat` with `{"question": "...", "conversation_id": "..."}` (`conversation_id` optional; at most 2,000 characters; any other field is 400) returns `{"answer": "...", "refusal": null, "agents": ["data-agent", "policy-agent"], "citations": ["PP-07 §2"], "conversation_id": "..."}` (D-029). `refusal` is `out_of_scope`, `no_access` or `no_data` when nothing could be answered; `citations` are policy clauses. An unknown, someone else's or expired conversation is 404 `{"error": "conversation not found"}`.
- The same `POST /chat` with `Accept: text/event-stream` streams the turn as server-sent events (D-045), each `data:` one JSON object: `step` (`{"step": "condense" | "route" | "data-agent" | "policy-agent" | "action-agent" | "compose"}`, as each stage starts), `lead` (`{"answer": "..."}`, the data agent's reply the moment it arrives in a turn with two agents; the final answer leads with it, or replaces it), then `done` with exactly the JSON body above, or `error` with `{"error": "...", "status": 502}`, the message and status the JSON reply would have had. Bad bodies and tokens are still answered before any stream, as JSON 400/401/403/413. A turn finishes and is stored even if the client leaves; a failure at 500 or above marks the request failed in traces. When the app shuts down (a deploy), an open stream gets `STREAM_SHUTDOWN_GRACE_SECONDS` (default 25 s; infra sets 235 s, within the 240 s Container Apps termination grace) to finish its turn.
- `POST /warm` (D-044) returns `202 {}` at once, then wakes the agents in the background: it fetches each agent's card, which starts its container, and calls the data agent's `/warm` with an OBO token, which resumes a paused database as the user. One warm-up runs at a time per replica; failures are logged as `warm failed: <agent>: <type>`. The web app calls it after sign-in.
- Both routes need a bearer token addressed to this service, with scope `access_as_user`, from an app on `ALLOWED_CALLERS`.
- Errors:
  - 400 for a malformed body, 413 when too large.
  - 401 or 403 for an untrusted token or a disallowed caller.
  - 404 `{"error": "conversation not found"}` for an unknown, someone else's, malformed or expired conversation.
  - 502 `{"error": "conversations unavailable"}` when the conversation store fails.
  - 502 `{"error": "<name> unavailable"}` (`data-agent`, `policy-agent`, `action-agent`, `router`, `condenser` or `orchestrator` for a turn over its limit), also when an agent's reply breaks the shared `AgentReply` contract. The details go only to the log.
- A router (one gpt-5-mini call, structured output) picks one of six routes per question (D-040): the data agent, the policy agent, both, the action agent alone (it only drafts pending proposals), a fixed pointer to the approval screen for requests to approve, reject or list proposals, or a fixed decline for out-of-scope questions. One agent's reply passes through unchanged. Two replies are combined by a composer call; code checks that every number in the result appears in a reply or in what the user typed (percentages against percentages, clause IDs as IDs), retries once, and otherwise returns the replies side by side, as it does when the composer fails. If the composer repeats the data agent's reply, only its own text after it is kept.
- Conversations (D-041): turns are stored in Table Storage under the caller's `oid`, so only the owner can continue one; follow-ups are rewritten into a standalone question from the last 10 turns; a conversation can be continued for 7 days after its first turn. A turn has 230 seconds; past that it's a 502 and nothing is stored.

## Configuration

Names in [.env.example](.env.example). `ADAPTERS=azure|fake` (no default), `TENANT_ID`, `CLIENT_ID` (this app registration), `AZURE_CLIENT_ID` (its managed identity), `ALLOWED_CALLERS`, `DATA_AGENT_URL`, `DATA_AGENT_SCOPE`, `POLICY_AGENT_URL`, `POLICY_AGENT_SCOPE`, `ACTION_AGENT_URL`, `ACTION_AGENT_SCOPE`, `FOUNDRY_PROJECT_ENDPOINT`, `FOUNDRY_MODEL` (the router's, composer's and condenser's model, called with the managed identity), `TABLE_ENDPOINT`, `CONVERSATIONS_TABLE`, `ALLOWED_ORIGINS` (exact browser origins allowed to call `/chat` and `/warm`, comma-separated; CORS only, the token check still decides). Optional: `STREAM_SHUTDOWN_GRACE_SECONDS` (seconds an open stream may finish after SIGTERM; a non-negative number, default 25; infra sets 235, within the 240 s Container Apps termination grace; anything else fails at start).

## Run and test

```bash
uv sync
uv run pytest -q
ADAPTERS=fake uv run uvicorn --factory orchestrator.main:create_app --port 8000
```

How the identity flow works: [security model](https://github.com/margin-copilot/docs/blob/main/security-model.md).

## Deploy

A push to `main` runs the tests, publishes `ghcr.io/margin-copilot/orchestrator:sha-<commit>`, runs the evals gate on that commit, then deploys the image to the container app through GitHub OIDC (no stored Azure secret). The deploy waits up to 5 minutes for the new revision to be ready and serve all traffic, or fails while the old revision keeps serving, then checks that `POST /chat` without a token is 401 and that a preflight from the web origin (the first `https://` origin in the app's `ALLOWED_ORIGINS`) is allowed. Runs on `main` go one at a time: a push waits until the running one has finished, and a newer waiting run replaces an older waiting one, so the latest commit ends up live. Other branches and pull requests get no Azure token. Repository secrets: `AZURE_CLIENT_ID` (this repo's deploy identity, not the app setting of the same name), `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`, `AZURE_RESOURCE_GROUP`, `CONTAINER_APP_NAME`. The infra repo creates the app and the identity; how it all fits: [docs](https://github.com/margin-copilot/docs).

## License

MIT. See [LICENSE](LICENSE).
