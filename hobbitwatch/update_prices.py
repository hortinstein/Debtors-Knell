#!/usr/bin/env python3
"""
Pull the latest daily price archive from main into this feature branch, and
rebuild the Hobbit Watch dataset from it.

main gets a new "Archive card prices for <date>" commit every day
(.github/workflows/fetch-prices.yml); this branch doesn't see those until
something merges them in. Doing that by hand is a `git fetch` + `git merge`
that reliably hits one binary conflict: prices/mtgjson/uuid_to_name.json.gz
gets rebuilt by main's own (much rarer) monthly refresh using whatever
version of scripts/build_mtgjson_uuid_map.py is on main at the time, and this
branch has carried a newer version of that script since 8e57faf ("Price each
printing exactly, and match double-faced card names") -- one that adds a
`printings` section the older script doesn't write. A plain merge can silently
replace the richer map with a `printings`-less one from main, which would
quietly fall the physical-price join back from per-printing to per-name
medians. This script resolves that one conflict by keeping whichever side's
map has `printings`, so nothing needs babysitting.

Any *other* conflict is a surprise worth a human's attention, not a guess --
the script aborts the merge and leaves the tree exactly as it was.

Run from anywhere in the repo:
    python3 hobbitwatch/update_prices.py
"""
import gzip
import json
import os
import subprocess
import sys

MAP_PATH = "prices/mtgjson/uuid_to_name.json.gz"
HERE = os.path.dirname(os.path.abspath(__file__))


def log(msg):
    print(msg, flush=True)


def run(args, **kw):
    return subprocess.run(args, check=True, text=True, capture_output=True, **kw)


def repo_root():
    return run(["git", "rev-parse", "--show-toplevel"]).stdout.strip()


def current_branch():
    return run(["git", "symbolic-ref", "--short", "HEAD"]).stdout.strip()


def is_clean():
    return run(["git", "status", "--porcelain"]).stdout.strip() == ""


def conflicted_files():
    out = run(["git", "diff", "--name-only", "--diff-filter=U"]).stdout
    return [line for line in out.splitlines() if line]


def show(ref):
    """git show for a path that may not exist at that ref (e.g. one side
    added it) -- returns b"" rather than raising."""
    r = subprocess.run(["git", "show", ref], capture_output=True)
    return r.stdout if r.returncode == 0 else b""


def score_uuid_map(blob):
    """Higher is better: a map with a `printings` section beats one without,
    then the newer `generated` date wins (ISO dates sort lexicographically).
    An unreadable/missing blob scores lowest so it never wins by default."""
    if not blob:
        return (-1, "")
    try:
        payload = json.loads(gzip.decompress(blob))
    except Exception:
        return (-1, "")
    return (1 if payload.get("printings") else 0, payload.get("generated") or "")


def resolve_uuid_map_conflict():
    ours = show(f":2:{MAP_PATH}")
    theirs = show(f":3:{MAP_PATH}")
    ours_score, theirs_score = score_uuid_map(ours), score_uuid_map(theirs)
    side = "ours" if ours_score >= theirs_score else "theirs"
    log(f"  {MAP_PATH}: keeping {side}'s version "
        f"(ours printings={bool(ours_score[0] == 1)} generated={ours_score[1] or '?'}, "
        f"theirs printings={bool(theirs_score[0] == 1)} generated={theirs_score[1] or '?'}).")
    run(["git", "checkout", f"--{side}", MAP_PATH])
    run(["git", "add", MAP_PATH])


def main():
    root = repo_root()
    os.chdir(root)

    branch = current_branch()
    if branch == "main":
        log("On main -- nothing to pull into. Switch to a feature branch first.")
        return 1
    if not is_clean():
        log("Working tree isn't clean -- commit or stash your changes first:")
        log(run(["git", "status", "--short"]).stdout)
        return 1

    log("Fetching origin/main...")
    run(["git", "fetch", "origin", "main"])

    behind = run(["git", "rev-list", "--count", f"{branch}..origin/main"]).stdout.strip()
    if behind == "0":
        log("Already up to date with origin/main.")
    else:
        log(f"{branch} is {behind} commit(s) behind origin/main -- merging...")
        merge = subprocess.run(
            ["git", "merge", "origin/main", "-m", "Merge latest daily price archive from main"],
            text=True, capture_output=True,
        )
        if merge.returncode != 0:
            conflicts = conflicted_files()
            other = [f for f in conflicts if f != MAP_PATH]
            if other:
                log("Unexpected merge conflict(s) outside the usual uuid map -- "
                    "aborting so you can resolve by hand:")
                for f in other:
                    log(f"  {f}")
                run(["git", "merge", "--abort"])
                return 1
            if MAP_PATH in conflicts:
                log("Resolving the usual uuid-map conflict...")
                resolve_uuid_map_conflict()
            run(["git", "commit", "--no-edit"])
        log("Merged.")

    log("Rebuilding the Hobbit Watch dataset...")
    subprocess.run([sys.executable, "build_dataset.py"], cwd=HERE, check=True)

    latest = run(["git", "log", "-1", "--format=%s", "--grep=^Archive card prices",
                   "origin/main"]).stdout.strip()
    log(f"Done. Latest on origin/main: {latest or '(none found)'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
