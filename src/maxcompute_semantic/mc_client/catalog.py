# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Catalog API search via pyodps RestClient — server-side full-text search.

Uses ``ODPS.catalog_rest`` (RestClient with auth already wired) to call
the Catalog search endpoint directly, **without** depending on
``pyodps_catalog`` SDK.

Endpoint pattern:
    POST {catalog_endpoint}/api/catalog/v1alpha/namespaces/{tenant_id}:search

Auto-routing:
    ``ODPS.catalog_endpoint`` resolves the catalog host via
    ``GET {odps_endpoint}/catalogapi`` → region-based default → cached.

If Catalog API is unavailable (no catalog_endpoint, no tenant_id, or
network error), search methods return None to signal the caller to
fallback to client-side iteration.
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


def catalog_search_tables(
    odps: Any,
    project: str,
    keyword: str,
    *,
    schema: str | None = None,
    page_size: int = 50,
) -> list[dict[str, Any]] | None:
    """Search tables via Catalog API server-side full-text search.

    Args:
        odps: The ODPS instance (must have catalog_rest and project attrs).
        project: MaxCompute project name.
        keyword: Search term — matched against table name (substring).
        schema: Optional schema to scope the search.
        page_size: Results per page (max 100).

    Returns:
        List of dicts with keys: name, schema, comment, owner.
        Returns None if Catalog API is unavailable (caller should fallback).
    """
    try:
        catalog_rest = odps.catalog_rest
    except AttributeError:
        logger.debug("odps instance has no catalog_rest attribute; using client-side fallback")
        catalog_rest = None
    if catalog_rest is None:
        return None

    tenant_id = _resolve_tenant_id(odps, project)
    if tenant_id is None:
        return None

    try:
        base = (catalog_rest.endpoint or "").rstrip("/")
        if not base:
            return None
        url = f"{base}/api/catalog/v1alpha/namespaces/{tenant_id}:search"

        # Build query string per Catalog API spec:
        #   name:{keyword}  — substring match on entity name
        #   type=TABLE      — required filter
        #   project={proj}  — scope to project
        parts = ["type=TABLE"]
        if project:
            parts.append(f"project={project}")
        if keyword:
            parts.append(f"name:{keyword}")
        query = ",".join(parts)

        params = {
            "query": query,
            "pageSize": str(min(page_size, 100)),
            "orderBy": "default",
        }

        # Walk nextPageToken until exhausted. Without this loop a
        # search with > pageSize matches silently truncates at the
        # first page; the agent then sees a partial table list and
        # can't tell the result is incomplete.
        matches: list[dict[str, Any]] = []
        page_count = 0
        while True:
            resp = catalog_rest.request(url, "post", params=params, curr_project=project)
            body = resp.text if hasattr(resp, "text") else resp.content.decode("utf-8")
            data = json.loads(body)

            for entry in data.get("entries") or []:
                if entry is None:
                    continue

                display_name = entry.get("displayName", "")
                full_name = entry.get("name", "")  # projects/X/schemas/Y/tables/Z
                entry_schema = ""
                if full_name:
                    path_parts = full_name.split("/")
                    for i, p in enumerate(path_parts):
                        if p == "schemas" and i + 1 < len(path_parts):
                            entry_schema = path_parts[i + 1]
                            break

                if schema and entry_schema and entry_schema.lower() != schema.lower():
                    continue

                matches.append(
                    {
                        "name": display_name,
                        "schema": entry_schema,
                        "comment": entry.get("description", ""),
                        "owner": "",
                    }
                )

            next_token = data.get("nextPageToken")
            page_count += 1
            if not next_token or page_count >= 100:
                break
            params = {**params, "pageToken": next_token}

        return matches

    except Exception:
        logger.debug("Catalog API search failed, will fallback to client-side", exc_info=True)
        return None


def _resolve_tenant_id(odps: Any, project: str) -> str | None:
    """Resolve tenant_id from the ODPS project object.

    Returns None on any pyodps-side failure (NoSuchObject, auth errors,
    network errors). Callers MUST treat None as "Catalog API unavailable
    for this project; fall back to client-side iteration" — never as a
    hard error. The actual error will resurface in the client-side path
    with proper exception mapping. This silent-swallow is the
    intentional fallback signal documented at the module top.
    """
    try:
        proj = odps.get_project(project)
        tid = proj.tenant_id
        return str(tid) if tid else None
    except Exception:
        logger.debug(
            "tenant_id lookup failed for project %r; using client-side fallback",
            project,
            exc_info=True,
        )
        return None
