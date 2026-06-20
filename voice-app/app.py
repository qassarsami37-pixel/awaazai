import os
import io
import re
import wave
import base64
import json
import requests
from flask import Flask, render_template, request, jsonify

app = Flask(__name__)

SARVAM_API_KEY = os.environ.get("SARVAM_API_KEY", "")
SARVAM_TTS_URL = "https://api.sarvam.ai/text-to-speech"
SARVAM_TRANSLITERATE_URL = "https://api.sarvam.ai/transliterate"
SARVAM_MODEL = "bulbul:v2"

# All speakers below are confirmed valid for bulbul:v2 across all supported languages.
_FEMALE = [
    {"id": "anushka", "name": "Anushka (Female)"},
    {"id": "manisha", "name": "Manisha (Female)"},
    {"id": "vidya",   "name": "Vidya (Female)"},
    {"id": "priya",   "name": "Priya (Female)"},
    {"id": "neha",    "name": "Neha (Female)"},
    {"id": "shruti",  "name": "Shruti (Female)"},
]
_MALE = [
    {"id": "abhilash", "name": "Abhilash (Male)"},
    {"id": "aditya",   "name": "Aditya (Male)"},
    {"id": "rahul",    "name": "Rahul (Male)"},
    {"id": "rohan",    "name": "Rohan (Male)"},
    {"id": "amit",     "name": "Amit (Male)"},
    {"id": "kabir",    "name": "Kabir (Male)"},
]
_ALL_SPEAKERS = _FEMALE + _MALE

SPEAKERS = {
    "hi-IN": _ALL_SPEAKERS,
    "bn-IN": _ALL_SPEAKERS,
    "gu-IN": _ALL_SPEAKERS,
    "kn-IN": _ALL_SPEAKERS,
    "ml-IN": _ALL_SPEAKERS,
    "mr-IN": _ALL_SPEAKERS,
    "od-IN": _ALL_SPEAKERS,
    "pa-IN": _ALL_SPEAKERS,
    "ta-IN": _ALL_SPEAKERS,
    "te-IN": _ALL_SPEAKERS,
    "en-IN": _ALL_SPEAKERS,
}

# Valid language codes for bulbul:v2
VALID_LANGUAGES = set(SPEAKERS.keys())

MAX_TEXT_LEN = 5000  # ~10 segments of 500 chars each


def split_text(text, max_len=490):
    """Split text at natural sentence boundaries so each chunk is ≤ max_len chars."""
    text = text.strip()
    if len(text) <= max_len:
        return [text]

    segments = []
    current = ""

    # Split on sentence-ending punctuation (keep the delimiter attached)
    parts = re.split(r'(?<=[।.!?;])\s*', text)

    for part in parts:
        if not part:
            continue
        if len(current) + len(part) <= max_len:
            current += part
        else:
            if current:
                segments.append(current.strip())
            if len(part) > max_len:
                # Force-split at the last comma before max_len, or hard-cut
                while len(part) > max_len:
                    cut = part.rfind(",", 0, max_len)
                    if cut == -1 or cut < max_len // 3:
                        cut = max_len
                    segments.append(part[:cut].strip())
                    part = part[cut:].lstrip(", ")
                current = part
            else:
                current = part

    if current.strip():
        segments.append(current.strip())

    return [s for s in segments if s]


def combine_wav_b64(b64_list, silence_ms=250, sample_rate=22050):
    """Stitch a list of base64-encoded WAV strings into one, with brief silence between."""
    if len(b64_list) == 1:
        return b64_list[0]

    # 16-bit mono silence = 2 bytes per sample
    silence = b"\x00" * int(sample_rate * silence_ms / 1000) * 2
    all_frames = []
    params = None

    for i, b64 in enumerate(b64_list):
        raw = base64.b64decode(b64)
        with wave.open(io.BytesIO(raw)) as w:
            if params is None:
                params = w.getparams()
            all_frames.append(w.readframes(w.getnframes()))
        if i < len(b64_list) - 1:
            all_frames.append(silence)

    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setparams(params)
        for frames in all_frames:
            w.writeframes(frames)
    out.seek(0)
    return base64.b64encode(out.read()).decode("utf-8")


@app.route("/")
def index():
    return render_template("index.html", speakers_json=SPEAKERS)


@app.route("/api/speakers/<language>")
def get_speakers(language):
    speakers = SPEAKERS.get(language, SPEAKERS["hi-IN"])
    return jsonify(speakers)


