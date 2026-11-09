#!/usr/bin/env python3
"""Instruction counts of the Dreamcast build, scene by scene, layer by layer.

usage: bench.py HARNESS_DIR OUT_DIR [--ref REF_DIR] [--only NAME,...]

Runs the harness (built by build.py into HARNESS_DIR) under callgrind along
fixed input schedules from a new game, counting the instructions of a few
windows of 300 vblanks: a movie, menus, the street, the opera house. Each
window's count is split by layer (renderer, GTE, GPU queue, game, ...) from
the source file of every function; the harness's own stand-ins (software
PVR, audio) are left out. The frame at the end of each window is saved, and
with --ref compared to the same frame of an earlier run, so a change that
should not alter the picture can be checked on every scene at once.

Instruction counts are x86's, not the SH-4's: compare runs with each other,
not with the Dreamcast. Needs valgrind and the disc images the harness uses.
"""
import argparse
import collections
import glob
import os
import re
import subprocess
import sys

FIELD = (
    "10000:8 10010:0 11000:16 11010:0 11500:16384 11510:0 "
    + " ".join(f"{v}:16384 {v + 10}:0" for v in range(17000, 21801, 600))
    + " 23000:8 23010:0 23600:8 23610:0 24200:16384 24210:0 24800:16384"
    " 24810:0 26000:16 26900:0 "
    + " ".join(f"{v}:16384 {v + 10}:0 {v + 20}:16 {v + 60}:0"
               for v in range(27500, 35901, 600))
    + " 36000:16 38000:0 "
    + " ".join(f"{v}:16384 {v + 10}:0" for v in range(38100, 49901, 300))
)

# run name: (schedule, last vblank, [(scene, from, to)])
RUNS = {
    "newgame": (FIELD + " 50500:16 54000:0", 52200, [
        ("movie", 12000, 12300),
        ("name_entry", 17700, 18000),
        ("street_talk", 25000, 25300),
        ("street_walk", 33000, 33300),
        ("lobby", 36600, 36900),
        ("stage_talk", 38300, 38600),
        ("stage_play", 41000, 41300),
        ("auditorium", 50000, 50300),
        ("auditorium_walk", 51800, 52100),
    ]),
    "corridor": (FIELD + " 50500:64 54000:0", 53400, [
        ("lobby_bodies", 53000, 53300),
    ]),
    "continue": ("10000:8 10010:0 11000:16384 11010:0 12200:16384 12210:0",
                 13100, [
        ("memcard_dialog", 12700, 13000),
    ]),
}

LAYERS = ["renderer", "GTE", "GPU queue", "game", "libc", "psyz other",
          "movies", "CD/XA", "sound"]


HARNESS = re.compile(
    r"(^|/)(fakepvr|host_\w+|harness_main)\.c:"  # its stand-ins
    r"|libcd\.c:hermite4"  # XA resampled to 44.1 kHz: the Dreamcast does not
    r"|\?\?\?:(0x0003b330|feraiseexcept)"  # libm's fmaf, for hermite4
    r"|:Psyz_VideoVSync$")  # waiting for the vblank


def layer(fn):
    if HARNESS.search(fn):
        return None  # the harness's own work
    if "dreamcast/dc_gpu.c" in fn:
        return "renderer"
    if "libgte.c" in fn or "gte_state.h" in fn:
        return "GTE"
    if re.search(r"psyz/(libgpu|prim|sys)\.c", fn):
        return "GPU queue"
    if "libpress.c" in fn or "vlc" in fn:
        return "movies"
    if "libcd.c" in fn:
        return "CD/XA"
    if re.search(r"dc_spu|dc_aica|libspu|libsnd|spu_voice", fn):
        return "sound"
    if re.search(r"(^|/)src/(main|overlays)/", fn):
        return "game"
    if "/psyz/" in fn:
        return "psyz other"
    return "libc"


