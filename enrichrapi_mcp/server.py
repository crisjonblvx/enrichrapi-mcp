"""
Enrichr MCP Server
==================
Exposes Enrichr's data enrichment tools to AI coding assistants
(Claude Desktop, Cursor, VS Code with Cline, etc.) via the Model Context Protocol.

Setup
-----
1. pip install "mcp[cli]" httpx
2. Set ENRICHR_API_KEY env var (get a key at https://enrichrapi.dev)
3. Add to your Claude Desktop config (~/.claude/claude_desktop_config.json):

   {
     "mcpServers": {
       "enrichr": {
         "command": "python",
         "args": ["/absolute/path/to/mcp_server.py"],
         "env": { "ENRICHR_API_KEY": "your-key-here" }
       }
     }
   }

Or with uvx after publishing to PyPI:
   "command": "uvx", "args": ["enrichrapi-mcp"]
"""
from __future__ import annotations

import os

import httpx
from typing import List

from mcp.server.fastmcp import FastMCP

from .http import (
    ACCOUNT_POST,
    error_payload,
    is_get_path,
    normalize_path,
    parse_error_body,
    unwrap_success,
)

API_KEY = os.environ.get("ENRICHR_API_KEY", "")
BASE_URL = os.environ.get(
    "ENRICHR_BASE_URL",
    "https://enrichrapi.dev",
)

mcp = FastMCP(
    "Enrichr",
    instructions=(
        "Enrichr is a micro-toll utility API. "
        "Prefer list_catalog (filter outbound_io_only=true) then call_enrichr, "
        "or the named tools. Emails: syntax + optional MX, not mailbox/SMTP. "
        "Addresses: normalize, not geocode. IPs: HTTPS geo; degrades as "
        "geolocation_unavailable. Text classify: keyword heuristics. "
        "Latency is not a published SLO. Check account_usage before bulk loops. "
        "On 402 payment_required: if purchases_enabled, call start_checkout and "
        "ask the user to open the returned Stripe URL; if purchases_enabled is false, "
        "stop and tell the user checkout is not live yet. "
        "Priced tools return inner data fields plus _meta (ok, cost_usd, "
        "call_count_this_month). Failures return {ok:false, error:{code,retryable,status,detail}} "
        "instead of throwing. Pricing is fractions of a cent per call with "
        "1,000 free calls/month."
    ),
)

_allowed_post: set[str] | None = None


def _headers(*, auth: bool = True) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if auth and API_KEY:
        headers["X-Api-Key"] = API_KEY
    return headers


async def _request(
    method: str,
    path: str,
    body: dict | None = None,
    *,
    auth: bool = True,
    unwrap: bool = True,
) -> dict:
    url = f"{BASE_URL}{path}"
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.request(
                method,
                url,
                headers=_headers(auth=auth),
                json=body if method.upper() != "GET" else None,
            )
    except httpx.HTTPError as exc:
        return error_payload(
            status=None,
            detail=str(exc),
            code="network_error",
            retryable=True,
        )
    try:
        payload = resp.json()
    except Exception:
        payload = {"detail": resp.text}
    if not resp.is_success:
        return parse_error_body(resp.status_code, payload, dict(resp.headers))
    if unwrap:
        return unwrap_success(payload)
    return payload if isinstance(payload, dict) else {"data": payload}


async def _post(path: str, body: dict) -> dict:
    return await _request("POST", path, body)


async def _get(path: str, *, auth: bool = True, unwrap: bool = False) -> dict:
    return await _request("GET", path, auth=auth, unwrap=unwrap)


async def _catalog_post_paths() -> set[str]:
    global _allowed_post
    if _allowed_post is not None:
        return _allowed_post
    cat = await _get("/v1/catalog", auth=False, unwrap=False)
    routes = cat.get("routes") if isinstance(cat, dict) else None
    allowed = set(ACCOUNT_POST)
    if isinstance(routes, list):
        for row in routes:
            if isinstance(row, dict) and row.get("path"):
                allowed.add(row["path"])
        _allowed_post = allowed
    return allowed


