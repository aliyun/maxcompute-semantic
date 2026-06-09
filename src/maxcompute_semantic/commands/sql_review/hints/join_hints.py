# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""Hints about JOINs that are not declared in the package.

Two generators live here:

- ``hint_join_not_declared`` — fires once per (left, right) pair seen in
  explicit JOINs whose pairing has no matching row in the package's
  ``joins`` table. Emitted at ``info`` severity (a hint, not an issue):
  the SQL may be perfectly valid; we're just flagging that the package
  has no prior knowledge of the relationship, so the agent has the
  option to declare it via the proposal workflow if the JOIN is canonical.
- ``hint_join_bridge_suggested`` — fires for the same undeclared pair
  *only* when a transitive path through declared joins exists within
  ``max_hops=3``. The ``evidence["bridge_path"]`` carries the arrow-
  joined intermediate table list so the agent can decide whether the
  direct JOIN is correct or whether routing through the bridge is the
  intended semantics.

Both generators chain JOINs in source order — an ``A JOIN B JOIN C``
SQL produces ``[(A,B), (B,C)]`` rather than ``[(A,B), (A,C)]``. That
matches "what physically joins to what" semantics; pairing every JOIN
target against the FROM-clause table only would over-attribute.

Endpoints are identity-keyed on ``(source_key, table_name)`` rather
than bare table names. In a multi-source profile where the same bare
name exists in more than one source (e.g. ``proj_a.default.orders``
and ``proj_b.default.orders``), a declared join on one source must
not silently suppress the undeclared-join hint on the other; the
bridge graph similarly avoids "phantom" cross-source paths through
look-alike table names.
"""

from __future__ import annotations

from collections import defaultdict, deque

from sqlglot import exp

from maxcompute_semantic.commands.sql_review.rules._common import (
    alias_to_table_in_select,
    cte_names,
    parse_statements,
    resolve_source_for_table,
)
from maxcompute_semantic.commands.sql_review.types import Hint, ReviewContext

# An endpoint identifies a participant in a join: the source_key
# disambiguates same-bare-name tables across the profile's sources,
# and the table_name is preserved (rather than collapsed to source_key
# alone) so messages and evidence stay readable.
Endpoint = tuple[str, str]  # (source_key, table_name)


def _resolve_endpoint(ctx: ReviewContext, table: exp.Table, ctes: set[str]) -> Endpoint | None:
    """Map an ``exp.Table`` node to its ``(source_key, table_name)`` endpoint.

    Returns ``None`` for CTE refs (no row in the joins table by
    construction) and for tables that no source contains (the
    ``schema.table-not-found`` rule flags those at error severity, so a
    hint here would be noise).
    """
    if not table.catalog and table.name.lower() in ctes:
        return None
    sk = resolve_source_for_table(ctx, table, table.name)
    if sk is None:
        return None
    return (sk, table.name)


def _sql_join_pairs(ctx: ReviewContext) -> list[tuple[Endpoint, Endpoint]]:
    """Return resolved ``(left, right)`` endpoint pairs from explicit JOINs.

    Iterates ``stmt.find_all(exp.Select)`` and processes each Select's
    own ``args["from_"]`` + ``args["joins"]`` independently. The earlier
    form used a top-level ``stmt.find(exp.From)`` to seed ``left`` and
    then walked ``stmt.find_all(exp.Join)`` across every nested
    subquery, manufacturing cross-scope pairs like ``orders <-> users``
    from ``SELECT * FROM orders WHERE EXISTS (SELECT 1 FROM events
    JOIN users ON events.user_id = users.id)``. sqlglot 30.x stores
    the immediate FROM clause under the ``from_`` arg key and JOINs
    under ``joins``; both accessors are scoped to one Select node and
    do not recurse — which is exactly the scoping needed here.

    Pairing strategy (per Join *j* within a Select):

    - **ON-aware** — when ``j.args["on"]`` contains column references
      that the Select's alias map can resolve to FROM/JOIN tables
      other than ``j.this``, emit one pair per distinct other table.
      This handles the chain shape that the source-order pairing logic
      mis-attributes: ``orders JOIN users ON ... JOIN payments ON
      orders.id = payments.order_id`` is the ``orders <-> payments``
      pair, not ``users <-> payments``. Round 6 Codex review flagged
      this as P2 because a wrong pair fires a false
      ``join.not-declared`` hint even when ``orders <-> payments`` is
      declared in the package.
    - **Chain fallback** — when the ON clause is absent (CROSS JOIN,
      NATURAL JOIN, USING clause that sqlglot stores under ``using``
      not ``on``) or no column maps to a different table, fall back
      to "pair with the immediate predecessor in source order". This
      preserves the v1 chain shape ``A JOIN B JOIN C => [(A,B),
      (B,C)]`` for the cases where ON-inspection can't disambiguate.

    Each endpoint is ``(source_key, table_name)``. Pairs where either
    side fails ``_resolve_endpoint`` (CTE refs, missing tables) are
    dropped — same scope ``rules/schema.py`` and ``rules/tier.py``
    apply via ``cte_names``.
    """
    pairs: list[tuple[Endpoint, Endpoint]] = []
    for stmt in parse_statements(ctx.sql):
        ctes = cte_names(stmt)
        for select in stmt.find_all(exp.Select):
            from_clause = select.args.get("from_")
            if from_clause is None or not isinstance(from_clause.this, exp.Table):
                continue
            alias_map = alias_to_table_in_select(select)
            left_node: exp.Table = from_clause.this
            for j in select.args.get("joins") or []:
                right_node = j.this
                if not isinstance(right_node, exp.Table):
                    continue
                on_partners = _on_clause_partners(j, alias_map, right_node)
                if on_partners:
                    right_endpoint = _resolve_endpoint(ctx, right_node, ctes)
                    if right_endpoint is not None:
                        for partner in on_partners:
                            left_endpoint = _resolve_endpoint(ctx, partner, ctes)
                            if left_endpoint is not None:
                                pairs.append((left_endpoint, right_endpoint))
                else:
                    # ON clause missing, malformed, or only references the
                    # joined-in table — fall back to source-order chain so
                    # ``A JOIN B JOIN C`` still emits ``[(A,B), (B,C)]``.
                    left_endpoint = _resolve_endpoint(ctx, left_node, ctes)
                    right_endpoint = _resolve_endpoint(ctx, right_node, ctes)
                    if left_endpoint is not None and right_endpoint is not None:
                        pairs.append((left_endpoint, right_endpoint))
                left_node = right_node
    return pairs


def _on_clause_partners(
    join: exp.Join,
    alias_map: dict[str, exp.Table],
    joined_in: exp.Table,
) -> list[exp.Table]:
    """Tables referenced by *join*'s ON condition, other than *joined_in*.

    Walks every ``exp.Column`` inside ``join.args["on"]`` and resolves
    each qualifier through *alias_map* (the per-Select alias →
    ``exp.Table`` map). Returns the distinct tables (preserving the
    order in which they appear in the ON clause), excluding the table
    being joined in — those are the real "other side" of this join.

    Returns an empty list when the ON clause is missing (e.g. CROSS
    JOIN, NATURAL JOIN, ``USING (...)`` which sqlglot stores under
    a different arg key), or when no column resolves to a known
    FROM/JOIN table other than *joined_in*. Callers fall back to
    source-order chain pairing in that case.
    """
    on = join.args.get("on")
    if on is None:
        return []
    joined_key = (joined_in.alias or joined_in.name).lower()
    seen_keys: set[str] = set()
    partners: list[exp.Table] = []
    for col in on.find_all(exp.Column):
        if not col.table:
            continue
        key = col.table.lower()
        if key == joined_key or key in seen_keys:
            continue
        partner = alias_map.get(key)
        if partner is None:
            continue
        seen_keys.add(key)
        partners.append(partner)
    return partners


def _declared_pair(ctx: ReviewContext, left: Endpoint, right: Endpoint) -> bool:
    """True iff the package's ``joins`` table contains a row matching
    the unordered ``{left, right}`` endpoint pair.

    Source-key identity-keyed comparison: a join declared between
    ``proj_a.default.orders`` and ``proj_a.default.users`` does not
    satisfy a SQL pair joining ``proj_b.default.orders`` and
    ``proj_b.default.users`` — pre-fix the bare-name comparison
    collapsed both to ``{"orders", "users"}`` and silently suppressed
    the cross-source hint.
    """
    if ctx.db is None:
        return False
    target = {left, right}
    return any(
        {
            (j["left_source_key"], j["left_table"]),
            (j["right_source_key"], j["right_table"]),
        }
        == target
        for j in ctx.db.list_joins()
    )


def _format_endpoint(endpoint: Endpoint) -> str:
    """Render an endpoint as ``source_key.table`` for messages.

    The qualification is redundant for single-source profiles but
    harmless; for multi-source profiles it's load-bearing so the agent
    can tell which source's ``orders`` the hint refers to.
    """
    sk, name = endpoint
    return f"{sk}.{name}"


def hint_join_not_declared(ctx: ReviewContext) -> list[Hint]:
    hints: list[Hint] = []
    seen: set[frozenset[Endpoint]] = set()
    for left, right in _sql_join_pairs(ctx):
        pair = frozenset({left, right})
        if pair in seen:
            continue
        seen.add(pair)
        if _declared_pair(ctx, left, right):
            continue
        left_label = _format_endpoint(left)
        right_label = _format_endpoint(right)
        hints.append(
            Hint(
                kind="join.not-declared",
                message=(
                    f"JOIN between tables `{left_label}` and `{right_label}` — no join "
                    f"declared in the package's joins table"
                ),
                confidence="medium",
                evidence={
                    "left_source_key": left[0],
                    "left_table": left[1],
                    "right_source_key": right[0],
                    "right_table": right[1],
                },
                if_misleading=(
                    f"declare the relationship after the query: run "
                    f"`mcs build --refresh` to rediscover joins between "
                    f"`{left[1]}` and `{right[1]}`, or propose the "
                    f"relationship via `mcs package propose --from-stdin`"
                ),
            )
        )
    return hints


def _build_join_graph(
    ctx: ReviewContext,
) -> dict[Endpoint, set[tuple[Endpoint, str, str]]]:
    """Return endpoint -> set of ``(other_endpoint, left_col, right_col)`` edges.

    The graph is undirected at the endpoint level — every declared join
    contributes two entries so BFS can traverse from either side.
    Identity-keyed on ``(source_key, table_name)`` so a path like
    ``proj_a.orders -> proj_a.customers -> proj_a.users`` doesn't
    spuriously bridge ``proj_b.orders`` to ``proj_b.users``: the
    declared edges live entirely in the ``proj_a`` source, and the
    ``proj_b`` nodes are simply absent from the graph.
    """
    graph: dict[Endpoint, set[tuple[Endpoint, str, str]]] = defaultdict(set)
    if ctx.db is None:
        return graph
    for j in ctx.db.list_joins():
        left: Endpoint = (j["left_source_key"], j["left_table"])
        right: Endpoint = (j["right_source_key"], j["right_table"])
        graph[left].add((right, j["left_col"], j["right_col"]))
        graph[right].add((left, j["right_col"], j["left_col"]))
    return graph


def _bridge_path(
    graph: dict[Endpoint, set[tuple[Endpoint, str, str]]],
    src: Endpoint,
    dst: Endpoint,
    max_hops: int = 3,
) -> list[Endpoint] | None:
    """BFS for the shortest path src->dst through declared joins.

    Returns the list of endpoints including endpoints, or ``None`` when
    no path exists within ``max_hops`` hops.
    """
    queue: deque[tuple[Endpoint, list[Endpoint]]] = deque([(src, [src])])
    visited: set[Endpoint] = {src}
    while queue:
        node, path = queue.popleft()
        # Cap at ``max_hops`` extensions: ``len(path)`` counts nodes,
        # so an extension would yield ``len(path)+1`` nodes =
        # ``len(path)`` hops. Skipping when ``len(path) > max_hops``
        # keeps the longest returned path at ``max_hops + 1`` nodes
        # (= ``max_hops`` hops). The earlier ``> max_hops + 1`` check
        # let 4-hop paths slip through despite the docstring's cap.
        if len(path) > max_hops:
            continue
        for neighbor, _, _ in graph.get(node, set()):
            if neighbor == dst:
                return path + [neighbor]
            if neighbor in visited:
                continue
            visited.add(neighbor)
            queue.append((neighbor, path + [neighbor]))
    return None


def hint_join_bridge_suggested(ctx: ReviewContext) -> list[Hint]:
    hints: list[Hint] = []
    graph = _build_join_graph(ctx)
    seen: set[frozenset[Endpoint]] = set()
    for left, right in _sql_join_pairs(ctx):
        pair = frozenset({left, right})
        if pair in seen:
            continue
        seen.add(pair)
        if _declared_pair(ctx, left, right):
            continue
        path = _bridge_path(graph, left, right)
        if path is None or len(path) < 3:
            # Need >=1 intermediate hop for a "bridge" suggestion; a
            # direct path (len==2) is just the undeclared pair itself.
            continue
        path_labels = [_format_endpoint(p) for p in path]
        left_label = _format_endpoint(left)
        right_label = _format_endpoint(right)
        hints.append(
            Hint(
                kind="join.bridge-suggested",
                message=(
                    f"JOIN `{left_label}` <-> `{right_label}` is undeclared, but a "
                    f"{len(path) - 1}-hop path exists: {' -> '.join(path_labels)}"
                ),
                confidence="medium",
                evidence={
                    "bridge_path": " -> ".join(path_labels),
                    "hops": len(path) - 1,
                },
                if_misleading=(
                    f"if the direct JOIN `{left_label}` <-> `{right_label}` is "
                    f"correct, declare it after the query via "
                    f"`mcs build --refresh` or `mcs package propose --from-stdin`; "
                    f"otherwise rewrite the SQL to traverse the bridge path"
                ),
            )
        )
    return hints
