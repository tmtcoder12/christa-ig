"""Small PostgREST adapter for embedding ingestion writes."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


class SupabaseStore:
    def __init__(self, supabase_url: str, service_key: str, timeout_seconds: int = 30):
        self.supabase_url = supabase_url.rstrip("/")
        self.service_key = service_key
        self.timeout_seconds = timeout_seconds

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Dict[str, str]] = None,
        payload: Optional[Any] = None,
        prefer: Optional[str] = None,
    ) -> Any:
        query = urllib.parse.urlencode(params or {})
        url = f"{self.supabase_url}/rest/v1/{path}"
        if query:
            url = f"{url}?{query}"

        headers = {
            "apikey": self.service_key,
            "Authorization": f"Bearer {self.service_key}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        if prefer:
            headers["Prefer"] = prefer

        data = None
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")

        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                body = response.read().decode("utf-8")
                if not body:
                    return None
                return json.loads(body)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Supabase {method} {path} failed: {exc.code} {body}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Supabase {method} {path} failed: {exc.reason}") from exc
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Supabase {method} {path} returned invalid JSON: {exc}") from exc

    def upsert_knowledge_chunks(self, rows: List[Dict[str, Any]]) -> None:
        if not rows:
            return

        self._request(
            "POST",
            "knowledge_chunks",
            params={"on_conflict": "id"},
            payload=rows,
            prefer="resolution=merge-duplicates,return=minimal",
        )

    def begin_ingest_run(
        self,
        *,
        instagram_account_id: str,
        model: str,
        source_name: str,
        total_chunks: int,
    ) -> str:
        rows = self._request(
            "POST",
            "ingest_runs",
            payload={
                "instagram_account_id": instagram_account_id,
                "model": model,
                "source_name": source_name,
                "total_chunks": total_chunks,
                "embedded_chunks": 0,
                "status": "running",
            },
            prefer="return=representation",
        )
        if not rows:
            raise RuntimeError("Supabase did not return an ingest run id")
        return rows[0]["id"]

    def finish_ingest_run(
        self,
        run_id: str,
        *,
        status: str,
        embedded_chunks: int,
        error_message: Optional[str] = None,
    ) -> None:
        payload = {
            "status": status,
            "embedded_chunks": embedded_chunks,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        if error_message:
            payload["error_message"] = error_message

        self._request(
            "PATCH",
            "ingest_runs",
            params={"id": f"eq.{run_id}"},
            payload=payload,
            prefer="return=minimal",
        )
