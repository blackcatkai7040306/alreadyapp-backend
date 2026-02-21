# ALREADY Backend

FastAPI backend for **ALREADY** — voice cloning and stories (ElevenLabs).

## Setup

1. **Create a virtual environment** (recommended):
   ```bash
   python -m venv .venv
   .venv\Scripts\activate   # Windows
   # source .venv/bin/activate  # macOS/Linux
   ```

2. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

3. **Configure ElevenLabs**:
   - Copy `.env.example` to `.env`
   - Set `ELEVENLABS_API_KEY` to your [ElevenLabs](https://elevenlabs.io) API key

## Run

```bash
uvicorn app.main:app --reload
```

- API: http://127.0.0.1:8000  
- Docs: http://127.0.0.1:8000/docs  

## Voice cloning

**POST** `/api/voice/clone`

Creates an instant voice clone from uploaded audio using ElevenLabs.

- **Form fields**: `name` (required), `remove_background_noise` (optional, default `false`)
- **Files**: one or more audio files (e.g. MP3, WAV, M4A)

**Example (curl)**:
```bash
curl -X POST "http://127.0.0.1:8000/api/voice/clone" \
  -F "name=My Voice" \
  -F "files=@sample.mp3"
```

**Response**:
```json
{
  "voice_id": "c38kUX8pkfYO2kHyqfFy",
  "requires_verification": false
}
```

Use `voice_id` for text-to-speech or store it per user for “Re-record My Voice” in the Profile screen.
