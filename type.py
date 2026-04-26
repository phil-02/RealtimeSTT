# Voice-to-keyboard with real-time transcription
# Start with: uv run python type2.py
# Requires: sudo apt-get install xdotool

if __name__ == '__main__':

    import subprocess
    from RealtimeSTT import AudioToTextRecorder

    def type_text(text):
        """Type text using xdotool (works in terminal apps, supports Unicode)."""
        subprocess.run(['xdotool', 'type', '--clearmodifiers', '--', text], check=False)

    def on_transcription_finished(text):
        """Called when recording stops with final transcription."""
        if text:
            type_text(text + "\n")

    recorder = AudioToTextRecorder(
        spinner=False,
        
        # === CPU SETTINGS ===
        device="cpu",
        compute_type="int8",
        
        # === MODEL ===
        model="small.en",
        
        # === SPEED OPTIMIZATIONS ===
        beam_size=1,
        batch_size=0,
        
        # === ALLOW 2s PAUSES BETWEEN SENTENCES ===
        post_speech_silence_duration=1.5,
        silero_sensitivity=0.3,

        # === EARLY TRANSCRIPTION (speeds up final result) ===
        early_transcription_on_silence=1.2,
        
        language="en",
    )

    try:
        while True:
            recorder.text(on_transcription_finished)
    except KeyboardInterrupt:
        print("Exiting application due to keyboard interrupt")
