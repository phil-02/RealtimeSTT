# Voice-to-keyboard with real-time transcription
# Setup: source .venv/bin/activate
# Start with: uv pip install -e ".[sherpa-onnx,silero-onnx-cpu]" jeepney
#             stt-install-sherpa-models --root ./models/sherpa-onnx --model all
# Run with: uv run python type.py

import argparse
import logging
from difflib import SequenceMatcher
from pathlib import Path

from RealtimeSTT import AudioToTextRecorder

# === PINNED CPU MODEL BUNDLES ===
# Installed by: stt-install-sherpa-models --root ./models/sherpa-onnx --model all
MODEL_ROOT = Path(__file__).resolve().parent / "models" / "sherpa-onnx"
NEMOTRON_MODEL = MODEL_ROOT / "sherpa-onnx-nemotron-3.5-asr-streaming-0.6b-560ms-int8-2026-06-11"
PARAKEET_MODEL = MODEL_ROOT / "sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8"

# RealtimeSTT writes this relative to the working directory, not to the script,
# so truncate the same relative path it will open.
LOG_FILE = "realtimesst.log"

# Echo live hypotheses to the terminal type.py was started from. Off by
# default: it puts spoken content on screen and into terminal scrollback,
# even though nothing goes to the log. Turn on only for debugging.
SHOW_LIVE_PREVIEW = False

# === YOUR VOCABULARY ===
# Nemotron cannot take sherpa hotwords (it is greedy-search only, and sherpa
# requires modified_beam_search for hotword biasing), so domain terms are
# corrected here instead. This runs only on Parakeet's final text, matching
# whole words case-insensitively and keeping your capitalization.
# Add "what it hears": "what you meant".
VOCABULARY = {
    "versatable": "versatile",
    "lightninging": "lightning",
    "cabilities": "capabilities",
    "super bass": "Supabase"
}

# Provisional characters currently present in the focused application. They
# are replaced wholesale by Parakeet's final so live feedback cannot reduce
# the accuracy of the completed transcript.
typed = ""

# Portal keyboard session, opened in main(). Spawned workers re-import this
# module but never type, so they never open one.
keyboard = None


def check_models():
    """Fail early if either pinned model bundle is incomplete."""
    required_files = {
        NEMOTRON_MODEL: (
            "encoder.int8.onnx",
            "decoder.int8.onnx",
            "joiner.int8.onnx",
            "tokens.txt",
        ),
        PARAKEET_MODEL: (
            "encoder.int8.onnx",
            "decoder.int8.onnx",
            "joiner.int8.onnx",
            "tokens.txt",
        ),
    }
    for model_dir, filenames in required_files.items():
        missing = [name for name in filenames if not (model_dir / name).is_file()]
        if not model_dir.is_dir() or missing:
            detail = f" (missing: {', '.join(missing)})" if missing else ""
            raise SystemExit(
                f"Missing or incomplete model bundle: {model_dir}{detail}\n"
                "Install it with: "
                "stt-install-sherpa-models --root ./models/sherpa-onnx --model all"
            )


def check_setup():
    """Validate the non-interactive parts of the dictation setup."""
    check_models()
    try:
        import jeepney
    except ImportError as error:
        raise SystemExit(
            "Missing Python dependency: jeepney\n"
            "Install with: uv pip install jeepney"
        ) from error

    try:
        import pyaudio
        import sherpa_onnx
        import silero_vad
    except ImportError as error:
        raise SystemExit(
            f"Missing Python dependency: {error.name}\n"
            'Install with: uv pip install -e ".[sherpa-onnx,silero-onnx-cpu]"'
        ) from error

    print("Setup check passed")
    print(f"  realtime (provisional): {NEMOTRON_MODEL.name}")
    print(f"  final (authoritative):  {PARAKEET_MODEL.name}")
    print("  keyboard policy:        Parakeet final only")


# RealtimeSTT logs at DEBUG to realtimesst.log (initialization.py:348), which
# means per-audio-chunk chatter roughly twice a second plus a full copy of the
# accumulated transcript on every realtime update. Keep INFO and above only:
# the log stays small and dictation no longer lands on disk.
LOG_LEVEL = logging.INFO


class DropBelowLevel(logging.Filter):
    """Drops records under LOG_LEVEL, whichever process emits them."""

    def filter(self, record):
        return record.levelno >= LOG_LEVEL


# Installed at module scope so the audio worker process, which re-imports this
# module under spawn, filters its own records too.
logging.getLogger("realtimestt").addFilter(DropBelowLevel())