@app.route("/api/tts", methods=["POST"])
def text_to_speech():
    data = request.get_json()
    if not data:
        return jsonify({"error": "Invalid JSON body"}), 400

    text = data.get("text", "").strip()
    language = data.get("language", "hi-IN")
    speaker = data.get("speaker", "anushka")
    # Clamp all numeric params to the ranges accepted by bulbul:v2
    pitch    = max(-20, min(20,  int(round(float(data.get("pitch",    0))))))
    pace     = max(0.5, min(2.0, float(data.get("pace",     1.0))))
    loudness = max(0.1, min(3.0, float(data.get("loudness", 1.5))))

    if not text:
        return jsonify({"error": "Text is required"}), 400

    if len(text) > MAX_TEXT_LEN:
        return jsonify({"error": f"Text must be {MAX_TEXT_LEN:,} characters or fewer."}), 400

    if not SARVAM_API_KEY:
        return jsonify({"error": "SARVAM_API_KEY is not configured"}), 500

    headers = {
        "api-subscription-key": SARVAM_API_KEY,
        "Content-Type": "application/json",
    }

    if language not in VALID_LANGUAGES:
        return jsonify({"error": f"Language '{language}' is not supported. Use one of: {', '.join(sorted(VALID_LANGUAGES))}"}), 400

    valid_speaker_ids = {s["id"] for s in SPEAKERS.get(language, _ALL_SPEAKERS)}
    if speaker not in valid_speaker_ids:
        speaker = "anushka"  # safe default

    segments = split_text(text)
    collected = []

    for seg in segments:
        payload = {
            "inputs": [seg],
            "target_language_code": language,
            "speaker": speaker,
            "pitch": pitch,      # already int, clamped -20..20
            "pace": pace,        # float, clamped 0.5..2.0
            "loudness": loudness, # float, clamped 0.1..3.0
            "speech_sample_rate": 22050,
            "enable_preprocessing": True,
            "model": SARVAM_MODEL,
        }
        try:
            response = requests.post(SARVAM_TTS_URL, json=payload, headers=headers, timeout=30)
        except requests.exceptions.Timeout:
            return jsonify({"error": "Request to Sarvam AI timed out. Try again."}), 504
        except requests.exceptions.RequestException as e:
            return jsonify({"error": f"Network error: {str(e)}"}), 502

        if response.status_code != 200:
            try:
                err = response.json()
            except Exception:
                err = {"message": response.text}
            return jsonify({"error": err.get("message", f"Sarvam API error {response.status_code}")}), response.status_code

        result = response.json()
        audios = result.get("audios", [])
        if not audios:
            return jsonify({"error": "No audio returned from Sarvam API"}), 500
        collected.append(audios[0])

    combined = combine_wav_b64(collected)
    return jsonify({"audio": combined, "segments": len(segments)})


@app.route("/api/transliterate", methods=["POST"])
def transliterate():
    data = request.get_json()
    if not data:
        return jsonify({"error": "Invalid JSON body"}), 400

    text = data.get("text", "").strip()
    target_language = data.get("language", "hi-IN")

    if not text:
        return jsonify({"transliterated": ""}), 200

    if not SARVAM_API_KEY:
        return jsonify({"error": "SARVAM_API_KEY is not configured"}), 500

    headers = {
        "api-subscription-key": SARVAM_API_KEY,
        "Content-Type": "application/json",
    }

    payload = {
        "input": text,
        "source_language_code": "en-Latn",
        "target_language_code": target_language,
        "speaker_gender": "Female",
        "mode": "classic-colloquial",
        "enable_preprocessing": False,
        "numerals_format": "international",
    }

    try:
        response = requests.post(
            SARVAM_TRANSLITERATE_URL, json=payload, headers=headers, timeout=15
        )
    except requests.exceptions.Timeout:
        return jsonify({"error": "Transliteration timed out. Try again."}), 504
    except requests.exceptions.RequestException as e:
        return jsonify({"error": f"Network error: {str(e)}"}), 502

    if response.status_code != 200:
        try:
            err = response.json()
        except Exception:
            err = {"message": response.text}
        return jsonify({"error": err.get("message", f"Sarvam API error {response.status_code}")}), response.status_code

    result = response.json()
    return jsonify({"transliterated": result.get("transliterated_text", text)})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
