# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0

"""The 3-step auth probe used internally by the create / update wizards.

Lifecycle history: this code used to live in ``commands/auth.py`` and
back the ``mcs auth test`` CLI verb plus an in-wizard call. The
standalone CLI verb was deleted in the same change that introduced
``mcs profile whoami`` as the canonical "tell me who this profile
resolves to" command — any real verb (e.g. ``mcs sql execute "select
1" --profile <name>``) exercises the same probe path and surfaces
the same error envelopes, so the dedicated verb wasn't earning its
keep. The helper itself is still used by both ``mcs profile create``
(the post-credentials gate that asks "Auth test failed. Save
anyway?") and ``mcs profile update`` (when ``auth`` changed in the
edit), so it keeps a private module home under the ``commands``
package.

The probe runs three steps in sequence and aborts on the first
failure, returning the failing exception's ``McsError.exit_code``:

  1. ``resolve_credentials`` — pulls the literal AK out of the
     ``${env:VAR}`` reference / process-auth subprocess. Catches a
     stuck or missing env var before any network traffic.
  2. ``get_tier`` — the cached "is this 2-level or 3-level?" probe,
     which is also the cheapest possible authenticated metadata
     call.
  3. ``execute_sql("SELECT 1", use_interactive=True)`` — confirms the
     AK has session-level access to the compute project. The interactive
     channel (MCQA on legacy projects, MaxQA on post-2025-11 projects)
     is tried first so the probe stays sub-second on warm runtimes; if
     the project has no interactive quota configured the probe falls
     back to a plain ``run_sql("SELECT 1")`` so we don't false-negative
     a working AK because of a Quota gap.

Returns ``0`` on full success. Callers wanting boolean semantics
must compare with ``0`` explicitly (``test_ok == 0``) because
Python's truthiness inverts the natural reading (``0`` is the
success code but ``bool(0)`` is False).

``emit_summary`` defaults to True and writes the structured
envelope (``r.success({"profile": ..., "step1_resolve_ms": ...,
…})``) at the end. The wizard callers pass ``emit_summary=False``
to suppress the envelope since they print their own
profile-saved confirmation and don't want the auth-test envelope
echoing alongside.
"""

from __future__ import annotations

import time

import click

from maxcompute_semantic._internal.output import Renderer
from maxcompute_semantic.auth.credential import resolve_credentials
from maxcompute_semantic.auth.schema import Profile
from maxcompute_semantic.mc_client.client import MaxComputeClient
from maxcompute_semantic.mc_client.errors import McsError
from maxcompute_semantic.mc_client.tier import get_tier


def _run_auth_test(profile: Profile, r: Renderer, *, emit_summary: bool = True) -> int:
    is_envelope = r.is_envelope

    # Visual cues — green ✓ for success per step, red ✗ for failure,
    # final 🎉 banner for the "all three steps passed" line. Click
    # strips ANSI when stdout isn't a TTY (CliRunner / pipes) so
    # the grep-on-result.output assertions in tests still match.
    ok = click.style("✓", fg="green", bold=True)
    fail = click.style("✗", fg="red", bold=True)
    step = lambda n: click.style(f"[{n}/3]", dim=True)  # noqa: E731

    if not is_envelope:
        click.echo(click.style(f"🔐 Validating profile '{profile.name}'", bold=True))
    results: dict = {"profile": profile.name}

    # Step 1: resolve credentials. Catches missing env vars and
    # subprocess auth-helper failures before any network call.
    t0 = time.monotonic()
    try:
        resolve_credentials(profile.auth)
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        if not is_envelope:
            click.echo(f"{step(1)} resolve credentials {ok} ({elapsed_ms}ms)")
        results["step1_resolve_ms"] = elapsed_ms
    except McsError as e:
        if not is_envelope:
            click.echo(f"{step(1)} resolve credentials                  {fail}  {e.code}", err=True)
            click.echo(f"      {e.message}", err=True)
            if e.remediation:
                click.echo(f"      remediation: {e.remediation}", err=True)
        r.error(e)
        return e.exit_code

    # Step 2: tier probe. Hits the catalog API once and caches the
    # 2-level / 3-level answer in ``<data_dir>/tier_cache/<project>``.
    client = MaxComputeClient(profile)
    t1 = time.monotonic()
    try:
        tier = get_tier(profile, profile.compute_project, client=client)
        client._tier = tier
        elapsed_ms = int((time.monotonic() - t1) * 1000)
        if not is_envelope:
            click.echo(f"{step(2)} probe tier ({tier}-level) {ok} ({elapsed_ms}ms)")
        results["step2_tier"] = tier
        results["step2_probe_ms"] = elapsed_ms
    except McsError as e:
        if not is_envelope:
            click.echo(
                f"{step(2)} probe tier                            {fail}  {e.code}", err=True
            )
            click.echo(f"      {e.message}", err=True)
            if e.remediation:
                click.echo(f"      remediation: {e.remediation}", err=True)
        r.error(e)
        return e.exit_code

    # Step 3: SELECT 1. The cheapest end-to-end "the AK actually has
    # session permission on this project" check. Prefer the interactive
    # channel (MCQA / MaxQA) for sub-second latency on warm runtimes;
    # fall back to plain batch run_sql if the project lacks an
    # interactive quota — otherwise a Quota-config gap would surface
    # as a fake "auth broken" verdict.
    #
    # assume_yes=True: SELECT 1 is a 0-byte connectivity probe; the
    # cost gate would otherwise re-run a cost estimate over a no-op.
    t2 = time.monotonic()
    interactive_err: McsError | None = None
    try:
        client.execute_sql("SELECT 1", use_interactive=True, timeout=30, assume_yes=True)
        elapsed_ms = int((time.monotonic() - t2) * 1000)
        if not is_envelope:
            click.echo(f"{step(3)} execute SELECT 1 (interactive) {ok} ({elapsed_ms}ms)")
        results["step3_select_ms"] = elapsed_ms
        results["step3_mode"] = "interactive"
    except McsError as e:
        interactive_err = e
        try:
            client.execute_sql("SELECT 1", use_interactive=False, timeout=30, assume_yes=True)
            elapsed_ms = int((time.monotonic() - t2) * 1000)
            if not is_envelope:
                click.echo(
                    f"{step(3)} execute SELECT 1 (batch fallback) {ok} ({elapsed_ms}ms) "
                    f"— interactive unavailable: {e.code}"
                )
            results["step3_select_ms"] = elapsed_ms
            results["step3_mode"] = "batch"
            results["step3_interactive_error"] = e.code
        except McsError as batch_err:
            if not is_envelope:
                click.echo(
                    f"{step(3)} execute SELECT 1                      {fail}  {batch_err.code}",
                    err=True,
                )
                click.echo(f"      {batch_err.message}", err=True)
                if batch_err.remediation:
                    click.echo(f"      remediation: {batch_err.remediation}", err=True)
                if interactive_err.code != batch_err.code:
                    click.echo(
                        f"      (interactive channel also failed: {interactive_err.code})",
                        err=True,
                    )
            r.error(batch_err)
            return batch_err.exit_code

    if not is_envelope:
        click.secho(f"\n🎉 profile '{profile.name}' is ready", fg="green", bold=True)
    if emit_summary:
        r.success(results)
    return 0
