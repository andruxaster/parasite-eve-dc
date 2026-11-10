#!/usr/bin/env python3
"""Builds a GD-ROM image (GDI) of one Parasite Eve disc, for optical drive
emulators (GDEMU, MODE, USB-GDROM) and emulators.

usage: make_gdi.py --disc 1 --boot 1ST_READ.BIN --ip IP.BIN \\
                   "Parasite Eve (USA) (Disc 1).bin" OUT_DIR

OUT_DIR gets disc.gdi, track01.bin, track02.raw and track03.bin. Track 3,
the high-density area at LBA 45000, holds IP.BIN in its first 16 sectors and
an ISO 9660 file system with 1ST_READ.BIN and the disc image as PE1.BIN (or
PE2.BIN), as on the burnt CD-R. Make one GDI per disc: the two images do not
fit on one GD-ROM.

1ST_READ.BIN must be the plain binary (objcopy of the ELF), not the
scrambled one that a CD-R needs: a GD-ROM boots it unscrambled. IP.BIN comes
from KallistiOS's makeip with "-i GD-ROM1/2" (or 2/2).

Sectors are written raw (2352 bytes, Mode 1 with EDC and ECC) like
redump's GDIs; that needs numpy (pip install numpy). --iso writes 2048-byte
sectors instead (track03.iso), which needs nothing but not every drive
emulator takes.
"""
import argparse
import itertools
import os
import struct
import sys
import time

SECTOR = 2048
RAW = 2352
HD_START = 45000  # first LBA of the high-density area
HD_END = 549150  # end of a GD-ROM

# --- ISO 9660 -------------------------------------------------------------


def both16(v):
    return struct.pack("<H", v) + struct.pack(">H", v)


def both32(v):
    return struct.pack("<I", v) + struct.pack(">I", v)


def dir_record(name, lba, size, is_dir):
    ident = name.encode("ascii")
    length = 33 + len(ident) + (0 if len(ident) % 2 else 1)
    rec = struct.pack("<BB", length, 0) + both32(lba) + both32(size)
    rec += bytes([126, 11, 10, 12, 0, 0, 0])  # 2026-11-10 12:00:00 GMT
    rec += bytes([2 if is_dir else 0, 0, 0]) + both16(1)
    rec += bytes([len(ident)]) + ident
    return rec.ljust(length, b"\0")


def iso_date():
    return b"2026111012000000\0"


def text(s, n):
    return s.encode("ascii").ljust(n, b" ")


def build_iso(base, files, system_area=b""):
    """The file system of a track starting at LBA base: [(name, size)].

    Returns (header sectors as bytes, [(name, offset in sectors)], total
    sectors). Files follow the header in order, each from a new sector.
    """
    root_off = 20
    first_file = 21
    layout = []
    off = first_file
    for name, size in files:
        layout.append((name, off, size))
        off += (size + SECTOR - 1) // SECTOR
    total = off
    root = dir_record("\0", base + root_off, SECTOR, True)
    root += dir_record("\1", base + root_off, SECTOR, True)
    for name, o, size in layout:
        root += dir_record(name + ";1", base + o, size, False)
    assert len(root) <= SECTOR
    root_self = dir_record("\0", base + root_off, SECTOR, True)
    pvd = bytes([1]) + b"CD001" + bytes([1, 0])
    pvd += text("SEGA SEGAKATANA", 32) + text("PARASITE_EVE", 32)
    pvd += bytes(8) + both32(base + total) + bytes(32)
    pvd += both16(1) + both16(1) + both16(SECTOR)
    path_table = struct.pack("<BBIH", 1, 0, base + root_off, 1) + b"\0\0"
    pvd += both32(len(path_table) - 2)
    pvd += struct.pack("<I", base + 18) + struct.pack("<I", 0)
    pvd += struct.pack(">I", base + 19) + struct.pack(">I", 0)
    pvd += root_self
    pvd += text("", 128) + text("ANDRUXASTER", 128) + text("", 128)
    pvd += text("PARASITE EVE DREAMCAST", 128)
    pvd += text("", 37) + text("", 37) + text("", 37)
    pvd += iso_date() * 2 + b"0000000000000000\0" * 2
    pvd += bytes([1, 0])
    pvd = pvd.ljust(SECTOR, b"\0")
    term = (bytes([255]) + b"CD001" + bytes([1])).ljust(SECTOR, b"\0")
    l_table = struct.pack("<BBIH", 1, 0, base + root_off, 1) + b"\0\0"
    m_table = struct.pack(">BBIH", 1, 0, base + root_off, 1) + b"\0\0"
    head = system_area.ljust(16 * SECTOR, b"\0")[:16 * SECTOR]
    head += pvd + term + l_table.ljust(SECTOR, b"\0")
    head += m_table.ljust(SECTOR, b"\0") + root.ljust(SECTOR, b"\0")
    assert len(head) == first_file * SECTOR
    return head, [(n, o) for n, o, _ in layout], total

