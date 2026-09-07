"""Backfill Instagram media metadata into ig_posts for one Instagram account."""

from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from christa_ig.http_client import perform_request
from supabase_client import get_instagram_account_by_id, upsert_instagram_post

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - dependency is optional for deployed/script envs.

    def load_dotenv() -> bool:
        return False


INSTAGRAM_API_VERSION = "v24.0"
MEDIA_FIELDS = "id,caption,media_type,media_url,permalink,timestamp"


def require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def parse_meta_media_timestamp(value: Optional[str]) -> Optional[str]:
    if not value:
        return None

    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.isoformat()


def caption_preview(caption: Optional[str], max_length: int = 72) -> str:
    text = " ".join((caption or "").split())
    if len(text) <= max_length:
        return text
    return f"{text[: max_length - 3]}..."


def fetch_json(url: str, access_token: str) -> Dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
        },
        method="GET",
    )

    try:
        response = perform_request(request, timeout=30, retry_safe=True)
        return json.loads(response.body.decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Instagram media fetch failed: {exc.code} {body}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise RuntimeError(f"Instagram media fetch failed: {getattr(exc, 'reason', exc)}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Instagram media fetch returned invalid JSON: {exc}") from exc


def iter_instagram_media(
    instagram_user_id: str, access_token: str, limit: Optional[int] = None
) -> Iterable[Dict[str, Any]]:
    query = urllib.parse.urlencode(
        {
            "fields": MEDIA_FIELDS,
            "limit": "100",
        }
    )
    next_url: Optional[str] = f"https://graph.instagram.com/{INSTAGRAM_API_VERSION}/{instagram_user_id}/media?{query}"
    yielded = 0

    while next_url:
        payload = fetch_json(next_url, access_token)
        media_items = payload.get("data")
        if not isinstance(media_items, list):
            media_items = []

        for item in media_items:
            if not isinstance(item, dict) or not item.get("id"):
                continue
            yield item
            yielded += 1
            if limit is not None and yielded >= limit:
                return

        paging = payload.get("paging")
        next_url = paging.get("next") if isinstance(paging, dict) else None


def build_post_row(instagram_account_id: str, media_item: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "instagram_account_id": instagram_account_id,
        "instagram_media_id": media_item["id"],
        "caption": media_item.get("caption"),
        "media_type": media_item.get("media_type"),
        "media_url": media_item.get("media_url"),
        "permalink": media_item.get("permalink"),
        "posted_at": parse_meta_media_timestamp(media_item.get("timestamp")),
        "post_type": "regular",
        "automation_enabled": False,
        "extra_metadata": {
            "source": "instagram_media_backfill",
            "meta_media": media_item,
        },
    }


def backfill_instagram_media(instagram_account_id: str, limit: Optional[int] = None) -> List[Dict[str, Any]]:
    access_token = require_env("INSTAGRAM_ACCESS_TOKEN")
    account = get_instagram_account_by_id(instagram_account_id)
    if not account:
        raise RuntimeError(f"Instagram account not found: {instagram_account_id}")

    print(
        "Backfilling media for "
        f"{account.get('username') or account.get('instagram_user_id')} "
        f"(internal id: {account['id']})"
    )

    upserted: List[Dict[str, Any]] = []
    for media_item in iter_instagram_media(account["instagram_user_id"], access_token, limit=limit):
        row = build_post_row(account["id"], media_item)
        post = upsert_instagram_post(row)
        upserted.append(post)
        print(
            f"- {media_item['id']} "
            f"{row.get('posted_at') or 'unknown-time'} "
            f"{caption_preview(media_item.get('caption'))}"
        )

    print(f"Fetched/upserted {len(upserted)} media item(s).")
    return upserted


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill Instagram media into ig_posts.")
    parser.add_argument(
        "instagram_account_id",
        help="Internal public.instagram_accounts.id UUID for the account to backfill.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Optional maximum number of media items to fetch/upsert.",
    )
    return parser.parse_args()


def main() -> None:
    load_dotenv()
    args = parse_args()
    if args.limit is not None and args.limit <= 0:
        raise SystemExit("--limit must be greater than 0")

    require_env("SUPABASE_URL")
    require_env("SUPABASE_SERVICE_ROLE_KEY")
    backfill_instagram_media(args.instagram_account_id, limit=args.limit)


if __name__ == "__main__":
    main()
