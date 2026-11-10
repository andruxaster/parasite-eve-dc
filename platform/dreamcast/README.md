# Sega Dreamcast host

`dc_main.c` replaces the desktop `src/main/boot/main.c` on KallistiOS: it
mounts the two USA disc images and runs the original scene dispatch loop.
`dc_sdl.c` provides the handful of SDL3 calls the game modules make (save
files, environment, ticks); SDL itself is not built. `dc_libc.c` adds the
stdio locks newlib declares but does not build.

The PsyZ side lives in `third_party/psyz/psyz/src/dreamcast/`: a PowerVR
renderer (see its README for what is and isn't emulated), AICA sound and
memory cards on VMUs (card 1 in controller port A, card 2 in port B).

## Build

Needs a KallistiOS toolchain (`sh-elf` GCC, newlib) and a built KOS tree.

```sh
source /opt/toolchains/dc/kos/environ.sh
cmake -S . -B build-dc -G Ninja \
  -DCMAKE_TOOLCHAIN_FILE=$KOS_CMAKE_TOOLCHAIN \
  -DCMAKE_BUILD_TYPE=Release -DPE_SMALL_MEMORY=ON
cmake --build build-dc
sh-elf-size build-dc/parasite-eve.elf
```

This build logs to the debug console: GPU statistics and the profiler
(`prof:` lines) every 300 frames, movie timings, mounted discs. For players,
add `-DPE_DC_RELEASE=ON` (in its own build directory): no KOS banner, no
logs, no profiler.

`prof_report.py` names the profile's functions, window by window, from a
saved log and the ELF of the same build:

```sh
python3 platform/dreamcast/prof_report.py console.log build-dc/parasite-eve.elf \
  --match "verts/frame" --top 15
```

The buckets are 128 bytes of code, so a bucket that starts in a small
function before memcpy (`strnlen`, `L_odddst`) is memcpy's.

## Disc images

Raw MODE2/2352 BIN images need no CUE sheet: the image type comes from its
ISO9660 header and which disc it is from its contents. The first set that
opens wins:

| Where | Disc 1 | Disc 2 |
| --- | --- | --- |
| burnt disc (ISO9660) | `/cd/PE1.BIN` | `/cd/PE2.BIN` |
| | `/cd/PE1.CUE` | `/cd/PE2.CUE` |
| dc-tool host folder | `/pc/pe1.bin` | `/pc/pe2.bin` |
| | `/pc/pe1.cue` | `/pc/pe2.cue` |
| SD card adapter | `/sd/pe1.bin` | `/sd/pe2.bin` |
| | `/sd/pe1.cue` | `/sd/pe2.cue` |

The two images (about 495 and 647 MB) do not fit on one CD-R, so burn two
discs: `1ST_READ.BIN` with `PE1.BIN`, and `1ST_READ.BIN` with `PE2.BIN`.
Boot disc 1. When the game needs the other disc, a prompt asks for it:
open the lid, change the disc and close it; the game goes on once the
right disc is read (a wrong one is ignored). Booting disc 2 asks for disc 1
for the title screen the same way. Over dc-tool or SD both images are
mounted from the start, so there is no prompt.

### GD-ROM images (GDI)

For an optical drive emulator (GDEMU, MODE, USB-GDROM) or an emulator,
`make_gdi.py` builds a GDI of each disc. On a GD-ROM, `1ST_READ.BIN` is the
**unscrambled** binary (objcopy's output, before `scramble`), and
IP.BIN comes from KallistiOS's makeip:

```sh
sh-elf-objcopy -R .stack -O binary build-dc/parasite-eve.elf 1ST_READ.BIN
$KOS_BASE/utils/makeip/makeip -f -g "PARASITE EVE" -c ANDRUXASTER \
  -n T-PE001 -a U -i GD-ROM1/2 IP1.BIN    # and GD-ROM2/2 IP2.BIN
python3 platform/dreamcast/make_gdi.py --disc 1 --boot 1ST_READ.BIN \
  --ip IP1.BIN "Parasite Eve (USA) (Disc 1).bin" gdi-disc1
```

Each GDI has three tracks: a small file system with a note (track 1),
silence (track 2) and the high-density area from LBA 45000 (track 3): IP.BIN
in its first 16 sectors, then 1ST_READ.BIN and `PE1.BIN` (or `PE2.BIN`).
Sectors are raw Mode 1 with EDC and ECC, like redump's GDIs, which needs
numpy; `--iso` writes 2048-byte sectors without it. A disc takes 15-30 s.
The two images (557,816 sectors together) do not fit on one GD-ROM
(504,150 sectors from LBA 45000), so there is one GDI per disc, as on CD-R.

Disc images are never part of this repository.
