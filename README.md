# आवाज़ — Awaaz AI

A multilingual Indian-language text-to-speech web app powered by [Sarvam AI](https://sarvam.ai).

## Features
- 11 Indian languages: Hindi, Bengali, Gujarati, Kannada, Malayalam, Marathi, Odia, Punjabi, Tamil, Telugu, English (IN)
- 12 voices per language (6 female, 6 male)
- Long-text support — auto-splits at sentence boundaries and stitches audio
- Batch export — generate multiple lines as individual WAV files, download as ZIP
- Pitch, pace, and loudness controls
- Roman → script transliteration toggle

## Stack
- **Backend**: Python 3.11, Flask 3.x, Gunicorn
- **Frontend**: Vanilla HTML/CSS/JS (served as Flask template)
- **TTS API**: Sarvam AI (`bulbul:v2` model)

## Setup
```bash
pip install -r voice-app/requirements.txt
SARVAM_API_KEY=your_key python voice-app/app.py
```
Then open http://localhost:5000

## Environment Variables
- `SARVAM_API_KEY` — your Sarvam AI API key (required)
- `PORT` — server port (default 5000)
