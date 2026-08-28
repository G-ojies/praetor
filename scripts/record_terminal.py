#!/usr/bin/env python3
"""Record a real command's execution as video.

This is not a mock-up of a terminal. The command runs under a pty, every chunk
of output is stamped with the moment it actually arrived, and the frames are
rendered from that timeline. What you see is what happened, at the speed it
happened, which is what the contest means by unedited live execution.

Handles the subset of terminal behaviour the demo actually uses: printable
text, newlines, carriage returns, and SGR colour. Anything else is dropped
rather than guessed at, because a renderer that improvises escape sequences
produces footage that does not match the real session.

    python3 scripts/record_terminal.py --out evidence/run.mp4 -- ./.venv/bin/python scripts/demo.py
"""

from __future__ import annotations

import argparse
import os
import pty
import re
import select
import subprocess
import sys
import time
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

COLS, ROWS = 104, 30
PAD, LINE_H, CHAR_W = 26, 23, 10
FPS = 12

BG = (13, 18, 21)
FG = (230, 236, 239)
DIM = (110, 124, 133)
ANSI = {
    30: (60, 70, 78), 31: (210, 104, 92), 32: (92, 174, 135), 33: (214, 155, 60),
    34: (110, 150, 200), 35: (167, 139, 208), 36: (63, 179, 189), 37: FG,
    90: DIM, 91: (210, 104, 92), 92: (92, 174, 135), 93: (214, 155, 60),
    94: (110, 150, 200), 95: (167, 139, 208), 96: (63, 179, 189), 97: FG,
}
SGR = re.compile(r"\x1b\[([0-9;]*)m")
OTHER_ESC = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b[()][A-Za-z0-9]|\x1b[=>]")


def find_font(size: int):
    for path in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
        "/usr/share/fonts/TTF/DejaVuSansMono.ttf",
    ):
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


class Screen:
    """Append-only line buffer with per-character colour."""

    def __init__(self) -> None:
        self.lines: list[list[tuple[str, tuple, bool]]] = [[]]
        self.colour, self.dim = FG, False

    def _sgr(self, params: str) -> None:
        for raw in (params or "0").split(";"):
            code = int(raw or 0)
            if code == 0:
                self.colour, self.dim = FG, False
            elif code == 1:
                self.dim = False
            elif code == 2:
                self.dim = True
            elif code in ANSI:
                self.colour = ANSI[code]

    def feed(self, text: str) -> None:
        i = 0
        while i < len(text):
            m = SGR.match(text, i)
            if m:
                self._sgr(m.group(1)); i = m.end(); continue
            m = OTHER_ESC.match(text, i)
            if m:
                i = m.end(); continue
            ch = text[i]; i += 1
            if ch == "\n":
                self.lines.append([])
            elif ch == "\r":
                # A pty ends every line with CRLF, so a bare \r handler that
                # clears the line wipes all of them and renders an empty
                # screen. Only a lone \r, the progress-bar redraw, clears;
                # \r\n is consumed as a single line ending.
                if i < len(text) and text[i] == "\n":
                    i += 1
                    self.lines.append([])
                else:
                    self.lines[-1] = []
            elif ch == "\t":
                self.lines[-1].extend((" ", self.colour, self.dim) for _ in range(4))
            elif ch >= " ":
                if len(self.lines[-1]) >= COLS:
                    self.lines.append([])
                self.lines[-1].append((ch, self.colour, self.dim))

    def visible(self) -> list[list[tuple]]:
        return self.lines[-ROWS:]


def render(screen: Screen, font, width: int, height: int) -> Image.Image:
    img = Image.new("RGB", (width, height), BG)
    d = ImageDraw.Draw(img)
    for row, line in enumerate(screen.visible()):
        y = PAD + row * LINE_H
        for col, (ch, colour, dim) in enumerate(line):
            if ch == " ":
                continue
            c = tuple(int(v * 0.62) for v in colour) if dim else colour
            d.text((PAD + col * CHAR_W, y), ch, font=font, fill=c)
    return img


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="evidence/run.mp4")
    ap.add_argument("--fps", type=int, default=FPS)
    ap.add_argument("--tail", type=float, default=2.5, help="seconds to hold the final frame")
    ap.add_argument("cmd", nargs=argparse.REMAINDER)
    args = ap.parse_args()
    cmd = args.cmd[1:] if args.cmd and args.cmd[0] == "--" else args.cmd
    if not cmd:
        print("give a command after --"); return 2

    # Run it for real, under a pty so the program believes it has a terminal
    # and emits the colour it normally would.
    master, slave = pty.openpty()
    started = time.time()
    proc = subprocess.Popen(cmd, stdout=slave, stderr=slave, stdin=subprocess.DEVNULL,
                            env={**os.environ, "TERM": "xterm-256color", "COLUMNS": str(COLS)})
    os.close(slave)

    timeline: list[tuple[float, str]] = []
    while True:
        r, _, _ = select.select([master], [], [], 0.1)
        if r:
            try:
                data = os.read(master, 65536)
            except OSError:
                break
            if not data:
                break
            timeline.append((time.time() - started, data.decode("utf-8", "replace")))
        elif proc.poll() is not None:
            break
    os.close(master)
    proc.wait()
    duration = (timeline[-1][0] if timeline else 0) + args.tail
    print(f"  captured {len(timeline)} output chunks over {duration:.1f}s (exit {proc.returncode})")

    font = find_font(17)
    width = PAD * 2 + COLS * CHAR_W
    height = PAD * 2 + ROWS * LINE_H

    out = Path(args.out); out.parent.mkdir(parents=True, exist_ok=True)
    ff = subprocess.Popen(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "image2pipe", "-vcodec", "png",
         "-r", str(args.fps), "-i", "-", "-c:v", "libx264", "-pix_fmt", "yuv420p",
         "-crf", "20", str(out)], stdin=subprocess.PIPE)

    screen, cursor, frames = Screen(), 0, int(duration * args.fps)
    for frame in range(frames):
        t = frame / args.fps
        while cursor < len(timeline) and timeline[cursor][0] <= t:
            screen.feed(timeline[cursor][1]); cursor += 1
        render(screen, font, width, height).save(ff.stdin, "PNG")
    ff.stdin.close(); ff.wait()

    print(f"  {out}  {width}x{height}  {frames} frames @ {args.fps}fps  "
          f"{out.stat().st_size / 1_000_000:.1f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
