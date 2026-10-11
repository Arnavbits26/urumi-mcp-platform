# Using the gateway from outside the dashboard

An external client needs exactly two things: the gateway address and one person's API key (Dashboard,
API keys, Create key). It never needs the upstream servers' credentials.

Gateway address: `http://gateway.localtest.me:8080/mcp`

## A. MCP Inspector (free, uses no AI model)
The official MCP debugging tool. Needs Node.js (`node --version`).

    npx @modelcontextprotocol/inspector

In the page that opens: transport type Streamable HTTP, URL as above, add a header named `Authorization`
with value `Bearer <your key>`, then Connect. Open the Tools tab, List Tools, pick a tool, Run Tool.
(Labels vary a little between versions.) Use it to show a named client connecting and seeing the tools
of every enabled server, and to call `woocommerce__list_orders` by hand.

## B. Claude Code (connection only, free)
    claude mcp add --transport http urumi http://gateway.localtest.me:8080/mcp --header "Authorization: Bearer <your key>"
    claude mcp list          # shows urumi as connected; uses no model
Sending prompts from Claude Code spends your Claude account's allowance, so it is optional.

## C. scripts/mcp_client.py (free AI through your own key)
A standalone client built on the official MCP library. It imports nothing from the gateway.

    python3 -m venv ~/Desktop/urumi/venv-client
    source ~/Desktop/urumi/venv-client/bin/activate
    pip install "mcp>=1.30,<2" httpx

    export LLM_API_KEY=$(kubectl -n gateway get secret llm-credentials -o jsonpath='{.data.api-key}' | base64 -d)
    python scripts/mcp_client.py tools
    python scripts/mcp_client.py call woocommerce__list_orders '{"period": "last_7_days"}'
    python scripts/mcp_client.py ask "What orders came in during the last 7 days?"
    python scripts/mcp_client.py ask "Add up the totals of my orders from the last 7 days and save the total as a note called Weekly revenue."
    python scripts/mcp_client.py chat

If `URUMI_API_KEY` is not exported, the script asks for it with a hidden prompt. The AI key (`LLM_API_KEY`)
belongs to this client and goes only to the AI service; the gateway key goes only to the gateway.
Calls made this way appear in the audit log under your name, marked as coming from an MCP client.

Remember that an external client brings its own AI model, so the gateway cannot control how that model treats
tool output. Tool descriptions and results are untrusted text (see the threat model).

## Which part of the definition of done does each cover?
- Connect with only the gateway URL and user B's key, and see every enabled server's tools: A, B or C (`tools`).
- Ask the orders question: C (`ask`), or A by calling `woocommerce__list_orders`.
- One prompt that needs two servers: C (`ask` with the notes prompt above).
- Revoke user B's key, then the next call is rejected: any of them, for example `tools` prints
  "The gateway rejected this API key".
