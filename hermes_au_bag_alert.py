#!/usr/bin/env python3
"""
Hermes Australia women's bags monitor.

It checks the Hermes AU women's bags category, remembers previously seen
products, and emails only newly listed bags.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import smtplib
import ssl
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from email.message import EmailMessage
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.request import Request, urlopen


DEFAULT_URL = (
    "https://www.hermes.com/au/en/category/leather-goods/"
    "bags-and-clutches/womens-bags-and-clutches/"
)
DEFAULT_RECIPIENT = ""
DEFAULT_WATCH_TERMS = ["Neo Garden 23", "Herbag Zip 20 bag"]
DEFAULT_STATE_FILE = "hermes_au_bag_state.json"
REQUEST_TIMEOUT_SECONDS = 30
TRANSIENT_HTTP_CODES = {403, 408, 425, 429, 500, 502, 503, 504}


class TransientFetchError(RuntimeError):
    pass


@dataclass(frozen=True)
class Product:
    product_id: str
    name: str
    color: str
    price: str
    url: str


class VisibleTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tokens: list[tuple[str, str | None]] = []
        self._skip_depth = 0
        self._href_stack: list[str | None] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "noscript", "svg"}:
            self._skip_depth += 1
            return
        if tag == "a":
            attrs_dict = dict(attrs)
            self._href_stack.append(attrs_dict.get("href"))

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "noscript", "svg"} and self._skip_depth:
            self._skip_depth -= 1
            return
        if tag == "a" and self._href_stack:
            self._href_stack.pop()

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        text = normalize_space(data)
        if text:
            href = self._href_stack[-1] if self._href_stack else None
            self.tokens.append((text, href))


def normalize_space(value: str) -> str:
    return re.sub(r"\s+", " ", value.replace("\xa0", " ")).strip()


def normalize_for_match(value: str) -> str:
    value = html.unescape(value).casefold()
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return normalize_space(value)


def canonical_url(value: str) -> str:
    parts = urlsplit(value)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def product_fingerprint(name: str, color: str, price: str, url: str) -> str:
    if url:
        raw = canonical_url(url)
    else:
        raw = "|".join([normalize_for_match(name), normalize_for_match(color), price])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def load_dotenv(dotenv_path: Path) -> None:
    if not dotenv_path.exists():
        return
    for raw_line in dotenv_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def fetch_page(url: str, attempts: int = 3, backoff_seconds: int = 10) -> str:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/126.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-AU,en;q=0.9",
        "Cache-Control": "no-cache",
    }
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        request = Request(url, headers=headers)
        try:
            with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
                charset = response.headers.get_content_charset() or "utf-8"
                return response.read().decode(charset, errors="replace")
        except HTTPError as exc:
            last_error = exc
            if exc.code not in TRANSIENT_HTTP_CODES:
                raise RuntimeError(f"Hermes returned HTTP {exc.code}: {exc.reason}") from exc
            print(
                f"Temporary Hermes HTTP {exc.code}: {exc.reason} "
                f"(attempt {attempt}/{attempts}).",
                file=sys.stderr,
            )
        except URLError as exc:
            last_error = exc
            print(
                f"Temporary network error reaching Hermes: {exc.reason} "
                f"(attempt {attempt}/{attempts}).",
                file=sys.stderr,
            )

        if attempt < attempts:
            time.sleep(backoff_seconds * attempt)

    raise TransientFetchError(f"Could not fetch Hermes page after {attempts} attempt(s).") from last_error


def parse_products(page_html: str, page_url: str) -> list[Product]:
    parser = VisibleTextParser()
    parser.feed(page_html)
    tokens = parser.tokens

    start = next(
        (idx for idx, (text, _) in enumerate(tokens) if text.casefold() == "product list"),
        0,
    )
    end = next(
        (
            idx
            for idx, (text, _) in enumerate(tokens[start + 1 :], start + 1)
            if text.casefold().startswith("the goldsmith")
            or text.casefold().startswith("breadcrumb trail")
        ),
        len(tokens),
    )

    products: list[Product] = []
    idx = start
    while idx < end:
        name, href = tokens[idx]
        if not href or not looks_like_bag_name(name):
            idx += 1
            continue

        next_link = idx + 1
        while next_link < end and not tokens[next_link][1]:
            next_link += 1
        segment = " ".join(text for text, _ in tokens[idx + 1 : next_link])

        color_match = re.search(
            r"Color:\s*(.*?)(?:,\s*)?Price\b",
            segment,
            flags=re.IGNORECASE,
        )
        price_match = re.search(
            r"Price\s+(AU\$\s*[\d,]+(?:\.\d{2})?)",
            segment,
            flags=re.IGNORECASE,
        )
        if not price_match:
            idx += 1
            continue

        url = urljoin(page_url, href)
        color = normalize_space(color_match.group(1).strip(" ,")) if color_match else ""
        price = normalize_space(price_match.group(1).replace(" ", ""))
        product_id = product_fingerprint(name, color, price, url)
        products.append(
            Product(
                product_id=product_id,
                name=normalize_space(name),
                color=color,
                price=price,
                url=canonical_url(url),
            )
        )
        idx = max(idx + 1, next_link)

    return dedupe_products(products)


def looks_like_bag_name(name: str) -> bool:
    lowered = name.casefold()
    return any(word in lowered for word in ["bag", "clutch", "pouch", "tote"])


def dedupe_products(products: Iterable[Product]) -> list[Product]:
    seen: set[str] = set()
    result: list[Product] = []
    for product in products:
        if product.product_id in seen:
            continue
        seen.add(product.product_id)
        result.append(product)
    return result


def load_state(path: Path) -> dict:
    if not path.exists():
        return {"seen_ids": [], "products": {}, "last_checked_at": None}
    return json.loads(path.read_text(encoding="utf-8"))


def save_state(path: Path, products: list[Product]) -> None:
    state = {
        "last_checked_at": datetime.now(timezone.utc).isoformat(),
        "seen_ids": [product.product_id for product in products],
        "products": {product.product_id: asdict(product) for product in products},
    }
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def matching_watch_terms(products: Iterable[Product], watch_terms: list[str]) -> list[str]:
    normalized_terms = [(term, normalize_for_match(term)) for term in watch_terms if term]
    matches: list[str] = []
    for product in products:
        product_name = normalize_for_match(product.name)
        for original, normalized in normalized_terms:
            if normalized and normalized in product_name and original not in matches:
                matches.append(original)
    return matches


def build_subject(new_products: list[Product], watch_hits: list[str]) -> str:
    if watch_hits:
        return f"\u2757 Hermes AU wishlist alert: {', '.join(watch_hits)}"
    count = len(new_products)
    return f"Hermes AU new bag alert: {count} new item{'s' if count != 1 else ''}"


def build_email_body(new_products: list[Product], page_url: str) -> tuple[str, str]:
    checked_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    plain_lines = [
        f"Found {len(new_products)} new Hermes AU bag item(s).",
        f"Checked at: {checked_at}",
        f"Category: {page_url}",
        "",
    ]
    rows = []
    for product in new_products:
        plain_lines.extend(
            [
                f"- {product.name}",
                f"  Color: {product.color or 'N/A'}",
                f"  Price: {product.price}",
                f"  URL: {product.url}",
                "",
            ]
        )
        rows.append(
            "<tr>"
            f"<td>{html.escape(product.name)}</td>"
            f"<td>{html.escape(product.color or 'N/A')}</td>"
            f"<td>{html.escape(product.price)}</td>"
            f"<td><a href=\"{html.escape(product.url)}\">Open</a></td>"
            "</tr>"
        )

    html_body = f"""\
