import re
import struct
import threading
from dataclasses import dataclass
from enum import IntEnum
from typing import List, Optional

# ── Must match packet.h exactly ──────────────────────────────────
class CommandType(IntEnum):
    MOVE_FORWARD  = 0x01
    MOVE_BACKWARD = 0x02
    TURN_LEFT     = 0x03
    TURN_RIGHT    = 0x04
    SPIN_CW       = 0x05
    SPIN_CCW      = 0x06
    STOP          = 0x07
    PAUSE         = 0x08

# MotionPacket_t layout (14 bytes total):
# magic[2], version, packet_type, sequence_id, total_steps,
# step_index, command, duration_ms(u16), speed_pct, flags, crc16(u16)
# Format: <2s B B B B B B H B B  → 12 bytes body, then +2 bytes CRC
MOTION_PACKET_FMT  = "<2sBBBBBBHBB"   # 12 bytes — no CRC
MOTION_PACKET_SIZE = 14               # 12 body + 2 CRC

MAGIC        = bytes([0xAB, 0xCD])
PROTOCOL_VER = 0x02
PKT_MOTION   = 0x01
PKT_ABORT    = 0x03
PKT_RESUME   = 0x05
FLAG_LAST    = 0x01
FLAG_ACK     = 0x02

assert len(MAGIC) == 2, "MAGIC must be exactly 2 bytes"
assert struct.calcsize(MOTION_PACKET_FMT) == MOTION_PACKET_SIZE - 2, \
    f"Format size mismatch: {struct.calcsize(MOTION_PACKET_FMT)} != 12"

MAX_STEPS      = 16
MAX_TOTAL_MS   = 30_000
DEFAULT_SPEED  = 60
MIN_CONFIDENCE = 0.75

MOTION_LIMITS = {
    CommandType.MOVE_FORWARD:  (100, 8000),
    CommandType.MOVE_BACKWARD: (100, 5000),
    CommandType.TURN_LEFT:     (100, 3000),
    CommandType.TURN_RIGHT:    (100, 3000),
    CommandType.SPIN_CW:       (200, 5000),
    CommandType.SPIN_CCW:      (200, 5000),
    CommandType.PAUSE:         (100, 3000),
}

KEYWORDS = {
    CommandType.MOVE_FORWARD:  ["go forward", "move forward", "forward",
                                "ahead", "straight"],
    CommandType.MOVE_BACKWARD: ["go backward", "move backward", "backward",
                                "back", "reverse"],
    CommandType.TURN_LEFT:     ["turn left"],
    CommandType.TURN_RIGHT:    ["turn right"],
    CommandType.SPIN_CW:       ["spin clockwise", "spin right",
                                "spin cw", "spin", "rotate", "turn around"],
    CommandType.SPIN_CCW:      ["spin anticlockwise", "spin counter",
                                "spin left", "spin ccw"],
    CommandType.STOP:          ["stop", "halt", "freeze", "cancel", "abort"],
    CommandType.PAUSE:         ["wait", "pause", "hold"],
}

DURATION_PATTERNS = [
    (r"(\d+(?:\.\d+)?)\s*seconds?", lambda m: int(float(m.group(1)) * 1000)),
    (r"(\d+(?:\.\d+)?)\s*secs?",    lambda m: int(float(m.group(1)) * 1000)),
    (r"(\d+(?:\.\d+)?)\s*ms\b",     lambda m: int(float(m.group(1)))),
    (r"for\s+a\s+bit",              lambda m: 1500),
    (r"\bquickly\b",                lambda m: 600),
    (r"\bslowly\b",                 lambda m: 2500),
]

SPIN_PATTERNS = [
    (r"(\d+)\s*times?",                           lambda m: int(m.group(1)) * 1200),
    (r"\bonce\b",                                 lambda m: 1200),
    (r"\btwice\b",                                lambda m: 2400),
    (r"\bhalf\s*(?:a\s*)?(?:turn|spin|rotation)", lambda m: 600),
    (r"\bfull\s*(?:turn|spin|rotation)",          lambda m: 1200),
    (r"\b360\b",                                  lambda m: 1200),
]

SPEED_MAP = [
    ("full speed", 100), ("full", 100),
    ("fast",        80),
    ("normal",      60),
    ("slow",        35),
    ("very slow",   20),
    ("crawl",       15),
]

_seq_lock = threading.Lock()
_seq_id   = 0

def _next_seq() -> int:
    global _seq_id
    with _seq_lock:
        _seq_id = (_seq_id + 1) % 256
        return _seq_id