@mcp.tool()
async def enrich_email(email: str, check_mx: bool = True) -> dict:
    """
    Validate and enrich an email address.

    Syntax-validates the address, optionally looks up MX records via DNS-over-HTTPS,
    and flags known disposable / free providers. MX means the domain publishes mail
    exchangers — not that the mailbox exists or will accept mail.

    Args:
        email: The email address to validate (e.g. "user@example.com")
        check_mx: When true (default), perform an MX DNS lookup. When false, syntax only.

    Returns:
        dict with keys: valid, format_ok, normalized, domain, is_disposable, disposable,
        validation_scope (syntax | syntax_and_mx), mx_ok, has_mx, deliverability_checked
        (always false), likely_deliverable (always null), cost_usd
    """
    return await _post("/v1/enrich/email", {"email": email, "check_mx": check_mx})


@mcp.tool()
async def enrich_email_batch(emails: List[str], check_mx: bool = True) -> dict:
    """
    Validate up to 100 emails in one call (billed per item).

    Same honesty as enrich_email: MX ≠ mailbox. Duplicate domains share one MX lookup.

    Args:
        emails: 1–100 email addresses
        check_mx: When true (default), perform MX DNS lookups (deduped by domain)

    Returns:
        dict with keys: ok, count, results (list of {email, data}), cost_usd,
        call_count_this_month — or {ok:false, error:{...}}
    """
    return await _request(
        "POST",
        "/v1/enrich/email/batch",
        {"emails": emails, "check_mx": check_mx},
        unwrap=False,
    )


@mcp.tool()
async def enrich_phone(phone: str, country_hint: str = "US") -> dict:
    """
    Parse and validate a phone number.

    Normalizes to E.164 format, detects line type (mobile/landline/voip),
    and returns the carrier region.

    Args:
        phone:        Phone number in any format (e.g. "415-555-2671", "+44 20 7946 0958")
        country_hint: ISO 3166-1 alpha-2 country code to assume when no country prefix
                      is given (default "US")

    Returns:
        dict with keys: valid, e164, national, country_code, line_type, cost_usd
    """
    return await _post("/v1/enrich/phone", {"phone": phone, "country_hint": country_hint})


@mcp.tool()
async def enrich_address(
    street: str,
    city: str,
    state: str = "",
    postal_code: str = "",
    country: str = "US",
) -> dict:
    """
    Normalize a postal address.

    Title-cases components and regex-checks US ZIP codes. This is not USPS
    validation or geocoding.

    Args:
        street:      Street line (e.g. "123 Main St")
        city:        City name
        state:       State / province abbreviation (e.g. "CA")
        postal_code: ZIP or postal code
        country:     ISO 3166-1 alpha-2 country code (default "US")

    Returns:
        dict with keys: normalized, us_zip_valid, one_line, method (normalization),
        geocoded (false), usps_validated (false), cost_usd
    """
    return await _post(
        "/v1/enrich/address",
        {
            "street": street,
            "city": city,
            "state": state,
            "postal_code": postal_code,
            "country": country,
        },
    )


@mcp.tool()
async def enrich_ip(ip: str) -> dict:
    """
    Geolocate an IP address over HTTPS.

    Public IPs are looked up via ipinfo.io (if IPINFO_TOKEN is configured) or
    ipwho.is. Private/loopback addresses are classified locally. Vendor failures
    return error=geolocation_unavailable with null coordinates — never invented lat/lon.

    Args:
        ip: IPv4 or IPv6 address (e.g. "8.8.8.8")

    Returns:
        dict with keys: valid, country, country_code, region, city, isp,
        timezone, lat, lon, error (when unavailable), cost_usd
    """
    return await _post("/v1/enrich/ip", {"ip": ip})


@mcp.tool()
async def list_catalog(outbound_io_only: bool = False) -> dict:
    """
    Live billed-utility catalog (unauthenticated, not billed).

    Use this to discover routes, prices, rate limits, and outbound_io flags.
    Then call a named tool or call_enrichr(path, body).

    Args:
        outbound_io_only: If true, keep only routes that hit external systems
            (VAT, HIBP, IP geo, MX, currency, postal, …).

    Returns:
        Catalog payload: ok, billed_utility_count, routes[{path, description,
        price_usd, billing, outbound_io, rate_limit, ...}]
    """
    payload = await _get("/v1/catalog", auth=False, unwrap=False)
    if (
        outbound_io_only
        and isinstance(payload, dict)
        and isinstance(payload.get("routes"), list)
    ):
        payload = {
            **payload,
            "routes": [r for r in payload["routes"] if r.get("outbound_io")],
        }
    return payload


