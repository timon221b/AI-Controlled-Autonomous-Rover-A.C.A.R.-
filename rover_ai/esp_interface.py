import time
import threading
import logging
import serial
import serial.tools.list_ports
from motion_parser import (MotionSequence, build_packet,
                            build_abort_packet, build_resume_packet)

log = logging.getLogger(__name__)

BAUD_RATE          = 115200
ACK_TIMEOUT        = 2.0
TELEMETRY_PKT_SIZE = 15
MOTION_PKT_SIZE    = 14

MAGIC_0          = 0xAB
MAGIC_1          = 0xCD
PKT_ACK_OK       = 0x10
PKT_ACK_REJECTED = 0x11
PKT_SAFE_STATE   = 0x12
PKT_TELEMETRY    = 0x20


def find_esp32_port() -> str:
    """Auto-detect CP2102 or CH340 USB-serial chip."""
    KNOWN = {(0x10C4, 0xEA60), (0x1A86, 0x7523)}
    for p in serial.tools.list_ports.comports():
        if (p.vid, p.pid) in KNOWN:
            log.info(f"Found ESP32 on {p.device} ({p.description})")
            return p.device
    raise RuntimeError(
        "No ESP32 found. Check USB cable and drivers.\n"
        "CP2102: https://www.silabs.com/developers/usb-to-uart-bridge-vcp-drivers\n"
        "CH340:  https://www.wch-ic.com/downloads/CH341SER_EXE.html"
    )


class ESPInterface:

    def __init__(self, port: str = None):
        self._port = port or find_esp32_port()
        self._ser  = serial.Serial(self._port, BAUD_RATE, timeout=1.0)
        self._lock = threading.Lock()
        self._last_telemetry: dict = {}
        self._pending: dict[int, threading.Event] = {}
        self._pending_lock = threading.Lock()
        self._last_ack_type: dict[int, int] = {}

        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()
        log.info(f"ESPInterface ready on {self._port}")

    def _read_loop(self):
        """
        Syncs on 0xAB 0xCD magic, reads version+type first to determine
        total packet size, then reads remaining bytes accordingly.
        This prevents desync when telemetry (15 bytes) and motion ACK
        (14 bytes) packets are interleaved.
        """
        while True:
            try:
                # Step 1: find magic bytes
                b = self._ser.read(1)
                if not b or b[0] != MAGIC_0:
                    continue
                b2 = self._ser.read(1)
                if not b2 or b2[0] != MAGIC_1:
                    continue

                # Step 2: read version + packet_type (2 bytes)
                header = self._ser.read(2)
                if len(header) < 2:
                    continue

                # version = header[0]
                pkt_type = header[1]

                # Step 3: determine remaining bytes based on packet type
                if pkt_type == PKT_TELEMETRY:
                    # telemetry = 15 bytes total, already read 4 (magic+version+type)
                    remaining = TELEMETRY_PKT_SIZE - 4
                else:
                    # all other packets (ACK, SAFE_STATE, etc) = 14 bytes total
                    remaining = MOTION_PKT_SIZE - 4

                rest = self._ser.read(remaining)
                if len(rest) < remaining:
                    continue

                # seq_id is first byte after version+type
                seq_id = rest[0]

                if pkt_type == PKT_ACK_OK or pkt_type == PKT_ACK_REJECTED:
                    with self._pending_lock:
                        self._last_ack_type[seq_id] = pkt_type
                        ev = self._pending.get(seq_id)
                    if ev:
                        ev.set()

                elif pkt_type == PKT_SAFE_STATE:
                    log.warning(f"Rover SAFE STATE reason=0x{seq_id:02X}")

                elif pkt_type == PKT_TELEMETRY:
                    self._parse_telemetry(rest)

            except serial.SerialException as e:
                log.error(f"Serial error: {e}")
                self._reconnect()
            except Exception as e:
                log.error(f"Read loop: {e}")

    def _parse_telemetry(self, data: bytes):
        """
        data starts after magic(2) + version(1) + type(1) = 4 bytes already read
        so: safety_state=data[0], battery=data[1], front=data[2:4],
            rear=data[4:6], current_step=data[6], total_steps=data[7]
        """
        try:
            if len(data) >= 8:
                self._last_telemetry = {
                    "safety_state":      data[0],
                    "battery_pct":       data[1],
                    "obstacle_front_cm": int.from_bytes(data[2:4], "little"),
                    "obstacle_rear_cm":  int.from_bytes(data[4:6], "little"),
                    "current_step":      data[6],
                    "total_steps":       data[7],
                }
        except Exception as e:
            log.error(f"Telemetry parse: {e}")

    def _reconnect(self):
        """Auto-reconnect on USB replug."""
        for attempt in range(5):
            try:
                time.sleep(2.0)
                self._ser.close()
                self._ser.open()
                log.info("Serial reconnected")
                return
            except Exception:
                log.warning(f"Reconnect attempt {attempt + 1} failed")
        log.error("Could not reconnect to ESP32")

    def _safe_write(self, data: bytes) -> bool:
        try:
            self._ser.write(data)
            return True
        except serial.SerialException:
            self._reconnect()
            return False

    def send_sequence(self, seq: MotionSequence) -> bool:
        """Send steps one at a time, waiting for per-packet ACK."""
        with self._lock:
            for step in seq.steps:
                packet = build_packet(seq, step)
                sid    = seq.sequence_id

                ev = threading.Event()
                with self._pending_lock:
                    self._pending[sid] = ev

                if not self._safe_write(packet):
                    with self._pending_lock:
                        self._pending.pop(sid, None)
                    return False

                if not ev.wait(timeout=ACK_TIMEOUT):
                    log.error(f"ACK timeout step {step.step_index}")
                    with self._pending_lock:
                        self._pending.pop(sid, None)
                    return False

                with self._pending_lock:
                    ack = self._last_ack_type.pop(sid, None)
                    self._pending.pop(sid, None)

                if ack == PKT_ACK_REJECTED:
                    log.warning(f"Step {step.step_index} rejected")
                    return False
        return True

    def send_abort(self):
        """Emergency stop — fire and forget."""
        with self._lock:
            self._safe_write(build_abort_packet())
        log.warning("ABORT sent")

    def send_resume(self):
        """Unlock rover from safe state."""
        with self._lock:
            self._safe_write(build_resume_packet())
        log.info("RESUME sent")

    @property
    def telemetry(self) -> dict:
        return self._last_telemetry.copy()

    def close(self):
        self._ser.close()