def _crc16(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if (crc & 1) else crc >> 1
    return crc


@dataclass
class MotionStep:
    command:     CommandType
    duration_ms: int
    speed_pct:   int
    step_index:  int


@dataclass
class MotionSequence:
    steps:       List[MotionStep]
    sequence_id: int

    @property
    def total_steps(self): return len(self.steps)

    @property
    def total_duration_ms(self): return sum(s.duration_ms for s in self.steps)


def _match_command(fragment: str) -> Optional[CommandType]:
    f = fragment.lower()
    for cmd, kws in sorted(KEYWORDS.items(),
                           key=lambda x: max(len(k) for k in x[1]),
                           reverse=True):
        for kw in kws:
            if kw in f:
                return cmd
    return None


def _parse_duration(fragment: str, cmd: CommandType) -> int:
    if cmd in (CommandType.SPIN_CW, CommandType.SPIN_CCW):
        for pat, fn in SPIN_PATTERNS:
            m = re.search(pat, fragment, re.IGNORECASE)
            if m:
                return fn(m)
    for pat, fn in DURATION_PATTERNS:
        m = re.search(pat, fragment, re.IGNORECASE)
        if m:
            return fn(m)
    defaults = {
        CommandType.MOVE_FORWARD:  2000,
        CommandType.MOVE_BACKWARD: 1500,
        CommandType.TURN_LEFT:      800,
        CommandType.TURN_RIGHT:     800,
        CommandType.SPIN_CW:       1200,
        CommandType.SPIN_CCW:      1200,
        CommandType.PAUSE:         1000,
    }
    return defaults.get(cmd, 1000)


def _parse_speed(fragment: str) -> int:
    f = fragment.lower()
    for kw, spd in SPEED_MAP:
        if kw in f:
            return spd
    m = re.search(r"(\d+)\s*%", f)
    if m:
        return max(10, min(100, int(m.group(1))))
    return DEFAULT_SPEED


def _clamp(cmd: CommandType, ms: int) -> int:
    lo, hi = MOTION_LIMITS.get(cmd, (100, 5000))
    return max(lo, min(hi, ms))


def _split_fragments(text: str) -> List[str]:
    parts = re.split(
        r"\s*(?:then|and then|after that|after|followed by|"
        r"subsequently|next|,\s*then|,)\s*",
        text, flags=re.IGNORECASE
    )
    return [p.strip() for p in parts if p.strip()]


def build_packet(seq: "MotionSequence", step: MotionStep) -> bytes:
    is_last = (step.step_index == seq.total_steps - 1)
    flags   = FLAG_ACK | (FLAG_LAST if is_last else 0)
    body = struct.pack(
        MOTION_PACKET_FMT,
        MAGIC,                  # magic[2]
        PROTOCOL_VER,           # version
        PKT_MOTION,             # packet_type
        seq.sequence_id,        # sequence_id
        seq.total_steps,        # total_steps
        step.step_index,        # step_index
        int(step.command),      # command
        step.duration_ms,       # duration_ms  (uint16)
        step.speed_pct,         # speed_pct
        flags,                  # flags
    )
    return body + struct.pack("<H", _crc16(body))


def build_abort_packet() -> bytes:
    body = struct.pack(
        MOTION_PACKET_FMT,
        MAGIC, PROTOCOL_VER, PKT_ABORT,
        0, 0, 0, 0, 0, 0, 0
    )
    return body + struct.pack("<H", _crc16(body))


def build_resume_packet() -> bytes:
    body = struct.pack(
        MOTION_PACKET_FMT,
        MAGIC, PROTOCOL_VER, PKT_RESUME,
        0, 0, 0, 0, 0, 0, 0
    )
    return body + struct.pack("<H", _crc16(body))


def parse_motion_sequence(text: str) -> Optional[MotionSequence]:
    fragments = _split_fragments(text)
    steps: List[MotionStep] = []

    for frag in fragments:
        if len(steps) >= MAX_STEPS:
            break
        cmd = _match_command(frag)
        if cmd is None:
            continue
        if cmd == CommandType.STOP:
            return MotionSequence(
                steps=[MotionStep(CommandType.STOP, 0, 0, 0)],
                sequence_id=_next_seq()
            )
        ms  = _clamp(cmd, _parse_duration(frag, cmd))
        spd = _parse_speed(frag)
        steps.append(MotionStep(cmd, ms, spd, len(steps)))

    if not steps:
        return None

    seq = MotionSequence(steps=steps, sequence_id=_next_seq())
    if seq.total_duration_ms > MAX_TOTAL_MS:
        return None

    return seq