@mcp.tool()
async def call_enrichr(path: str, body: dict | None = None) -> dict:
    """
    Call any billed catalog route or account endpoint by path.

    Closes MCP coverage gaps without a dedicated wrapper. Body is the JSON
    object the REST API expects (e.g. {"email": "..."} for /v1/enrich/email).
    GET is used automatically for /v1/catalog and /v1/account/usage|options.

    Args:
        path: API path such as /v1/validate/domain or /v1/analyze/text
        body: JSON body for POST routes (ignored for GET)

    Returns:
        Unwrapped priced payload, raw custom envelope, or {ok:false, error:{...}}
    """
    path = normalize_path(path)
    if is_get_path(path):
        return await _get(path, auth=path != "/v1/catalog", unwrap=False)
    allowed = await _catalog_post_paths()
    if path not in allowed:
        return error_payload(
            status=400,
            detail=f"Path not in Enrichr catalog or account routes: {path}",
            code="bad_request",
            retryable=False,
        )
    return await _request(
        "POST",
        path,
        body or {},
        auth=path not in {
            "/v1/account/signup",
            "/v1/account/recover",
            "/v1/account/verify",
        },
        unwrap=True,
    )


@mcp.tool()
async def account_usage() -> dict:
    """
    Current-month usage and prepaid balance for this API key.

    Call this before a bulk loop. 402 payment_required means the free allowance
    (or prepaid balance) is exhausted.

    Returns:
        dict with keys: month, calls, free_tier_remaining, estimated_charge_usd,
        balance_usd
    """
    return await _get("/v1/account/usage", unwrap=False)


@mcp.tool()
async def account_options() -> dict:
    """
    Whether credit purchases are live, the server top-up amount, and billing URL.

    Unauthenticated. Call this (or read a 402 body) before start_checkout.

    Returns:
        dict with keys: purchases_enabled, topup_usd, free_tier_calls,
        billing_url, checkout_path
    """
    return await _get("/v1/account/options", auth=False, unwrap=False)


@mcp.tool()
async def start_checkout() -> dict:
    """
    Open Stripe Checkout for the server-configured prepaid top-up.

    Returns a hosted Checkout URL the human must open. Credits land only after
    Stripe sends checkout.session.completed to /v1/webhooks/stripe.
    If purchases are not enabled, returns {ok:false, error:{code:service_unavailable}}
    plus purchases_enabled=false.

    Returns:
        dict with keys: url (or ok/false error)
    """
    return await _request("POST", "/v1/account/checkout", {}, unwrap=False)


@mcp.tool()
async def classify_text(text: str) -> dict:
    """
    Classify a piece of text with keyword heuristics.

    Not a trained NLP or toxicity model. Scores sentiment (positive/negative/neutral),
    keyword toxicity, keyword spam, and optional langdetect language.

    Args:
        text: The text to classify (up to ~5,000 characters recommended)

    Returns:
        dict with keys: method (heuristic), sentiment, toxicity_score,
        spam_score, language, char_count, word_count, cost_usd
    """
    return await _post("/v1/classify/text", {"text": text})


@mcp.tool()
async def signup(email: str) -> dict:
    """
    Create an Enrichr API key.

    The raw key is returned once. The free monthly allowance and lost-key
    recovery require verifying the mailbox via POST /v1/account/verify
    (token emailed by Resend). Unverified keys do not receive the free tier.

    Args:
        email: The user's email address

    Returns:
        dict with keys: api_key, message, email_verified, verification_required
    """
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            f"{BASE_URL}/v1/account/signup",
            json={"email": email},
        )
        resp.raise_for_status()
        return resp.json()


@mcp.tool()
async def convert_currency(amount: float, from_currency: str, to_currency: str) -> dict:
    """
    Convert an amount between any two currencies.

    Uses ECB (European Central Bank) rates updated daily via frankfurter.dev.
    Rates are cached for 1 hour to minimize latency.

    Args:
        amount:        The amount to convert (e.g. 100.0)
        from_currency: ISO 4217 source currency code (e.g. "USD", "EUR", "GBP")
        to_currency:   ISO 4217 target currency code (e.g. "JPY", "CAD", "CHF")

    Returns:
        dict with keys: amount, from, to, result, rate, date, cost_usd
    """
    return await _post(
        "/v1/convert/currency",
        {"amount": amount, "from_currency": from_currency, "to_currency": to_currency},
    )


