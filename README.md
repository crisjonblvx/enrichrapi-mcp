# enrichrapi-mcp

<!-- mcp-name: io.github.crisjonblvx/enrichr-api -->

MCP (Model Context Protocol) server for [Enrichr](https://enrichrapi.dev) — exposes Enrichr's **50 billed utilities** to AI coding assistants like Claude Desktop, Cursor, VS Code, and any other MCP client.

> **One install. Start free. Add prepaid credits only when you need them.** Validate emails (syntax + MX, not mailbox), parse phones, geolocate IPs over HTTPS, decode JWTs, sign webhooks, parse cron expressions, convert currencies, generate QR codes, count LLM tokens, and the rest of the catalog.

## Try Enrichr in 60 seconds

```bash
uvx enrichrapi-mcp
```

You do **not** need an Enrichr API key just to launch the MCP server. In your MCP client, ask Enrichr to:

1. `list_catalog` — browse the live utility catalog and prices without a key.
2. `signup` — create a key when you are ready to make billed utility calls.
3. Verify the email address tied to that key to unlock **1,000 free calls/month**.
4. Add the returned `enr_...` key as `ENRICHR_API_KEY` and restart the server.
5. Use any named tool or `call_enrichr`.

No card is required to start. Enrichr does not create a metered subscription when you sign up.

### When you outgrow the free tier

Use `account_usage` to see your remaining free calls and prepaid balance. If you need more:

1. Call `account_options` to confirm prepaid checkout is available.
2. Call `start_checkout` to get Enrichr's server-controlled Stripe Checkout URL.
3. Add **$10 prepaid credits**. There is no recurring subscription.

You can also manage credits directly at <https://enrichrapi.dev/billing>.

## Install

```bash
# uvx — recommended, no global install needed
uvx enrichrapi-mcp

# or pipx
pipx install enrichrapi-mcp
enrichrapi-mcp

# or pip in a venv
pip install enrichrapi-mcp
```

Prefer to create the key outside the MCP client? The public signup endpoint is available directly:

```bash
curl -X POST https://enrichrapi.dev/v1/account/signup \
  -H "Content-Type: application/json" \
  -d '{"email":"you@example.com"}'
```

## Configure your MCP client

### Claude Desktop

You can launch without `ENRICHR_API_KEY` for catalog/signup tools. After signup, add the returned key and restart the MCP server:

```json
{
  "mcpServers": {
    "enrichr": {
      "command": "uvx",
      "args": ["enrichrapi-mcp"],
      "env": {
        "ENRICHR_API_KEY": "enr_your_api_key"
      }
    }
  }
}
```

### Cursor / VS Code (Cline)

Use the same command/config under the client's MCP settings UI. `ENRICHR_API_KEY` is optional for launch and required for billed utility calls.

## Available tools (selected)

The server exposes 30+ MCP tools backed by the public Enrichr REST API. Highlights:

| Tool | What it does |
|------|--------------|
| `list_catalog` | Live billed catalog (`outbound_io_only` filter) |
| `signup` | Provision a new Enrichr API key without an existing key |
| `account_usage` | Remaining free-tier calls + prepaid balance |
| `account_options` | Whether purchases are live + top-up amount |
| `start_checkout` | Stripe Checkout URL for the server top-up |
| `call_enrichr` | Invoke any catalog or account path by name |
| `enrich_email` | Syntax + optional MX DNS; disposable flags (not mailbox) |
| `enrich_email_batch` | Up to 100 emails; billed per item; MX deduped |
| `validate_domain` | Format + DNS A + MX (not mailbox) |
| `validate_domain_batch` | Up to 100 domains; billed per item |
| `enrich_phone` | E.164 normalization + line-type detection |
| `enrich_ip` | HTTPS country / region / city / ISP geolocation |
| `convert_currency` | ECB rates, daily |
| `convert_timezone` | DST-aware IANA timezone conversion |
| `convert_units` | length/weight/temp/area/volume |
| `validate_credit_card` | Luhn + network detection (number is never logged) |
| `validate_iban` | MOD-97 checksum, 77 countries |
| `validate_vat` | Live VIES check for EU VAT numbers |
| `check_password_breach` | HIBP k-anonymity, never sends plaintext |
| `count_llm_tokens` | tiktoken for OpenAI; approximations for 25+ other models |
| `jwt_decode` | Decode + optional HMAC verify (HS256/384/512) |
| `webhook_sign` | HMAC sign with templating (Stripe-style) |
| `webhook_verify` | Constant-time HMAC verify |
| `cron_next` | Next N runs of a 5-field cron / `@daily` etc. |
| `generate_qr` | base64 PNG + data URI |
| `generate_password` | secrets-module backed, with entropy estimate |
| `billing_portal` | Open a Stripe Billing Portal session |
| `rotate_api_key` | Atomically rotate the Enrichr key (old key revoked) |

Full endpoint list: <https://enrichrapi.dev/llms.txt>

## Pricing

- First **1,000 calls/month free** after email verification.
- After that: most endpoints **$0.00001/call** (QR, postal, profanity, address, classify, …); email/phone/IP $0.0001; VAT/IBAN $0.0005; contact cleaner $0.001/row.
- Prepaid credits are server-controlled; the public site currently offers **$10 prepaid credits** at <https://enrichrapi.dev/billing>.
- This API will not do mailbox SMTP, USPS/geocode, trained NLP, or unpublished latency SLOs.
- Live catalog: <https://enrichrapi.dev/v1/catalog>

## Repository

Source: <https://github.com/crisjonblvx/enrichrapi-mcp>

MCP Registry name: `io.github.crisjonblvx/enrichr-api`

## License

MIT