<html>
  <body>
    <p>Found {len(new_products)} new Hermes AU bag item(s).</p>
    <p>Checked at: {html.escape(checked_at)}<br>
       Category: <a href="{html.escape(page_url)}">{html.escape(page_url)}</a></p>
    <table border="1" cellpadding="6" cellspacing="0">
      <thead>
        <tr><th>Name</th><th>Color</th><th>Price</th><th>Link</th></tr>
      </thead>
      <tbody>
        {''.join(rows)}
      </tbody>
    </table>
  </body>
</html>
"""
    return "\n".join(plain_lines), html_body


def send_email(subject: str, plain_body: str, html_body: str) -> None:
    smtp_host = os.getenv("SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(os.getenv("SMTP_PORT", "465"))
    smtp_user = os.getenv("SMTP_USER", "").strip()
    smtp_password = re.sub(r"\s+", "", os.getenv("SMTP_PASSWORD", ""))
    sender = os.getenv("SMTP_FROM", smtp_user).strip()
    recipient = os.getenv("EMAIL_TO", DEFAULT_RECIPIENT).strip()

    missing = [
        name
        for name, value in {
            "SMTP_USER": smtp_user,
            "SMTP_PASSWORD": smtp_password,
            "SMTP_FROM or SMTP_USER": sender,
            "EMAIL_TO": recipient,
        }.items()
        if not value
    ]
    if missing:
        raise RuntimeError(
            "Missing email configuration: "
            + ", ".join(missing)
            + ". Fill these in .env first."
        )

    message = EmailMessage()
    message["From"] = sender
    message["To"] = recipient
    message["Subject"] = subject
    message.set_content(plain_body)
    message.add_alternative(html_body, subtype="html")

    context = ssl.create_default_context()
    with smtplib.SMTP_SSL(smtp_host, smtp_port, context=context) as smtp:
        smtp.login(smtp_user, smtp_password)
        smtp.send_message(message)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Monitor Hermes AU women's bags and send email alerts for new products."
    )
    parser.add_argument("--url", default=os.getenv("HERMES_URL", DEFAULT_URL))
    parser.add_argument(
        "--state-file",
        default=os.getenv("STATE_FILE", DEFAULT_STATE_FILE),
        help="JSON file used to remember products already seen.",
    )
    parser.add_argument(
        "--send-initial",
        action="store_true",
        help="Send an email even on the first run, when every current product is new to the script.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would happen without sending email or changing state.",
    )
    parser.add_argument(
        "--debug-html",
        default="",
        help="Save fetched HTML to this file for troubleshooting parser changes.",
    )
    parser.add_argument(
        "--ignore-fetch-errors",
        action="store_true",
        help="Exit successfully when Hermes blocks or times out after retries.",
    )
    parser.add_argument(
        "--test-email",
        action="store_true",
        help="Send a test email using the current .env settings, then exit.",
    )
    parser.add_argument(
        "--test-recipient",
        default="",
        help="Override EMAIL_TO only for --test-email.",
    )
    parser.add_argument(
        "--sample-alert",
        action="store_true",
        help="Send a sample product alert email without changing state.",
    )
    parser.add_argument(
        "--sample-limit",
        type=int,
        default=3,
        help="Number of products to include in --sample-alert. Use 0 for all products.",
    )
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    script_dir = Path(__file__).resolve().parent
    load_dotenv(script_dir / ".env")

    args = parse_args(argv)
    state_path = Path(args.state_file)
    if not state_path.is_absolute():
        state_path = script_dir / state_path

    if args.test_email:
        if args.test_recipient:
            os.environ["EMAIL_TO"] = args.test_recipient
        checked_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        recipient = os.getenv("EMAIL_TO", DEFAULT_RECIPIENT).strip()
        subject = f"Hermes AU test to {recipient} at {checked_at}"
        send_email(
            subject,
            f"Test email sent successfully to {recipient} at {checked_at}.",
            (
                f"<p>Test email sent successfully to "
                f"{html.escape(recipient)} at {html.escape(checked_at)}.</p>"
            ),
        )
        print(f"Test email sent to {recipient}.")
        return 0

    try:
        page_html = fetch_page(args.url)
    except TransientFetchError as exc:
        if args.ignore_fetch_errors:
            print(f"Skipping this run: {exc}")
            return 0
        raise
    if args.debug_html:
        debug_path = Path(args.debug_html)
        if not debug_path.is_absolute():
            debug_path = script_dir / debug_path
        debug_path.write_text(page_html, encoding="utf-8")

    products = parse_products(page_html, args.url)
    if not products:
        raise RuntimeError("No products were parsed. The Hermes page layout may have changed.")

    if args.sample_alert:
        sample_products = products if args.sample_limit == 0 else products[: args.sample_limit]
        plain_body, html_body = build_email_body(sample_products, args.url)
        send_email(
            f"[Sample] Hermes AU new bag alert: {len(sample_products)} item preview",
            plain_body,
            html_body,
        )
        print(f"Sample alert email sent with {len(sample_products)} product(s).")
        return 0

    state = load_state(state_path)
    seen_ids = set(state.get("seen_ids", []))
    first_run = not state_path.exists() or not seen_ids
    if first_run:
        new_products = products if args.send_initial else []
    else:
        new_products = [product for product in products if product.product_id not in seen_ids]

    print(f"Checked {len(products)} product(s). New product(s): {len(new_products)}.")
    for product in new_products:
        print(f"- {product.name} | {product.color or 'N/A'} | {product.price} | {product.url}")

    if args.dry_run:
        print("Dry run: not sending email and not updating state.")
        return 0

    if new_products:
        watch_terms = [
            term.strip()
            for term in os.getenv("WATCH_TERMS", ",".join(DEFAULT_WATCH_TERMS)).split(",")
            if term.strip()
        ]
        watch_hits = matching_watch_terms(new_products, watch_terms)
        subject = build_subject(new_products, watch_hits)
        plain_body, html_body = build_email_body(new_products, args.url)
        send_email(subject, plain_body, html_body)
        print(f"Email sent: {subject}")
    elif first_run:
        print("First run: saved current products as baseline. No email sent.")
    else:
        print("No new products. No email sent.")

    save_state(state_path, products)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        time.sleep(1)
        raise SystemExit(1)