@mcp.tool()
async def convert_timezone(datetime: str, from_tz: str, to_tz: str) -> dict:
    """
    Convert a datetime from one timezone to another (DST-aware).

    Uses Python's built-in IANA timezone database. No external API needed.

    Args:
        datetime: ISO 8601 datetime string (e.g. "2024-06-15T14:30:00")
        from_tz:  IANA source timezone (e.g. "America/New_York", "UTC")
        to_tz:    IANA target timezone (e.g. "Asia/Tokyo", "Europe/Berlin")

    Returns:
        dict with keys: result, result_date, result_time, utc_offset, is_dst, cost_usd
    """
    return await _post(
        "/v1/convert/timezone",
        {"datetime": datetime, "from_tz": from_tz, "to_tz": to_tz},
    )


@mcp.tool()
async def validate_vat(vat_number: str) -> dict:
    """
    Validate a European VAT number.

    Checks format against country-specific patterns (all 27 EU member states + GB),
    then verifies against the live EU VIES database for company name and address.

    Args:
        vat_number: VAT number with country prefix (e.g. "DE123456789", "FR12345678901")

    Returns:
        dict with keys: valid, country_code, format_valid, vies_valid,
        company_name, company_address, cost_usd
    """
    return await _post("/v1/validate/vat", {"vat_number": vat_number})


@mcp.tool()
async def validate_domain(domain: str) -> dict:
    """
    Validate a domain: format, DNS A record, and MX (not mailbox/SMTP).

    Args:
        domain: Hostname or URL (e.g. "example.com" or "https://example.com/path")

    Returns:
        dict with keys: valid, domain, format_valid, resolves, has_mx, mx_records,
        is_disposable, tld, cost_usd
    """
    return await _post("/v1/validate/domain", {"domain": domain})


@mcp.tool()
async def validate_domain_batch(domains: List[str]) -> dict:
    """
    Validate up to 100 domains in one call (billed per item).

    Same checks as validate_domain. Not mailbox verification.

    Args:
        domains: 1–100 hostnames or URLs

    Returns:
        dict with keys: ok, count, results (list of {domain, data}), cost_usd,
        call_count_this_month — or {ok:false, error:{...}}
    """
    return await _request(
        "POST",
        "/v1/validate/domain/batch",
        {"domains": domains},
        unwrap=False,
    )


@mcp.tool()
async def generate_qr(content: str, box_size: int = 10, border: int = 4) -> dict:
    """
    Generate a QR code from any text or URL.

    Returns a base64-encoded PNG and a ready-to-use HTML data URI.
    Works for URLs, plain text, vCards, WiFi credentials, payment links, etc.

    Args:
        content:  The text or URL to encode
        box_size: Pixel size of each QR module, 1–20 (default 10)
        border:   Quiet-zone border width in modules, 1–10 (default 4)

    Returns:
        dict with keys: image (base64 PNG), data_uri, qr_version, modules, cost_usd
    """
    return await _post(
        "/v1/generate/qr",
        {"content": content, "box_size": box_size, "border": border},
    )


@mcp.tool()
async def filter_profanity(text: str) -> dict:
    """
    Detect and censor profanity in text.

    Uses a curated English profanity list to flag and censor offensive words.
    The censored version replaces flagged words with asterisks.

    Args:
        text: The text to check (e.g. "This is some sample text")

    Returns:
        dict with keys: contains_profanity, censored, flagged_word_count,
        profanity_ratio, cost_usd
    """
    return await _post("/v1/filter/profanity", {"text": text})


@mcp.tool()
async def check_password_breach(password: str) -> dict:
    """
    Check if a password has appeared in known data breaches and assess its strength.

    Uses the HaveIBeenPwned k-anonymity API — only the first 5 characters of the
    SHA-1 hash are sent externally. The plaintext password never leaves the server.

    Args:
        password: The password to check (never logged or stored)

    Returns:
        dict with keys: breached, breach_count, strength, score (0-7),
        entropy_bits, length, cost_usd
    """
    return await _post("/v1/validate/password", {"password": password})


@mcp.tool()
async def lookup_postal_code(postal_code: str, country: str = "US") -> dict:
    """
    Look up city, state, and coordinates for a postal code.

    Supports US ZIP codes and postal codes for 60+ countries via Zippopotam.us.

    Args:
        postal_code: The postal/ZIP code to look up (e.g. "90210", "EC1A 1BB")
        country:     ISO 3166-1 alpha-2 country code (default "US")

    Returns:
        dict with keys: valid, postal_code, country, country_code,
        city, state, state_abbreviation, lat, lon, places (list), cost_usd
    """
    return await _post(
        "/v1/lookup/postal",
        {"postal_code": postal_code, "country": country},
    )