# The library creates its DEBUG file handler during recorder construction, and
# the audio worker process is created in that same call -- so raising the level
# afterwards is too late for the worker, which inherits the handler as it was.
# Create it at LOG_LEVEL instead, before construction.
_LibraryFileHandler = logging.FileHandler


# Per-chunk chatter the audio worker emits ~twice a second whether or not
# anyone is speaking -- 85% of an idle run's lines. Dropped even at DEBUG.
LOG_NOISE = ("_audio_data_worker writing audio data into queue",)


class DropLogNoise(logging.Filter):
    """Drops the high-frequency per-chunk debug lines."""

    def filter(self, record):
        message = record.getMessage()
        return not any(noise in message for noise in LOG_NOISE)


class QuietFileHandler(_LibraryFileHandler):
    """A FileHandler built at LOG_LEVEL, with chunk noise filtered out.

    Both the level and the filter are applied here, at construction, because
    the audio worker process inherits this handler as it exists during
    recorder setup -- changing either afterwards is too late for the worker.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setLevel(LOG_LEVEL)
        self.addFilter(DropLogNoise())


logging.FileHandler = QuietFileHandler


def reset_log():
    """Truncate the debug log so it does not grow across runs.

    RealtimeSTT opens `realtimesst.log` in append mode, relative to the
    current working directory (core/initialization.py). It logs every realtime
    update at DEBUG, re-logging the whole accumulated sentence each time, so
    an appended file grows without bound and keeps every past dictation.
    """
    try:
        with open(LOG_FILE, "w"):
            pass
    except OSError as error:
        print(f"Could not reset {LOG_FILE}: {error}")


def apply_vocabulary(text):
    """Replace misheard domain terms, preserving each word's capitalization."""
    if not VOCABULARY:
        return text

    def fix(word):
        bare = "".join(c for c in word if c.isalnum() or c == "'")
        replacement = VOCABULARY.get(bare.lower())
        if not replacement:
            return word
        if bare.isupper():
            replacement = replacement.upper()
        elif bare[:1].isupper():
            replacement = replacement[:1].upper() + replacement[1:]
        return word.replace(bare, replacement, 1)

    return " ".join(fix(w) for w in text.split(" "))


def send_keys(action, *args):
    """Run one keyboard action, reporting rather than raising on failure.

    These run inside recorder callbacks, so an exception would take down the
    recorder thread. A failed session is dropped and the next call asks again.
    """
    from portal_keyboard import PortalError

    try:
        action(*args)
    except PortalError as error:
        print(f"Keyboard input failed: {error}")


def type_text(text):
    """Type text through the RemoteDesktop portal (supports Unicode)."""
    send_keys(keyboard.type, text)


def press_backspace(count):
    """Erase provisional characters before publishing the final result."""
    if count > 0:
        send_keys(keyboard.key, "BackSpace", count)


def move_cursor_left(count):
    """Move left within the provisional text without changing it."""
    if count > 0:
        send_keys(keyboard.key, "Left", count)


def move_cursor_right(count):
    """Return to the end after applying right-to-left corrections."""
    if count > 0:
        send_keys(keyboard.key, "Right", count)


def on_stabilized_delta(event):
    """Type each append-only stabilized delta while speech is in progress."""
    global typed
    if not event.should_publish or not event.stable_delta:
        return

    chunk = event.stable_delta
    if not typed:
        chunk = chunk[:1].upper() + chunk[1:]
    chunk = apply_vocabulary(chunk)
    type_text(chunk)
    typed += chunk


def on_recording_stop():
    """Publish likely terminal punctuation before final decoding starts."""
    global typed
    if typed and not typed[-1].isspace() and not typed.endswith((".", "?", "!")):
        type_text(".")
        typed += "."


def on_transcription_finished(text):
    """Apply minimal local edits to make provisional text match Parakeet."""
    global typed
    final = apply_vocabulary(text) if text else ""
    if not final:
        press_backspace(len(typed))
        typed = ""
        return

    cursor = len(typed)
    opcodes = SequenceMatcher(None, typed, final, autojunk=False).get_opcodes()
    for tag, typed_start, typed_end, final_start, final_end in reversed(opcodes):
        if tag == "equal":
            continue

        move_cursor_left(cursor - typed_end)
        cursor = typed_end

        erase_count = typed_end - typed_start
        if erase_count:
            press_backspace(erase_count)
            cursor -= erase_count

        replacement = final[final_start:final_end]
        if replacement:
            type_text(replacement)
            cursor += len(replacement)

    move_cursor_right(len(final) - cursor)
    type_text("\n")
    typed = ""


