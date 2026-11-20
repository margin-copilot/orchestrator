# Margin Copilot: orchestrator

Margin Copilot orchestrator agent: chat API that calls the specialist agents over A2A.

Part of **Margin Copilot**, a secure multi-agent pricing assistant on Azure, built as a personal learning project.

**Start here:** [margin-copilot/docs](https://github.com/margin-copilot/docs) explains how the whole system works and how the repos fit together.

## Contract

- `POST /chat` with `{"question": "..."}` (at most 2,000 characters) returns `{"answer": "...", "refusal": null, "agents": ["policy-agent"], "citations": ["PP-04 §3.2"]}` (D-029). `refusal` is `out_of_scope`, `no_access` or `no_data` when nothing could be answered; `citations` are policy clauses (empty for data answers).
- It needs a bearer token addressed to this service, with scope `access_as_user`, from an app on `ALLOWED_CALLERS`.
- Errors:
  - 400 for a malformed body, 413 when too large.
  - 401 or 403 for an untrusted token or a disallowed caller.
  - 502 `{"error": "<agent> unavailable"}` (`data-agent`, `policy-agent` or `router`), also when an agent's reply breaks the shared `AgentReply` contract. The details go only to the log.
- Milestone 5: a router (one gpt-5-mini call, structured output) picks the data agent or the policy agent, and that agent's reply passes through unchanged (D-035). Each agent is called over A2A with a fresh OBO token, at its configured URL (an agent's own card is never trusted for where tokens go). Answers combining several agents arrive in milestone 7.

## Configuration

Names in `.env.example`. `ADAPTERS=azure|fake` (no default), `TENANT_ID`, `CLIENT_ID` (this app registration), `AZURE_CLIENT_ID` (its managed identity), `ALLOWED_CALLERS`, `DATA_AGENT_URL`, `DATA_AGENT_SCOPE`, `POLICY_AGENT_URL`, `POLICY_AGENT_SCOPE`, `FOUNDRY_PROJECT_ENDPOINT`, `FOUNDRY_MODEL` (the router's model, called with the managed identity).

## Run and test

```bash
uv sync
uv run pytest -q
ADAPTERS=fake uv run uvicorn --factory orchestrator.main:create_app --port 8000
```

Pushing to `main` publishes `ghcr.io/margin-copilot/orchestrator:sha-<commit>`. Deployment: the private infra repo. How the identity flow works: [security model](https://github.com/margin-copilot/docs/blob/main/security-model.md).

## License

MIT. See [LICENSE](LICENSE).