@mcp.tool()
async def validate_credit_card(number: str) -> dict:
    """
    Validate a credit card number.

    Performs Luhn checksum verification and detects the card network
    (Visa, Mastercard, Amex, Discover, UnionPay, etc.). The full card
    number is never logged or stored — only a masked version is returned.

    Args:
        number: Card number with optional spaces or dashes (e.g. "4111 1111 1111 1111")

    Returns:
        dict with keys: valid, luhn_valid, length_valid, network, length, masked, cost_usd
    """
    return await _post("/v1/validate/credit-card", {"number": number})


@mcp.tool()
async def validate_iban(iban: str) -> dict:
    """
    Validate an IBAN (International Bank Account Number).

    Checks format and length for 77 countries and verifies the ISO 7064
    MOD-97-10 checksum. No external API — pure math.

    Args:
        iban: IBAN string with optional spaces (e.g. "DE89 3704 0044 0532 0130 00")

    Returns:
        dict with keys: valid, country_code, country, length, expected_length,
        length_valid, checksum_valid, bban, formatted, cost_usd
    """
    return await _post("/v1/validate/iban", {"iban": iban})


@mcp.tool()
async def generate_uuid(version: int = 4, count: int = 1) -> dict:
    """
    Generate one or more UUIDs.

    Supports UUID v1 (MAC address + timestamp) and v4 (random).
    Up to 100 UUIDs per request.

    Args:
        version: UUID version — 1 or 4 (default 4)
        count:   Number of UUIDs to generate, 1–100 (default 1)

    Returns:
        dict with keys: uuid (first result), uuids (list), version, count, cost_usd
    """
    return await _post("/v1/generate/uuid", {"version": version, "count": count})


@mcp.tool()
async def generate_hash(
    text: str,
    algorithm: str = "sha256",
    encoding: str = "hex",
) -> dict:
    """
    Hash text using a cryptographic hash function.

    Supports MD5, SHA-1, SHA-224, SHA-256, SHA-384, SHA-512.
    Returns the digest in hex, base64, or both.

    Args:
        text:      The string to hash
        algorithm: Hash function — md5 | sha1 | sha224 | sha256 | sha384 | sha512 (default sha256)
        encoding:  Output format — hex | base64 | both (default hex)

    Returns:
        dict with keys: hex and/or base64, algorithm, input_length, digest_bits, cost_usd
    """
    return await _post(
        "/v1/generate/hash",
        {"text": text, "algorithm": algorithm, "encoding": encoding},
    )


@mcp.tool()
async def parse_url(url: str) -> dict:
    """
    Parse a URL into its components.

    Extracts scheme, host, domain, subdomain, TLD, port, path, path segments,
    query parameters, UTM tracking tags, and fragment.

    Args:
        url: The URL to parse (e.g. "https://example.com/path?utm_source=google#section")

    Returns:
        dict with keys: valid, scheme, host, subdomain, domain, tld, port, path,
        path_segments, params, utm_tags, has_utm, fragment, cost_usd
    """
    return await _post("/v1/parse/url", {"url": url})


@mcp.tool()
async def convert_units(
    value: float,
    from_unit: str,
    to_unit: str,
    category: str | None = None,
) -> dict:
    """
    Convert a value between units of measurement.

    Supports length, weight, temperature, area, and volume.
    Category is auto-detected from unit names.

    Args:
        value:     The numeric value to convert
        from_unit: Source unit (e.g. "km", "kg", "celsius", "gal", "ft2")
        to_unit:   Target unit (e.g. "mi", "lb", "fahrenheit", "l", "m2")
        category:  Optional: "length", "weight", "temperature", "area", "volume"

    Returns:
        dict with keys: value, from_unit, to_unit, result, category, cost_usd
    """
    return await _post(
        "/v1/convert/units",
        {"value": value, "from_unit": from_unit, "to_unit": to_unit, "category": category},
    )


