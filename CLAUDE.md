# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

RealtimeSTT is a Python speech-to-text library (`realtimestt`, currently 1.0.4) built around one
class, `AudioToTextRecorder`: VAD-gated recording, optional wake words, optional realtime partial
transcription, and pluggable ASR engines. Python >= 3.11. The wheel ships `RealtimeSTT/` and
`RealtimeSTT_server/` only; `example_*/`, `tests/`, `tools/` are source-checkout only.

This checkout is a fork (`origin` = phil-02/RealtimeSTT, `upstream` = KoljaB/RealtimeSTT). The
fork carries the production server and the pinned CPU streaming profile; check `git status -sb`
before trusting local docs, since master falling behind origin has already caused confusion.

## Commands

Use the venv interpreter (`.venv/bin/python`; this venv was created by uv, so prefer
`uv pip install` or the pip added via `ensurepip`). Tests are plain `unittest` — no pytest, no
conftest. Run from the repository root.

```bash
# Full fast suite (what CI runs; no model downloads, slow model tests self-skip)
python -m unittest discover -s tests/unit -p "test_*.py"

# Single module / class / test
python -m unittest -v tests.unit.test_preroll
python -m unittest -v tests.unit.test_nemotron_engine.NemotronGoldenTranscriptionTests
python -m unittest -v tests.unit.test_sherpa_onnx_engine.SherpaOnnxGoldenTranscriptionTests.test_transcribes_fixture_with_real_sherpa_parakeet_backend

# Console entry points
stt-server                  # legacy dual-websocket server
stt-server-production       # packaged production HTTP/WebSocket server (v1 protocol)
stt                         # CLI client for the legacy server
stt-install-kroko --build
stt-install-sherpa-models --root ./models/sherpa-onnx --model all   # ~1 GB, verified + atomic

# Browser reference app (source checkout only)
python example_fastapi_server/server.py --host 0.0.0.0 --port 8010
```

No linter or formatter is configured. `.github/workflows/release-checks.yml` runs three jobs:
`unit` (OS/Python matrix), `package` (build + isolated wheel smoke), and `real-model-acceptance`
(Nemotron + Parakeet golden tests, ~1 GB of archives, only on manual dispatch / `v*` tag /
`release/*` push).

Golden and real-model tests are opt-in behind `REALTIMESTT_*` variables; `docs/testing.md` is the
authoritative list (written in PowerShell — translate to `export`). Model caches go in the
git-ignored `test-model-cache/`.

## Architecture

```
microphone (audio_input.py) or feed_audio() / feed_audio_file()
  -> recorder audio queue
  -> wake word -> VAD -> pre-roll -> recording state machine
  -> optional realtime ASR + text stabilization
  -> final ASR engine (separate model worker process)
  -> callbacks / websocket messages / text() return value
```

16 kHz mono 16-bit PCM is the internal audio currency; anything else is resampled on the way in.

**`RealtimeSTT/audio_recorder.py` is a thin facade.** It owns the public constructor signature,
defaults, and method names, and delegates into `RealtimeSTT/core/*`: `initialization.py`
(worker/process/model startup), `recording.py`, `voice_activity.py`, `realtime.py`,
`transcription.py` + `transcription_api.py`, `wakeword.py`, `lifecycle.py`, `shutdown.py`. Change
behavior in the `core` module; leave the facade's signature intact.

Pure helpers — keep free of device, server, and model-runtime dependencies: `core/preroll.py`,
`core/realtime_boundary_detector.py`, `core/realtime_text_stabilizer.py`, `core/text_formatting.py`.

**Engine layer** (`RealtimeSTT/transcription_engines/`): `base.py` defines
`TranscriptionEngineConfig` / `TranscriptionResult` / `StreamingTranscriptionSession` / errors;
`factory.py` maps names and aliases to `(module, class)` and imports lazily. `RealtimeSTT/engines/`
is the *public* custom-engine API (`BaseEngine` and re-exports) for third-party engines injected
via `transcription_executor` / `realtime_transcription_executor` — see
`docs/custom-transcription-engines.md`.

Adding an engine: adapter module, entry in `ENGINE_CLASS_PATHS`, extras entry in `setup.py`, docs
page under `docs/engines/`, and fast contract tests (factory selection, missing-dependency error
text, config-to-backend option mapping, result conversion) before any opt-in golden test.

**Pinned models.** `RealtimeSTT/model_manifests.py` holds immutable archive URL / size / SHA-256 /
extracted-file metadata for the two pinned bundles; `install_sherpa_models.py` is the verifying,
resumable, atomic installer behind `stt-install-sherpa-models`. RealtimeSTT never auto-downloads
weights — model directories are always user-supplied and persistent.

**Laziness is a hard constraint.** `RealtimeSTT/__init__.py` exports via `__getattr__`, and every
optional runtime must be imported inside the function that needs it. `import RealtimeSTT` must not
pull in a model runtime.

**Multiprocessing:** final transcription runs in a child process over `core/safepipe.py`; `spawn`
is forced on Windows. Scripts need the `if __name__ == "__main__":` guard.

**Servers, in order of currency:**
- `RealtimeSTT_server/production_server.py` — packaged, versioned v1 HTTP/WebSocket server. The
  WebSocket path owns its own turn state machine and canonical PCM buffer; it does *not* derive
  finalization from recorder VAD. Non-loopback binds require a bearer token
  (`REALTIMESTT_SERVER_BEARER_TOKEN` only — never a CLI flag) plus TLS. See `PRODUCTION_SERVER.md`.
- `example_fastapi_server/` — shared multi-user backend plus the source-only browser UI. Its
  binary packet format in `protocol.py` is a stable service boundary.
- `RealtimeSTT_server/stt_server.py` + `audio_recorder_client.py` — legacy compatibility path;
  don't entangle its cleanup with recorder refactors.

## CPU streaming profile

The recommended CPU (Linux x86-64) setup pairs two engines rather than retranscribing a growing
buffer: `sherpa_onnx_nemotron` (streaming, new frames only) for live hypotheses and
`sherpa_onnx_parakeet` (offline, decodes the complete turn once) for the final. Wire it through
`transcription_engine` / `model` and `realtime_transcription_engine` / `realtime_model_type`.

Nemotron live text is **display-only**; Parakeet is the sole authoritative final. Do not feed live
hypotheses into anything that acts on transcripts. `type.py` at the repo root is the local
voice-to-keyboard script using this profile.

## Compatibility rules

`docs/api-compatibility.md` and `docs/module-map.md` are the contract. Preserve
`AudioToTextRecorder` constructor parameter names, defaults, **and order** (tests inspect signature
order), public method names/return values/blocking behavior, text-formatting defaults, observable
attributes (`is_recording`, `state`, language fields), engine factory aliases, dependency-error
message text, the FastAPI packet format, and the production server's v1 protocol. If an import path
moves, leave a re-export at the old path and add a compatibility test.

## Docs

`docs/` is user-facing and expected to stay in sync: `configuration.md` (every constructor
parameter), `installation.md`, `transcription-engines.md` + `engines/*.md`,
`custom-transcription-engines.md`, `testing.md`, `test-scripts.md`, `fastapi-server.md`,
`troubleshooting.md`. Extras in `setup.py` are also documented in its `INSTALL_GUIDE` string —
update both together.