def read_dump(path):
    out = subprocess.run(
        ["callgrind_annotate", "--auto=no", "--threshold=100", path],
        capture_output=True, text=True, check=True).stdout
    totals = collections.Counter()
    funcs = collections.Counter()
    for line in out.splitlines():
        m = re.match(r"\s*([\d,]+) \(\s*[\d.]+%\)\s+(\S+)", line)
        if not m or "PROGRAM TOTALS" in line:
            continue
        n = int(m.group(1).replace(",", ""))
        fn = m.group(2)
        lay = layer(fn)
        if lay:
            totals[lay] += n
            funcs[fn.split("/")[-1]] += n
    return totals, funcs


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("harness")
    ap.add_argument("out")
    ap.add_argument("--ref")
    ap.add_argument("--only")
    ap.add_argument("--reuse", action="store_true",
                    help="read the dumps of the last run in OUT_DIR again")
    ap.add_argument("--top", type=int, default=0,
                    help="also list each scene's N costliest functions")
    args = ap.parse_args()
    exe = os.path.abspath(os.path.join(args.harness, "dchost"))
    os.makedirs(args.out, exist_ok=True)
    runs = RUNS
    if args.only:
        keep = set(args.only.split(","))
        runs = {k: v for k, v in RUNS.items() if k in keep}
    procs = []
    for run, (sched, last, scenes) in ([] if args.reuse else runs.items()):
        d = os.path.join(args.out, run)
        os.makedirs(d, exist_ok=True)
        for f in glob.glob(os.path.join(d, "cg.*")) + glob.glob(
                os.path.join(d, "*.ppm")):
            os.remove(f)
        env = dict(os.environ, DCHOST_DIR=".",
                   DCHOST_BENCH=",".join(f"{n}:{a}:{b}" for n, a, b in scenes),
                   DCHOST_SAVE=",".join(str(b) for _, _, b in scenes))
        cmd = ["valgrind", "--tool=callgrind", "--instr-atstart=no",
               "--dump-instr=no", "--callgrind-out-file=cg.%p", exe,
               str(last)] + sched.split()
        procs.append((run, subprocess.Popen(
            cmd, cwd=d, env=env, stdout=open(os.path.join(d, "log"), "w"),
            stderr=subprocess.STDOUT)))
    for run, p in procs:
        if p.wait():
            sys.exit(f"{run}: harness failed, see {args.out}/{run}/log")

    rows = []
    for run, (sched, last, scenes) in runs.items():
        d = os.path.join(args.out, run)
        dumps = {}
        for f in glob.glob(os.path.join(d, "cg.*.*")):
            with open(f, errors="replace") as fh:
                for line in fh:
                    m = re.match(r"desc: Trigger: Client Request: (\S+)", line)
                    if m:
                        dumps[m.group(1)] = f
                        break
        for name, a, b in scenes:
            if name not in dumps:
                sys.exit(f"{run}: no dump for {name}")
            totals, funcs = read_dump(dumps[name])
            same = ""
            if args.ref:
                frames = sorted(glob.glob(os.path.join(d, "*.ppm")))
                mine = [f for f in frames
                        if abs(int(re.findall(r"\d+", os.path.basename(f))[0])
                               - b) <= 2]
                ref = os.path.join(args.ref, run, os.path.basename(mine[0])) \
                    if mine else None
                if ref and os.path.exists(ref):
                    same = "same" if open(mine[0], "rb").read() == open(
                        ref, "rb").read() else "DIFF"
                else:
                    same = "?"
            rows.append((name, b - a, totals, funcs, same))

    head = f"{'scene':16} {'k instr/vblank':>14}" + "".join(
        f" {l[:9]:>9}" for l in LAYERS) + ("  picture" if args.ref else "")
    print(head)
    grand = collections.Counter()
    for name, vbl, totals, funcs, same in rows:
        tot = sum(totals.values())
        grand.update(totals)
        print(f"{name:16} {tot / vbl / 1000:14.0f}" + "".join(
            f" {100 * totals[l] / tot:8.1f}%" for l in LAYERS)
            + (f"  {same}" if args.ref else ""))
    tot = sum(grand.values())
    print(f"{'all scenes':16} {tot / 1e9:13.2f}G" + "".join(
        f" {100 * grand[l] / tot:8.1f}%" for l in LAYERS))
    if args.top:
        for name, vbl, totals, funcs, same in rows:
            print(f"\n{name}:")
            for fn, n in funcs.most_common(args.top):
                print(f"  {n / vbl / 1000:8.1f}k/vblank  {fn}")


if __name__ == "__main__":
    main()