@mcp.tool()
async def generate_password(
    length: int = 16,
    symbols: bool = True,
    numbers: bool = True,
    uppercase: bool = True,
) -> dict:
    """
    Generate a secure random password.

    Uses Python's cryptographically secure secrets module.
    Returns the password and an entropy estimate in bits.

    Args:
        length:    Password length, 4–256 (default 16)
        symbols:   Include symbols like !@#$%^&* (default True)
        numbers:   Include digits 0-9 (default True)
        uppercase: Include uppercase letters (default True)

    Returns:
        dict with keys: password, length, entropy_bits, has_uppercase,
        has_numbers, has_symbols, cost_usd
    """
    return await _post(
        "/v1/generate/password",
        {"length": length, "symbols": symbols, "numbers": numbers, "uppercase": uppercase},
    )


@mcp.tool()
async def generate_slug(
    text: str,
    separator: str = "-",
    max_length: int | None = None,
) -> dict:
    """
    Generate a URL-safe slug from any text.

    Handles Unicode normalization (café → cafe), removes punctuation,
    collapses whitespace, and lowercases the result.

    Args:
        text:       The text to slugify (e.g. "Hello World! Café & More")
        separator:  Word separator — "-", "_", or "." (default "-")
        max_length: Optional maximum slug length

    Returns:
        dict with keys: slug, original, separator, length, cost_usd
    """
    return await _post(
        "/v1/generate/slug",
        {"text": text, "separator": separator, "max_length": max_length},
    )


@mcp.tool()
async def parse_user_agent(user_agent: str) -> dict:
    """
    Parse a User-Agent string into browser, OS, and device details.

    Detects 7 browsers, 9 OS variants, device type (desktop/mobile/tablet),
    and 17+ known bots including Googlebot, GPTBot, and ClaudeBot.

    Args:
        user_agent: The full User-Agent header value

    Returns:
        dict with keys: is_bot, bot_name, browser, browser_version, os,
        os_version, device_type, cost_usd
    """
    return await _post("/v1/parse/user-agent", {"user_agent": user_agent})


@mcp.tool()
async def validate_json_string(json_string: str) -> dict:
    """
    Validate, format, and analyze a JSON string.

    Returns whether the JSON is valid, root type, key/item count,
    nesting depth, a pretty-printed version, and a minified version.
    On error, returns the line and column of the syntax mistake.

    Args:
        json_string: The raw JSON string to validate (e.g. '{"key": "value"}')

    Returns:
        dict with keys: valid, root_type, key_count, item_count, depth,
        formatted, minified (or error, line, column), cost_usd
    """
    return await _post("/v1/validate/json", {"json_string": json_string})


@mcp.tool()
async def validate_color(color: str) -> dict:
    """
    Validate a color and convert it between hex, rgb, and hsl formats.

    Accepts hex (#FF5733 or shorthand #F53), rgb(255,87,51), rgba(),
    hsl(14, 100%, 60%), and hsla(). Always returns all three formats
    plus a dark/light indicator based on WCAG luminance.

    Args:
        color: Color string in any supported format

    Returns:
        dict with keys: valid, input_format, hex, rgb, rgb_string,
        hsl, hsl_string, is_dark, cost_usd
    """
    return await _post("/v1/validate/color", {"color": color})


@mcp.tool()
async def validate_regex(
    pattern: str,
    test_string: str,
    flags: list[str] | None = None,
    max_matches: int = 50,
) -> dict:
    """
    Test a regex pattern against a string and return all matches.

    Compiles the pattern with optional flags, then returns every match with
    its start/end positions, capture groups, and named groups.

    Args:
        pattern:     The regex pattern to compile (e.g. r"\\d+")
        test_string: The string to match against
        flags:       Optional list of flag letters: "i" (ignore case), "m" (multiline),
                     "s" (dotall), "x" (verbose)
        max_matches: Maximum number of matches to return, 1–200 (default 50)

    Returns:
        dict with keys: valid_pattern, pattern, is_match, match_count,
        first_match, matches (list with start/end/groups), cost_usd
    """
    return await _post(
        "/v1/validate/regex",
        {
            "pattern": pattern,
            "test_string": test_string,
            "flags": flags or [],
            "max_matches": max_matches,
        },
    )


@mcp.tool()
async def validate_uuid(uuid: str) -> dict:
    """
    Validate a UUID string and extract its metadata.

    Checks the standard xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx format and detects
    the version (v1 time-based, v3 MD5, v4 random, v5 SHA-1).

    Args:
        uuid: UUID string (e.g. "550e8400-e29b-41d4-a716-446655440000")

    Returns:
        dict with keys: valid, uuid (normalized), version, version_description,
        variant, urn, hex, int, cost_usd
    """
    return await _post("/v1/validate/uuid", {"uuid": uuid})