# --- raw Mode 1 sectors -----------------------------------------------------


def tables():
    f = [0] * 256
    b = [0] * 256
    edc = [0] * 256
    for i in range(256):
        j = ((i << 1) ^ (0x11D if i & 0x80 else 0)) & 0xFF
        f[i] = j
        b[i ^ j] = i
        e = i
        for _ in range(8):
            e = (e >> 1) ^ (0xD8018001 if e & 1 else 0)
        edc[i] = e
    return f, b, edc


class RawWriter:
    """Mode 1 sectors with sync, header, EDC and ECC, many at a time."""

    def __init__(self):
        import numpy as np
        self.np = np
        f, b, edc = tables()
        self.f = np.array(f, np.uint8)
        self.b = np.array(b, np.uint8)
        self.edc = np.array(edc, np.uint32)
        # ECC: P over 86 columns of 24 bytes, Q over 52 diagonals of 43
        self.p_idx = self.ecc_index(86, 24, 2, 86)
        self.q_idx = self.ecc_index(52, 43, 86, 88)

    def ecc_index(self, major_count, minor_count, major_mult, minor_inc):
        size = major_count * minor_count
        idx = []
        for major in range(major_count):
            i = (major >> 1) * major_mult + (major & 1)
            row = []
            for _ in range(minor_count):
                row.append(i)
                i += minor_inc
                if i >= size:
                    i -= size
            idx.append(row)
        return self.np.array(idx).T  # [minor][major]

    def ecc(self, src, idx):
        np = self.np
        a = np.zeros((src.shape[0], idx.shape[1]), np.uint8)
        b = np.zeros_like(a)
        for minor in range(idx.shape[0]):
            t = src[:, idx[minor]]
            a ^= t
            b ^= t
            a = self.f[a]
        a = self.b[self.f[a] ^ b]
        return np.concatenate([a, a ^ b], axis=1)

    def sectors(self, data, lba):
        """data: n*2048 bytes, the first at LBA lba; returns n*2352 bytes."""
        np = self.np
        n = len(data) // SECTOR
        s = np.zeros((n, RAW), np.uint8)
        s[:, 1:11] = 0xFF
        fad = np.arange(lba + 150, lba + 150 + n)
        bcd = lambda v: (v // 10) * 16 + v % 10
        s[:, 12] = bcd(fad // 4500)
        s[:, 13] = bcd(fad // 75 % 60)
        s[:, 14] = bcd(fad % 75)
        s[:, 15] = 1
        s[:, 16:16 + SECTOR] = np.frombuffer(data, np.uint8).reshape(n, SECTOR)
        e = np.zeros(n, np.uint32)
        for k in range(16 + SECTOR):
            e = (e >> 8) ^ self.edc[(e ^ s[:, k]) & 0xFF]
        s[:, 0x810:0x814] = e.astype("<u4").view(np.uint8).reshape(n, 4)
        s[:, 0x81C:0x81C + 172] = self.ecc(s[:, 12:], self.p_idx)
        s[:, 0x81C + 172:RAW] = self.ecc(s[:, 12:], self.q_idx)
        return s.tobytes()


def write_track(path, lba, chunks, raw, total):
    """chunks: iterable of 2048-byte-aligned bytes; total: sectors."""
    writer = RawWriter() if raw else None
    done = 0
    start = time.time()
    with open(path, "wb") as out:
        for data in chunks:
            if raw:
                for i in range(0, len(data), 4096 * SECTOR):
                    part = data[i:i + 4096 * SECTOR]
                    out.write(writer.sectors(part, lba + done))
                    done += len(part) // SECTOR
                    if total > 20000:
                        pct = 100 * done // total
                        print(f"\r  {os.path.basename(path)}: {pct}%",
                              end="", flush=True)
            else:
                out.write(data)
                done += len(data) // SECTOR
    if raw and total > 20000:
        print(f"\r  {os.path.basename(path)}: 100% "
              f"({time.time() - start:.0f} s)")
    assert done == total, (done, total)


def file_chunks(path):
    """the file in blocks of whole sectors, the last one padded"""
    with open(path, "rb") as f:
        while True:
            block = f.read(64 * 4096 * SECTOR)
            if not block:
                break
            yield pad_to(block)


def pad_to(data):
    return data + bytes(-len(data) % SECTOR)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("image", help="the PS1 disc image (raw MODE2/2352 .bin)")
    ap.add_argument("out")
    ap.add_argument("--disc", type=int, choices=(1, 2), required=True)
    ap.add_argument("--boot", required=True,
                    help="1ST_READ.BIN, unscrambled")
    ap.add_argument("--ip", required=True, help="IP.BIN from makeip")
    ap.add_argument("--iso", action="store_true",
                    help="2048-byte sectors (no numpy needed)")
    args = ap.parse_args()

    raw = not args.iso
    if raw:
        try:
            import numpy  # noqa: F401
        except ImportError:
            sys.exit("raw sectors need numpy (pip install numpy); "
                     "or pass --iso")
    ip = open(args.ip, "rb").read()
    if len(ip) != 16 * SECTOR or ip[:16] != b"SEGA SEGAKATANA ":
        sys.exit(f"{args.ip} is not an IP.BIN")
    boot = open(args.boot, "rb").read()
    if boot[:4] == b"\x7fELF":
        sys.exit(f"{args.boot} is an ELF: objcopy it to a binary first")
    image_size = os.path.getsize(args.image)
    if image_size % 2352:
        print(f"warning: {args.image} is not a whole number of "
              "2352-byte sectors")
    name = f"PE{args.disc}.BIN"
    os.makedirs(args.out, exist_ok=True)
    ext = "bin" if raw else "iso"
    size = RAW if raw else SECTOR

    # track 1: low density, a small file system with a note
    note = (b"Parasite Eve for Dreamcast, disc %d.\r\n"
            b"The game is in the high-density area (track 3).\r\n"
            % args.disc)
    head1, layout1, total1 = build_iso(0, [("README.TXT", len(note))])
    total1 = max(total1, 300)
    track1 = head1 + pad_to(note)
    track1 += bytes(total1 * SECTOR - len(track1))
    write_track(os.path.join(args.out, f"track01.{ext}"), 0, [track1], raw,
                total1)

    # track 2: audio, silence
    with open(os.path.join(args.out, "track02.raw"), "wb") as f:
        f.write(bytes(302 * RAW))

    # track 3: IP.BIN, the file system, 1ST_READ.BIN, the disc image
    head3, layout3, total3 = build_iso(
        HD_START, [("1ST_READ.BIN", len(boot)), (name, image_size)], ip)
    if HD_START + total3 > HD_END:
        sys.exit(f"{name} does not fit on a GD-ROM")
    chunks = [head3, pad_to(boot)]
    assert layout3[1][1] == len(head3 + pad_to(boot)) // SECTOR
    print(f"disc {args.disc}: {total3} sectors from LBA {HD_START}")
    write_track(os.path.join(args.out, f"track03.{ext}"), HD_START,
                itertools.chain(chunks, file_chunks(args.image)),
                raw,
                total3)

    with open(os.path.join(args.out, "disc.gdi"), "w", newline="\r\n") as f:
        f.write("3\n")
        f.write(f"1 0 4 {size} track01.{ext} 0\n")
        f.write("2 450 0 2352 track02.raw 0\n")
        f.write(f"3 {HD_START} 4 {size} track03.{ext} 0\n")
    print(f"{args.out}/disc.gdi")


if __name__ == "__main__":
    main()
