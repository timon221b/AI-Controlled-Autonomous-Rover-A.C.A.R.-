import asyncio
import logging
import numpy as np
import sounddevice as sd
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from faster_whisper import WhisperModel
from intent_classifier import classify_intent, generate_chat_reply
from motion_parser     import (parse_motion_sequence,
                                MIN_CONFIDENCE, MAX_TOTAL_MS)
from esp_interface     import ESPInterface

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
log = logging.getLogger(__name__)

SAMPLE_RATE     = 16000
CHANNELS        = 1

# ── VAD recording parameters ──────────────────────────────────────
# FIX: replaced fixed 5s window with energy-based VAD.
# Recording now stops automatically after SILENCE_TIMEOUT seconds
# of silence, saving 3-4 seconds per short command.
VAD_FRAME_MS      = 30       # chunk size for energy analysis (ms)
VAD_FRAME_SAMPLES = int(SAMPLE_RATE * VAD_FRAME_MS / 1000)
SILENCE_THRESHOLD = 0.01     # RMS below this = silence
                              # Increase if noisy environment
SILENCE_TIMEOUT   = 0.8      # stop after 0.8s of continuous silence
MIN_SPEECH_MS     = 300      # ignore recordings shorter than this
MAX_RECORD_SEC    = 8.0      # hard ceiling — never record more than this

# ── Whisper domain prompt ─────────────────────────────────────────
# FIX: biases Whisper toward rover vocabulary, fixing mishearing of
# "turn left" → "Don't left", "turn right" → "Don't write" etc.
WHISPER_PROMPT = (
    "Rover voice commands: move forward, move backward, turn left, "
    "turn right, spin clockwise, spin counter-clockwise, stop, halt, "
    "pause, wait. Duration phrases: for 2 seconds, for 500 milliseconds. "
    "Speed phrases: slow, fast, full speed."
)

_executor = ThreadPoolExecutor(max_workers=3)