@mcp.tool()
async def convert_markdown(markdown: str) -> dict:
    """
    Convert a Markdown string to HTML.

    Handles headings (h1-h6), bold, italic, bold+italic, strikethrough,
    inline code, fenced code blocks with language class, links, images,
    blockquotes, unordered/ordered lists, and horizontal rules.

    Args:
        markdown: Raw Markdown string

    Returns:
        dict with keys: html, original_length, html_length, cost_usd
    """
    return await _post("/v1/convert/markdown", {"markdown": markdown})


@mcp.tool()
async def encode_decode(
    text: str,
    codec: str = "base64",
    operation: str = "encode",
) -> dict:
    """
    Encode or decode a string using a common encoding scheme.

    Supports: base64, base64url, hex, url (percent-encoding),
    html (entity encoding), rot13.

    Args:
        text:      The string to encode or decode
        codec:     Encoding scheme — base64 | base64url | hex | url | html | rot13
        operation: "encode" or "decode"

    Returns:
        dict with keys: operation, codec, input, output, input_bytes,
        output_length (or error), cost_usd
    """
    return await _post(
        "/v1/convert/encoding",
        {"text": text, "codec": codec, "operation": operation},
    )


@mcp.tool()
async def lookup_timezone(timezone: str) -> dict:
    """
    Look up timezone information by IANA timezone name.

    Returns the current UTC offset, DST status, abbreviation (e.g. EST/EDT),
    current local time, and current UTC time. Uses Python's stdlib zoneinfo.

    Args:
        timezone: IANA timezone name (e.g. "America/New_York", "Europe/London", "Asia/Tokyo")

    Returns:
        dict with keys: valid, timezone, utc_offset, utc_offset_seconds,
        abbreviation, is_dst, dst_offset_seconds, local_time, utc_time, cost_usd
    """
    return await _post("/v1/lookup/timezone", {"timezone": timezone})


@mcp.tool()
async def lookup_mime_type(query: str) -> dict:
    """
    Look up a MIME type by file extension or filename, or reverse-lookup extensions.

    Forward: pass "png", ".png", "photo.png", or "archive.tar.gz"
    Reverse: pass "image/png" or "application/json"

    Args:
        query: File extension, filename, or MIME type string

    Returns:
        dict with keys: query, mode (forward/reverse), mime_type, extensions (for reverse),
        extension (for forward), category, is_binary, is_text, found, cost_usd
    """
    return await _post("/v1/lookup/mime-type", {"query": query})


@mcp.tool()
async def count_llm_tokens(text: str, model: str = "gpt-4o") -> dict:
    """
    Count the tokens in text for any major LLM model.

    Returns the exact token count (or a close approximation for non-OpenAI models),
    how much of the model's context window is consumed, tokens remaining, and the
    estimated input cost. Essential for prompt engineering, RAG chunk sizing, and
    context window management.

    Supported models:
      OpenAI:    gpt-5.6, gpt-5, gpt-4o, gpt-4.1, o3, o4-mini
      Anthropic: claude-opus-5, claude-sonnet-5, claude-haiku-4-5
                 (plus aliases: claude-opus-4, claude-sonnet-4)
      Google:    gemini-3.5-flash, gemini-3.1-pro, gemini-2.0-flash
      Meta:      llama-4-maverick, llama-4-scout, llama-3.3-70b
      Mistral:   mistral-large-3, mistral-small-4
      DeepSeek:  deepseek-v4-flash, deepseek-v4-pro, deepseek-v3, deepseek-r1

    OpenAI models use tiktoken counts. All other models are approximated
    with cl100k_base (accurate to ±10%). Input prices are estimates; see as_of.

    Args:
        text:  The text to count tokens for (prompt, document, message, etc.)
        model: LLM model name (default "gpt-4o")

    Returns:
        dict with keys: model, model_family, token_count, context_window,
        context_used_pct, tokens_remaining, fits_in_context,
        estimated_input_cost_usd, approximate, as_of, note, cost_usd
    """
    return await _post("/v1/analyze/tokens", {"text": text, "model": model})


