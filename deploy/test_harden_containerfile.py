#!/usr/bin/env python3
"""Focused probe test for harden_containerfile — no docker, no network.

Run with: python3 deploy/test_harden_containerfile.py
Proves the transform is deterministic (exactly one injection, in the right
place) and fails loudly when the builder anchor is missing or ambiguous.
"""
import importlib.util
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location(
    "harden_containerfile", os.path.join(_HERE, "harden_containerfile.py")
)
hc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hc)

# Mirrors the shape of frappe_docker images/layered/Containerfile: a builder
# stage that drops to `USER frappe` then runs the apps_json-secret bench init,
# plus backend-stage `USER frappe` lines that must stay untouched.
UPSTREAM = """\
FROM frappe/build:version-15 AS builder
USER frappe

RUN --mount=type=secret,id=apps_json,target=/opt/frappe/apps.json,uid=1000,gid=1000 \\
  bench init --frappe-branch=version-15 /home/frappe/frappe-bench

FROM frappe/base:version-15 AS backend
USER frappe
COPY --from=builder /home/frappe/frappe-bench /home/frappe/frappe-bench
USER frappe
"""

GITCFG = "RUN git config --global http.version HTTP/1.1"


def test_single_injection_before_bench_init():
    out = hc.harden(UPSTREAM)
    assert out.count(GITCFG) == 1, "expected exactly one git-config RUN"
    assert out.index("http.version HTTP/1.1") < out.index("bench init"), (
        "hardening must precede bench init"
    )
    # backend-stage USER frappe lines are left alone
    assert out.count("USER frappe") == UPSTREAM.count("USER frappe")
    # the apps_json secret RUN is preserved verbatim
    assert "RUN --mount=type=secret,id=apps_json" in out


def test_missing_anchor_fails_loudly():
    try:
        hc.harden("FROM scratch\nUSER frappe\nRUN echo hi\n")
    except SystemExit:
        return
    raise AssertionError("expected SystemExit when the builder anchor is absent")


def test_ambiguous_anchor_fails_loudly():
    doubled = UPSTREAM + "\nUSER frappe\n\nRUN --mount=type=secret,id=apps_json x\n"
    try:
        hc.harden(doubled)
    except SystemExit:
        return
    raise AssertionError("expected SystemExit when the anchor is ambiguous")


def test_already_hardened_fails_loudly():
    try:
        hc.harden(hc.harden(UPSTREAM))
    except SystemExit:
        return
    raise AssertionError("expected SystemExit when http.version is already set")


if __name__ == "__main__":
    test_single_injection_before_bench_init()
    test_missing_anchor_fails_loudly()
    test_ambiguous_anchor_fails_loudly()
    test_already_hardened_fails_loudly()
    print("ok: harden_containerfile probe tests passed")
