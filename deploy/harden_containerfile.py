#!/usr/bin/env python3
"""Derive a Git-transport-hardened copy of frappe_docker's layered Containerfile.

Upstream's `frappe/build:version-15` builder ships Git 2.39.5, whose default
HTTP/2 transport fails public GitHub ref discovery during `bench init`:

    fatal: could not read Username for 'https://github.com'
    fatal: expected flush after ref listing

The same `git ls-remote https://github.com/frappe/frappe version-15` succeeds
when Git is forced to HTTP/1.1 (`git -c http.version=HTTP/1.1 ...`). See
TXB-214.

We do NOT edit the upstream `frappe_docker` checkout. Instead we copy its
layered Containerfile and inject exactly one

    RUN git config --global http.version HTTP/1.1

as the `frappe` builder user, immediately after the builder-stage `USER frappe`
anchor and before the `bench init` RUN. The transformation asserts that exactly
one intended builder-stage anchor exists, so an upstream layout change fails the
build loudly instead of silently producing an unhardened (still-broken) image.

Usage:
    harden_containerfile.py <input Containerfile> <output path>
"""
import re
import sys

# The builder stage runs `bench init` from the RUN that mounts the apps_json
# BuildKit secret, immediately after it drops to `USER frappe`. That
# `USER frappe` + apps_json-secret RUN pairing is unique to the builder stage
# (the backend stage's `USER frappe` lines are not followed by this mount), so
# it is an unambiguous anchor for the one place the hardening belongs.
ANCHOR_RE = re.compile(
    r"^(USER frappe[ \t]*\n)(\n*)(RUN --mount=type=secret,id=apps_json)",
    re.MULTILINE,
)

INJECTED_RUN = (
    "# TXB-214: frappe/build's Git 2.39.5 fails public GitHub ref discovery over\n"
    "# HTTP/2 during `bench init`; force HTTP/1.1 for the frappe builder user.\n"
    "RUN git config --global http.version HTTP/1.1\n\n"
)


def harden(text: str) -> str:
    """Return `text` with one HTTP/1.1 git-config RUN injected before bench init.

    Raises SystemExit if the builder-stage anchor is missing or ambiguous, or if
    the upstream file already pins http.version (avoid double-injection).
    """
    matches = ANCHOR_RE.findall(text)
    if len(matches) != 1:
        raise SystemExit(
            "harden_containerfile: expected exactly one builder-stage "
            "`USER frappe` + apps_json `bench init` anchor, found "
            f"{len(matches)}. Upstream Containerfile layout changed; refusing to "
            "build a potentially unhardened image."
        )
    if "http.version" in text:
        raise SystemExit(
            "harden_containerfile: upstream Containerfile already configures "
            "git http.version; refusing to double-inject."
        )

    def _repl(match: "re.Match[str]") -> str:
        return match.group(1) + match.group(2) + INJECTED_RUN + match.group(3)

    return ANCHOR_RE.sub(_repl, text, count=1)


def main(argv: "list[str]") -> None:
    if len(argv) != 3:
        raise SystemExit("usage: harden_containerfile.py <input> <output>")
    with open(argv[1], encoding="utf-8") as fh:
        text = fh.read()
    hardened = harden(text)
    with open(argv[2], "w", encoding="utf-8") as fh:
        fh.write(hardened)


if __name__ == "__main__":
    main(sys.argv)