@mcp.tool()
async def jwt_decode(token: str, secret: str | None = None) -> dict:
    """
    Decode a JWT and (optionally) verify the HMAC signature.

    Pure compute, no outbound network. When ``secret`` is provided we verify
    HS256/HS384/HS512 signatures; without it we just decode and return the
    header and payload. The original token is never logged.

    Args:
        token:  Full JWT string (three dot-separated base64url segments)
        secret: Optional HMAC secret. If supplied, ``signature_verified``
                will be True only if the signature matches.

    Returns:
        dict with keys: header, payload, algorithm, signature_verified, valid
    """
    body: dict = {"token": token}
    if secret is not None:
        body["secret"] = secret
    return await _post("/v1/tools/jwt-decode", body)


@mcp.tool()
async def webhook_sign(
    body: str,
    secret: str,
    algorithm: str = "sha256",
    encoding: str = "hex",
    timestamp: str | None = None,
    template: str = "{body}",
) -> dict:
    """
    Produce an HMAC signature for a webhook body.

    Useful for testing webhook receivers, generating Stripe-style
    ``{timestamp}.{body}`` signatures, or signing outbound webhooks.

    Args:
        body:       Raw request body (use exact bytes for accuracy)
        secret:     Webhook signing secret
        algorithm:  sha1 | sha256 | sha512 (default sha256)
        encoding:   hex | base64 | base64url (default hex)
        timestamp:  Optional Unix timestamp string used in the template
        template:   Format string with {body} and optional {timestamp}.
                    Stripe uses ``"{timestamp}.{body}"``.

    Returns:
        dict with keys: signature, algorithm, encoding, signing_string
    """
    return await _post(
        "/v1/tools/webhook-sign",
        {
            "body": body,
            "secret": secret,
            "algorithm": algorithm,
            "encoding": encoding,
            "timestamp": timestamp,
            "template": template,
        },
    )


@mcp.tool()
async def webhook_verify(
    body: str,
    secret: str,
    signature: str,
    algorithm: str = "sha256",
    encoding: str = "hex",
    timestamp: str | None = None,
    template: str = "{body}",
) -> dict:
    """
    Constant-time verify an HMAC webhook signature.

    Same parameters as ``webhook_sign`` plus the candidate ``signature``.
    Returns ``matches=True`` only if the signature is byte-equal under
    constant-time comparison.

    Returns:
        dict with keys: matches, algorithm, encoding, signing_string, valid
    """
    return await _post(
        "/v1/tools/webhook-verify",
        {
            "body": body,
            "secret": secret,
            "signature": signature,
            "algorithm": algorithm,
            "encoding": encoding,
            "timestamp": timestamp,
            "template": template,
        },
    )


@mcp.tool()
async def cron_next(
    expression: str,
    from_iso: str | None = None,
    count: int = 5,
) -> dict:
    """
    Compute the next N runs of a 5-field cron expression.

    Supports the standard aliases: ``@hourly``, ``@daily``, ``@weekly``,
    ``@monthly``, ``@yearly``.

    Args:
        expression: Standard cron (e.g. ``"*/15 * * * *"``) or @alias
        from_iso:   ISO-8601 starting point in UTC. Defaults to "now".
        count:      Number of runs to return, 1–100 (default 5)

    Returns:
        dict with keys: expression, runs (list of ISO timestamps), count
    """
    return await _post(
        "/v1/tools/cron-next",
        {"expression": expression, "from_iso": from_iso, "count": count},
    )


@mcp.tool()
async def billing_portal() -> dict:
    """
    Open a Stripe Billing Portal session for the current API key.

    Use this when the user wants to update their payment method,
    cancel, or download invoices. Returns a URL the user should
    open in their browser; the URL expires after a few minutes.

    Returns:
        dict with keys: url (or null + note if Stripe not configured)
    """
    return await _post("/v1/account/portal", {})


@mcp.tool()
async def rotate_api_key() -> dict:
    """
    Rotate the current Enrichr API key.

    The OLD key is deactivated immediately on the server. Stripe billing
    continues against the same customer/subscription so usage is not
    interrupted. Show the returned ``api_key`` to the user once and
    instruct them to store it securely — it cannot be retrieved later.

    Returns:
        dict with keys: api_key, message
    """
    return await _post("/v1/account/rotate", {})


def main() -> None:
    """Entry point for the ``enrichrapi-mcp`` console script."""
    mcp.run()


if __name__ == "__main__":
    main()