def on_recording_start():
    print("Recording starts")


def on_realtime_update(text):
    """Optional terminal echo of raw live hypotheses.

    Per RELEASE_NOTES 1.0.4 these are display-only and get revised, so this
    text is never typed. The keyboard is driven only by the Parakeet final.
    """
    if SHOW_LIVE_PREVIEW:
        print(f"\r{text}", end="", flush=True)


def build_recorder(*, use_microphone=True):
    """Build the CPU streaming recorder: Nemotron live, Parakeet final."""
    return AudioToTextRecorder(
        spinner=False,
        use_microphone=use_microphone,
        enable_realtime_transcription=True,

        # === CPU SETTINGS ===
        device="cpu",

        # === FINAL: Parakeet decodes the complete turn exactly once ===
        transcription_engine="sherpa_onnx_parakeet",
        model=str(PARAKEET_MODEL),
        transcription_engine_options={
            # Match the i7-7700K's four physical cores. Using all eight
            # hyperthreads oversubscribes the live VAD/Nemotron workload.
            "num_threads": 4,
            "provider": "cpu",
        },

        # === REALTIME: Nemotron streams only new audio frames ===
        realtime_transcription_engine="sherpa_onnx_nemotron",
        realtime_model_type=str(NEMOTRON_MODEL),
        realtime_transcription_engine_options={
            "num_threads": 2,
            "provider": "cpu",
            # Measured on tests/unit/audio: cuts Nemotron's WER from 0.311 to
            # 0.218 (0.189 once a normal end-of-turn pause is included).
            # 2.0 is the optimum -- 3.0 regresses, 4.0+ collapses entirely.
            # This NeMo transducer supports greedy_search only, so beam search
            # and temperature_scale are not available levers.
            "blank_penalty": 2.0,
        },

        # === ALLOW SHORT PAUSES BETWEEN SENTENCES ===
        post_speech_silence_duration=1.5,
        silero_sensitivity=0.3,

        # Decode only after the complete turn has closed. Early transcription
        # is faster, but an accuracy-first profile should always submit the
        # recorder's final audio buffer.
        early_transcription_on_silence=0,

        language="en",

        # Console preview of the raw (revisable) live hypotheses.
        on_realtime_transcription_update=on_realtime_update,
        # Keyboard feedback uses only stabilized complete words. Parakeet
        # replaces all of it at turn end, preserving authoritative accuracy.
        on_realtime_text_stabilization_update=on_stabilized_delta,
        # Do not make terminal punctuation wait for the offline final decode.
        # Parakeet corrects this provisional period when it returns.
        on_recording_stop=on_recording_stop,
    )


def main():
    global keyboard
    parser = argparse.ArgumentParser(
        description="Accuracy-first realtime voice-to-keyboard transcription."
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="validate dependencies and model files without opening the microphone",
    )
    parser.add_argument(
        "--smoke-test",
        action="store_true",
        help="load both models without opening the microphone, then exit",
    )
    args = parser.parse_args()

    check_setup()
    if args.check:
        return
    if args.smoke_test:
        recorder = build_recorder(use_microphone=False)
        recorder.shutdown()
        print("Model load smoke test passed")
        return
    from portal_keyboard import PortalError, PortalKeyboard

    print("Asking GNOME for keyboard access (Remote Desktop prompt)...")
    keyboard = PortalKeyboard()
    try:
        keyboard.connect()
    except PortalError as error:
        raise SystemExit(f"Cannot type without keyboard access: {error}")
    print("Keyboard access granted")

    reset_log()
    recorder = build_recorder()

    print("--- STT Application Started ---")
    print("Speak into your microphone. (Ctrl+C to stop)")

    try:
        while True:
            recorder.text(on_transcription_finished)
    except KeyboardInterrupt:
        print("Exiting application due to keyboard interrupt")
    finally:
        recorder.shutdown()
        keyboard.close()


# RealtimeSTT sets the multiprocessing start method to "spawn" (unconditionally,
# not just on Windows -- see RealtimeSTT/core/initialization.py). A spawned child
# re-imports this module, so anything at module level runs again in every worker
# and recorder construction must stay behind this guard or it would recurse.
# This is about correctness, not speed: it does not affect core utilization.
if __name__ == '__main__':
    main()
