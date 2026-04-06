# YouTube FFmpeg Server

A cloud FFmpeg server for YouTube Shorts automation.
Runs on Render.com free tier.

## What it does
- Takes 5 images + voiceover audio + script
- Animates images with Ken Burns effect (zoom in/out)
- Adds smooth fade transitions between images
- Mixes background music at 12% volume
- Burns captions (2-3 words at a time) onto video
- Returns final 1080x1920 MP4 (YouTube Shorts format)

## Deploy to Render.com

1. Push this repo to GitHub
2. Go to render.com → New → Web Service
3. Connect your GitHub repo
4. Render auto-detects render.yaml and deploys!
5. Free tier is enough to get started

## API Endpoints

### GET /health
Check if server is running.

### POST /assemble
Assemble video from images + audio.

**Request body (JSON):**
```json
{
  "images": [
    "https://url-to-image-1.jpg",
    "https://url-to-image-2.jpg",
    "https://url-to-image-3.jpg",
    "https://url-to-image-4.jpg",
    "https://url-to-image-5.jpg"
  ],
  "audio_url": "https://url-to-voiceover.mp3",
  "script": "Full script text here for captions",
  "job_id": "optional-unique-id"
}
```

**Returns:** MP4 video file (1080x1920, H.264)

### POST /upload-music
Upload background music file.

**Form data:** `file` = your music file (mp3/wav)

## Environment Variables
No env vars needed — FFmpeg is installed during build.

## Background Music
Upload your music via POST /upload-music endpoint
OR manually place file in /app/background_music/ folder.

Supported formats: mp3, wav, m4a, ogg
