import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class AccuracyFirstTypingContractTests(unittest.TestCase):
    def test_recording_stop_publishes_period_before_final_transcription(self):
        script = r'''
from unittest.mock import patch
import type as app

sentence = "Sam, that sounds like rewarding work"
app.typed = sentence
outputs = []
captured = {}

class FakeRecorder:
    def __init__(self, **kwargs):
        captured.update(kwargs)

with patch.object(app, "type_text", outputs.append), patch.object(
    app, "press_backspace"
) as backspace, patch.object(app, "AudioToTextRecorder", FakeRecorder):
    app.build_recorder()
    assert captured["on_recording_stop"] is app.on_recording_stop
    app.on_recording_stop()
    assert outputs == ["."], outputs
    assert app.typed == sentence + ".", app.typed
    app.on_transcription_finished(sentence + ".")
    backspace.assert_not_called()
    assert outputs == [".", "\n"], outputs
'''
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=ROOT,
            text=True,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_last_stabilized_word_is_immediate_and_matching_final_is_not_rewritten(self):
        script = r'''
from types import SimpleNamespace
from unittest.mock import patch
import type as app

outputs = []
with patch.object(app, "type_text", outputs.append), patch.object(
    app, "press_backspace"
) as backspace:
    app.on_stabilized_delta(SimpleNamespace(
        should_publish=True,
        stable_delta="quick final word",
    ))
    assert outputs == ["Quick final word"], outputs
    app.on_transcription_finished("Quick final word.")
    backspace.assert_not_called()
    assert "".join(outputs) == "Quick final word.\n", outputs
'''
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=ROOT,
            text=True,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_final_replaces_only_a_differing_suffix(self):
        script = r'''
from unittest.mock import patch
import type as app

original = "The quick fox"
final = "The quick dog."
app.typed = original
document = list(original)
cursor = [len(document)]

def type_at_cursor(text):
    document[cursor[0]:cursor[0]] = list(text)
    cursor[0] += len(text)

def backspace(count):
    del document[cursor[0] - count:cursor[0]]
    cursor[0] -= count

def left(count):
    cursor[0] -= count

def right(count):
    cursor[0] += count

with patch.object(app, "type_text", type_at_cursor), patch.object(
    app, "press_backspace", backspace
), patch.object(app, "move_cursor_left", left), patch.object(
    app, "move_cursor_right", right
):
    app.on_transcription_finished("The quick dog.")

assert "".join(document) == final + "\n", "".join(document)
'''
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=ROOT,
            text=True,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_final_applies_local_edits_when_the_first_word_differs(self):
        script = r'''
from unittest.mock import patch
import type as app

original = "Being comparaly modern"
final = "In being comparatively modern."
app.typed = original
document = list(original)
cursor = [len(document)]
erasures = []

def type_at_cursor(text):
    document[cursor[0]:cursor[0]] = list(text)
    cursor[0] += len(text)

def backspace(count):
    erasures.append(count)
    del document[cursor[0] - count:cursor[0]]
    cursor[0] -= count

def left(count):
    cursor[0] -= count

def right(count):
    cursor[0] += count

with patch.object(app, "type_text", type_at_cursor), patch.object(
    app, "press_backspace", backspace
), patch.object(app, "move_cursor_left", left), patch.object(
    app, "move_cursor_right", right
):
    app.on_transcription_finished(final)

assert "".join(document) == final + "\n", "".join(document)
assert erasures and max(erasures) < len(original), erasures
'''
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=ROOT,
            text=True,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
