# AGENTS.md - AI Coding Agent Guidelines for RealtimeSTT

## Project Overview

RealtimeSTT is a real-time speech-to-text library using faster-whisper, with voice activity detection (WebRTC VAD, Silero VAD) and wake word detection (Porcupine, OpenWakeWord). Main app is `type2.py` (voice-to-keyboard).

**Author:** Kolja Beigel | **License:** MIT | **GitHub:** https://github.com/KoljaB/RealTimeSTT

## Project Structure

```
RealtimeSTT/                    # Main package
  audio_recorder.py             # Core AudioToTextRecorder class (1900+ lines)
  audio_recorder_client.py      # WebSocket client wrapper
  audio_input.py                # Audio input handling
RealtimeSTT_server/             # Server package (stt_server.py, stt_cli_client.py)
tests/                          # Test scripts (no pytest - standalone scripts)
type2.py                        # Main application - voice-to-keyboard
```

## Build/Install/Run Commands

```bash
# Install and run (using uv - preferred)
uv pip install -r requirements.txt
uv run python type2.py

# Alternative: pip
pip install -r requirements.txt
pip install -e .

# Server/client CLI
stt-server -m large-v2 -l en -c 8011 -d 8012
stt
```

## Running Tests

No pytest - tests are standalone scripts:

```bash
python tests/simple_test.py           # Basic test
python tests/realtimestt_test.py      # Full demo with Rich UI
python tests/wakeword_test.py         # Wake word test
python tests/realtimestt_test.py -m small.en -l en  # With args
```

## Code Style Guidelines

### Imports
Order: Third-party, standard library, local (no strict blank line separation):
```python
from faster_whisper import WhisperModel
from typing import Iterable, List, Optional, Union
import torch.multiprocessing as mp
import numpy as np
import threading
import logging
```

### Naming Conventions
- **Classes:** PascalCase (`AudioToTextRecorder`, `TranscriptionWorker`)
- **Functions:** snake_case (`process_text`, `on_transcription_finished`)
- **Constants:** UPPER_SNAKE_CASE (`SAMPLE_RATE`, `BUFFER_SIZE`)
- **Module defaults:** `INIT_` prefix (`INIT_MODEL_TRANSCRIPTION`)
- **Callbacks:** `on_` prefix (`on_recording_start`, `on_vad_detect_stop`)

### Type Hints
Use for public APIs:
```python
def __init__(self,
             model: str = INIT_MODEL_TRANSCRIPTION,
             gpu_device_index: Union[int, List[int]] = 0,
             initial_prompt: Optional[Union[str, Iterable[int]]] = None):
```

### Error Handling
```python
try:
    data = self.conn.recv()
except Exception as e:
    logging.error(f"Error receiving data: {e}", exc_info=True)
```

### Multiprocessing Guard (REQUIRED)
Always use for scripts:
```python
if __name__ == '__main__':
    from RealtimeSTT import AudioToTextRecorder
    recorder = AudioToTextRecorder(...)
    try:
        while True:
            recorder.text(process_text)
    except KeyboardInterrupt:
        print("Exiting")
```

### Terminal Colors
```python
class bcolors:
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    ENDC = '\033[0m'
```

## Common Patterns

### Basic Usage
```python
recorder = AudioToTextRecorder(
    model="small.en",
    device="cpu",
    compute_type="int8",
    language="en",
    spinner=False,
    silero_sensitivity=0.3,
    post_speech_silence_duration=3.0,
    beam_size=1,
    on_recording_start=lambda: print("Recording..."),
    on_realtime_transcription_update=text_detected,
)

while True:
    recorder.text(on_transcription_finished)
```

## Key Dependencies

- `faster-whisper` - STT transcription
- `pvporcupine` / `openwakeword` - Wake word detection  
- `webrtcvad-wheels` - Voice activity detection
- `torch`, `torchaudio` - Deep learning
- `PyAudio` - Audio I/O
- `websockets` - WebSocket communication

## Notes for AI Agents

1. **No formal test framework** - Tests are standalone scripts, not pytest
2. **No linting config** - No .flake8, pylint, or ruff configured
3. **No CI/CD** - No GitHub Actions configured
4. **Multiprocessing** - ALWAYS use `if __name__ == '__main__':` guard
5. **CPU only** - No GPU/CUDA support in this environment
6. **Entry points** - type2.py
7. **Python version** - Requires >= 3.11 (pyproject.toml)
