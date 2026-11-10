#!/usr/bin/env python3
"""Names the busiest functions of a Dreamcast debug log's sampling profile.

usage: prof_report.py LOG ELF [--window N] [--match TEXT] [--top N]

A debug build logs, every 300 frames, a "gpu:" line and the profiler's
"prof:" lines: the busiest 128-byte buckets of code and their sample
counts, and "prof callers:" for samples inside memcpy, memset and memmove.
This adds the buckets up by function (sh-elf-addr2line on the ELF of the
same build, with symbols: symbols.elf, or the unstripped parasite-eve.elf)
and prints, for each window, its gpu line and the top functions.

--window N   only the Nth window (from 1), --match TEXT only windows whose
             gpu line contains TEXT (e.g. "5405 verts").
The buckets listed are only the busiest 64, so the shares are of those.
"""
import argparse
import collections
import os
import re
import subprocess
import sys


def addr2line(elf, addrs):
    tool = "sh-elf-addr2line"
    kos = os.environ.get("KOS_CC_BASE")
    if kos:
        tool = os.path.join(kos, "bin", tool)
    out = subprocess.run([tool, "-f", "-e", elf] + [f"0x{a:x}" for a in addrs],
                         capture_output=True, text=True, check=True).stdout
    lines = out.splitlines()
    return {a: lines[2 * i] for i, a in enumerate(addrs)}


def windows(log):
    """[(gpu line, {addr: n}, {addr: n} callers, total samples)]"""
    out = []
    cur = None
    for line in open(log, errors="replace"):
        if "gpu: 300 frames" in line:
            cur = [line.split("gpu: ", 1)[1].strip(), collections.Counter(),
                   collections.Counter(), 0]
            out.append(cur)
            continue
        if cur is None:
            continue
        m = re.search(r"prof: (\d+) samples", line)
        if m:
            cur[3] = int(m.group(1))
            continue
        m = re.search(r"prof( callers)?:((?: [0-9a-f]{8}:\d+)+)", line)
        if m:
            table = cur[2] if m.group(1) else cur[1]
            for a, n in re.findall(r"([0-9a-f]{8}):(\d+)", m.group(2)):
                table[int(a, 16)] += int(n)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("log")
    ap.add_argument("elf")
    ap.add_argument("--window", type=int)
    ap.add_argument("--match")
    ap.add_argument("--top", type=int, default=15)
    args = ap.parse_args()
    wins = windows(args.log)
    if not wins:
        sys.exit("no gpu/prof lines in the log (a release build has none)")
    pick = [(i + 1, w) for i, w in enumerate(wins)
            if (args.window is None or args.window == i + 1)
            and (args.match is None or args.match in w[0])]
    addrs = sorted({a for _, w in pick for t in (w[1], w[2]) for a in t})
    names = addr2line(args.elf, addrs) if addrs else {}
    for n, (gpu, buckets, callers, samples) in pick:
        print(f"[{n}] {gpu[:120]}")
        funcs = collections.Counter()
        for a, c in buckets.items():
            funcs[names[a]] += c
        listed = sum(funcs.values())
        if not listed:
            print("    (no profile)")
            continue
        print(f"    {samples} samples, {listed} in the listed buckets")
        for f, c in funcs.most_common(args.top):
            print(f"    {100 * c / listed:5.1f}%  {f}")
        calls = collections.Counter()
        for a, c in callers.items():
            calls[names[a]] += c
        if calls:
            print("    memcpy/memset/memmove called from: " + ", ".join(
                f"{f} {c}" for f, c in calls.most_common(5)))
        print()


if __name__ == "__main__":
    main()