class RoverOrchestrator:

    def __init__(self):
        log.info("Loading Whisper...")
        # FIX: upgraded from 'tiny' to 'base' — barely slower on CPU but
        # dramatically more accurate for short domain-specific commands.
        # If CPU is too slow, keep 'tiny' but keep the initial_prompt.
        self.whisper = WhisperModel("base", device="cpu", compute_type="int8")
        log.info("Whisper loaded on CPU")

        log.info("Connecting to ESP32...")
        try:
            self.esp = ESPInterface()
        except RuntimeError as e:
            log.error(f"ESP32 not found: {e}")
            self._speak("Master ESP32 not detected. Running in AI-only mode.")
            self.esp = None

    # ── Step 1: VAD-based recording ───────────────────────────────
    def _record_with_vad(self) -> np.ndarray:
        """
        Record until silence is detected, instead of a fixed window.

        Timeline:
          - Starts recording immediately on Enter
          - Waits for speech to begin (pre-roll buffer holds last 300ms)
          - Stops SILENCE_TIMEOUT seconds after speech ends
          - Hard cap at MAX_RECORD_SEC

        Typical command "move forward for 2 seconds":
          ~1.5s speech + 0.8s silence = ~2.3s total (was 5.0s fixed)
        """
        log.debug("VAD recording started")
        pre_roll   = deque(maxlen=10)   # ~300ms of pre-speech audio
        audio_buf  = []
        speech_started   = False
        silence_frames   = 0
        max_frames = int(MAX_RECORD_SEC * 1000 / VAD_FRAME_MS)
        silence_frame_limit = int(SILENCE_TIMEOUT * 1000 / VAD_FRAME_MS)

        with sd.InputStream(samplerate=SAMPLE_RATE,
                            channels=CHANNELS,
                            dtype="float32",
                            blocksize=VAD_FRAME_SAMPLES) as stream:
            for _ in range(max_frames):
                frame, _ = stream.read(VAD_FRAME_SAMPLES)
                frame = frame.flatten()
                rms = float(np.sqrt(np.mean(frame ** 2)))

                if not speech_started:
                    pre_roll.append(frame)
                    if rms > SILENCE_THRESHOLD:
                        speech_started = True
                        audio_buf.extend(list(pre_roll))
                        audio_buf.append(frame)
                        silence_frames = 0
                else:
                    audio_buf.append(frame)
                    if rms < SILENCE_THRESHOLD:
                        silence_frames += 1
                        if silence_frames >= silence_frame_limit:
                            break   # clean end of speech
                    else:
                        silence_frames = 0

        if not audio_buf:
            return np.zeros(0, dtype="float32")

        audio = np.concatenate(audio_buf)
        duration_ms = len(audio) / SAMPLE_RATE * 1000
        log.debug(f"VAD captured {duration_ms:.0f}ms of audio")

        # Discard clips too short to be real speech (button noise, cough)
        if duration_ms < MIN_SPEECH_MS:
            log.debug("Audio too short — discarded")
            return np.zeros(0, dtype="float32")

        return audio

    async def record_audio_async(self) -> np.ndarray:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(_executor, self._record_with_vad)

    # ── Step 2: Whisper STT with domain prompt ────────────────────
    def _transcribe_blocking(self, audio: np.ndarray) -> str:
        if len(audio) == 0:
            return ""
        segments, _ = self.whisper.transcribe(
            audio,
            language="en",
            initial_prompt=WHISPER_PROMPT,   # FIX: domain bias
            beam_size=5,
            vad_filter=True,                 # faster_whisper built-in VAD
            vad_parameters=dict(
                min_silence_duration_ms=300,
                speech_pad_ms=100,
            )
        )
        return " ".join(s.text for s in segments).strip()

    async def transcribe_async(self, audio: np.ndarray) -> str:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            _executor, self._transcribe_blocking, audio
        )

    # ── Step 3: intent routing ────────────────────────────────────
    async def handle_text(self, text: str):
        if not text:
            return

        loop   = asyncio.get_event_loop()
        intent = await loop.run_in_executor(
            _executor, classify_intent, text
        )
        log.info(f"Intent: {intent}")

        category   = intent.get("category", "CONVERSATION")
        confidence = intent.get("confidence", 0.0)
        needs_conf = intent.get("requires_confirmation", False)
        source     = intent.get("source", "llm")
        log.debug(f"Intent source: {source}")

        # ── HARDWARE_MOTION ───────────────────────────────────────
        if category == "HARDWARE_MOTION":
            if confidence < MIN_CONFIDENCE or needs_conf:
                self._speak("I'm not sure about that motion. Please repeat.")
                return

            if self.esp is None:
                self._speak("No rover connection. Running in AI-only mode.")
                return

            seq = parse_motion_sequence(text)
            if seq is None:
                self._speak(
                    "I couldn't parse that sequence, or it was too long. "
                    f"Maximum total duration is {MAX_TOTAL_MS // 1000} seconds."
                )
                return

            self._speak(f"Executing {seq.total_steps} step sequence.")
            ok = await loop.run_in_executor(
                _executor, self.esp.send_sequence, seq
            )
            if not ok:
                self._speak("Rover didn't acknowledge. Check connection.")

        # ── HARDWARE_QUERY ────────────────────────────────────────
        elif category == "HARDWARE_QUERY":
            if self.esp is None:
                self._speak("No rover connected.")
                return
            t = self.esp.telemetry
            if t:
                self._speak(
                    f"Battery is {t.get('battery_pct','?')} percent. "
                    f"Front obstacle {t.get('obstacle_front_cm','?')} cm. "
                    f"Rear obstacle {t.get('obstacle_rear_cm','?')} cm. "
                    f"Safety state {t.get('safety_state','?')}."
                )
            else:
                self._speak("No telemetry data yet.")

        # ── CONVERSATION ──────────────────────────────────────────
        # FIX: was silently dropping conversation intents —
        # generate_chat_reply was also using wrong API endpoint.
        elif category == "CONVERSATION":
            reply = await loop.run_in_executor(
                _executor, generate_chat_reply, text
            )
            self._speak(reply)

        # ── VISION_QUERY ──────────────────────────────────────────
        elif category == "VISION_QUERY":
            self._speak("Vision mode not yet active.")

        # ── UNKNOWN / UNHANDLED ───────────────────────────────────
        else:
            self._speak("I didn't catch that. Try again.")

    def _speak(self, text: str):
        log.info(f"TTS: {text}")
        print(f"[ROVER] {text}")

    # ── Main async loop ───────────────────────────────────────────
    async def run(self):
        self._speak("Rover AI ready. Press Enter to speak.")
        while True:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(_executor, input)

            self._speak("Listening...")
            audio = await self.record_audio_async()

            if len(audio) == 0:
                self._speak("Didn't catch anything. Press Enter to try again.")
                continue

            text = await self.transcribe_async(audio)
            log.info(f"STT: '{text}'")

            if text:
                await self.handle_text(text)
            else:
                self._speak("Didn't catch that. Please try again.")


if __name__ == "__main__":
    orchestrator = RoverOrchestrator()
    asyncio.run(orchestrator.run())