from flask import Flask, request, jsonify, send_file
import subprocess
import os
import uuid
import json
import tempfile
import requests
import math
from pathlib import Path

app = Flask(__name__)

TEMP_DIR = Path("/tmp/videos")
TEMP_DIR.mkdir(exist_ok=True)

BACKGROUND_MUSIC_PATH = Path("/tmp/background_music")
BACKGROUND_MUSIC_PATH.mkdir(exist_ok=True)


def download_file(url, dest_path):
    """Download a file from URL to local path"""
    response = requests.get(url, stream=True, timeout=60)
    response.raise_for_status()
    with open(dest_path, 'wb') as f:
        for chunk in response.iter_content(chunk_size=8192):
            f.write(chunk)
    return dest_path


def get_audio_duration(audio_path):
    """Get duration of audio file in seconds"""
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "json", str(audio_path)
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    data = json.loads(result.stdout)
    return float(data["format"]["duration"])


def get_background_music():
    """Find first audio file in background_music folder"""
    for ext in ["*.mp3", "*.wav", "*.m4a", "*.ogg"]:
        files = list(BACKGROUND_MUSIC_PATH.glob(ext))
        if files:
            return files[0]
    return None


def build_video_filter(num_images, audio_duration, width=1080, height=1920):
    """
    Build FFmpeg filtergraph:
    - Ken Burns zoom in/out alternating on each image
    - Fade transitions between images
    - Captions burned in
    """
    duration_per_image = audio_duration / num_images
    fade_duration = min(0.5, duration_per_image * 0.2)

    filter_parts = []
    zoompan_parts = []

    for i in range(num_images):
        # Alternate zoom in / zoom out
        if i % 2 == 0:
            # Zoom IN: start at 1.0, end at 1.15
            zoom_expr = f"'min(1.15,1.0+{0.15/25}/1*on)'"
            x_expr = f"'iw/2-(iw/zoom/2)'"
            y_expr = f"'ih/2-(ih/zoom/2)'"
        else:
            # Zoom OUT: start at 1.15, end at 1.0
            zoom_expr = f"'max(1.0,1.15-{0.15/25}/1*on)'"
            x_expr = f"'iw/2-(iw/zoom/2)'"
            y_expr = f"'ih/2-(ih/zoom/2)'"

        frames = int(duration_per_image * 25)  # 25fps

        zoompan = (
            f"[{i}:v]"
            f"scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},"
            f"zoompan=z={zoom_expr}:x={x_expr}:y={y_expr}"
            f":d={frames}:s={width}x{height}:fps=25,"
            f"setpts=PTS-STARTPTS,"
            f"format=yuva420p"
            f"[v{i}]"
        )
        zoompan_parts.append(zoompan)
        filter_parts.append(f"[v{i}]")

    # Cross-fade transitions between clips
    xfade_parts = []
    current_label = "v0"
    offset = duration_per_image - fade_duration

    for i in range(1, num_images):
        next_label = f"v{i}"
        out_label = f"xf{i}" if i < num_images - 1 else "vout"
        xfade = (
            f"[{current_label}][{next_label}]"
            f"xfade=transition=fade:duration={fade_duration:.2f}"
            f":offset={offset:.2f}[{out_label}]"
        )
        xfade_parts.append(xfade)
        current_label = out_label
        offset += duration_per_image - fade_duration

    all_filters = zoompan_parts + xfade_parts
    return ";".join(all_filters)


def create_subtitle_file(script_text, audio_duration, output_path):
    """
    Create SRT subtitle file from script
    2-3 words at a time, evenly distributed across audio duration
    """
    words = script_text.strip().split()
    chunks = []

    # Group into 2-3 word chunks
    i = 0
    while i < len(words):
        chunk_size = 2 if i % 3 == 0 else 3
        chunk = " ".join(words[i:i+chunk_size])
        chunks.append(chunk)
        i += chunk_size

    time_per_chunk = audio_duration / len(chunks) if chunks else 1.0

    def format_time(seconds):
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = seconds % 60
        return f"{h:02d}:{m:02d}:{s:06.3f}".replace(".", ",")

    srt_content = ""
    for idx, chunk in enumerate(chunks):
        start = idx * time_per_chunk
        end = start + time_per_chunk - 0.05
        srt_content += f"{idx+1}\n"
        srt_content += f"{format_time(start)} --> {format_time(end)}\n"
        srt_content += f"{chunk}\n\n"

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(srt_content)

    return output_path


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "message": "FFmpeg server running!"})


@app.route("/assemble", methods=["POST"])
def assemble_video():
    """
    Expects JSON body:
    {
        "images": ["url1", "url2", "url3", "url4", "url5"],  // URLs or base64
        "audio_url": "url_to_voiceover",
        "script": "full script text for captions",
        "job_id": "optional unique id"
    }
    Returns: video file (MP4)
    """
    data = request.get_json()

    if not data:
        return jsonify({"error": "No JSON body provided"}), 400

    images = data.get("images", [])
    audio_url = data.get("audio_url")
    script = data.get("script", "")
    job_id = data.get("job_id", str(uuid.uuid4()))

    if len(images) != 5:
        return jsonify({"error": "Exactly 5 images required"}), 400
    if not audio_url:
        return jsonify({"error": "audio_url is required"}), 400
    if not script:
        return jsonify({"error": "script is required for captions"}), 400

    # Working directory for this job
    job_dir = TEMP_DIR / job_id
    job_dir.mkdir(exist_ok=True)

    try:
        # 1. Download all images
        image_paths = []
        for i, img_url in enumerate(images):
            img_path = job_dir / f"image_{i}.jpg"
            download_file(img_url, img_path)
            image_paths.append(img_path)

        # 2. Download voiceover audio
        audio_path = job_dir / "voiceover.mp3"
        download_file(audio_url, audio_path)

        # 3. Get audio duration
        audio_duration = get_audio_duration(audio_path)

        # 4. Create subtitle file
        srt_path = job_dir / "captions.srt"
        create_subtitle_file(script, audio_duration, srt_path)

        # 5. Find background music
        bg_music = get_background_music()

        # 6. Build intermediate video (images + ken burns + transitions)
        raw_video_path = job_dir / "raw_video.mp4"

        # Build input args for ffmpeg
        input_args = []
        for img_path in image_paths:
            input_args += ["-loop", "1", "-t", str(audio_duration / 5 + 1), "-i", str(img_path)]

        filter_graph = build_video_filter(5, audio_duration)

        cmd_video = (
            ["ffmpeg", "-y"]
            + input_args
            + [
                "-filter_complex", filter_graph,
                "-map", "[vout]",
                "-t", str(audio_duration),
                "-c:v", "libx264",
                "-preset", "fast",
                "-crf", "18",
                "-pix_fmt", "yuv420p",
                "-r", "25",
                str(raw_video_path)
            ]
        )

        result = subprocess.run(cmd_video, capture_output=True, text=True)
        if result.returncode != 0:
            return jsonify({
                "error": "Video assembly failed",
                "details": result.stderr[-2000:]
            }), 500

        # 7. Add audio (voiceover + background music) + captions
        output_path = job_dir / "final_video.mp4"

        # Subtitle filter - bold white text, lower-middle position
        subtitle_filter = (
            f"subtitles={srt_path}"
            f":force_style='FontName=Arial Black,"
            f"FontSize=18,"
            f"PrimaryColour=&HFFFFFF&,"
            f"OutlineColour=&H000000&,"
            f"Outline=3,"
            f"Shadow=2,"
            f"Bold=1,"
            f"Alignment=2,"
            f"MarginV=320'"
        )

        if bg_music:
            # Mix voiceover + background music at 12% volume
            cmd_final = [
                "ffmpeg", "-y",
                "-i", str(raw_video_path),
                "-i", str(audio_path),
                "-stream_loop", "-1", "-i", str(bg_music),
                "-filter_complex",
                f"[1:a]volume=1.0[voice];"
                f"[2:a]volume=0.12,atrim=0:{audio_duration},asetpts=PTS-STARTPTS[bgm];"
                f"[voice][bgm]amix=inputs=2:duration=first[aout];"
                f"[0:v]{subtitle_filter}[vfinal]",
                "-map", "[vfinal]",
                "-map", "[aout]",
                "-c:v", "libx264",
                "-preset", "fast",
                "-crf", "18",
                "-c:a", "aac",
                "-b:a", "192k",
                "-t", str(audio_duration),
                "-movflags", "+faststart",
                str(output_path)
            ]
        else:
            # No background music — just voiceover + captions
            cmd_final = [
                "ffmpeg", "-y",
                "-i", str(raw_video_path),
                "-i", str(audio_path),
                "-filter_complex",
                f"[1:a]volume=1.0[aout];"
                f"[0:v]{subtitle_filter}[vfinal]",
                "-map", "[vfinal]",
                "-map", "[aout]",
                "-c:v", "libx264",
                "-preset", "fast",
                "-crf", "18",
                "-c:a", "aac",
                "-b:a", "192k",
                "-t", str(audio_duration),
                "-movflags", "+faststart",
                str(output_path)
            ]

        result = subprocess.run(cmd_final, capture_output=True, text=True)
        if result.returncode != 0:
            return jsonify({
                "error": "Final video assembly failed",
                "details": result.stderr[-2000:]
            }), 500

        # 8. Return the video file
        return send_file(
            str(output_path),
            mimetype="video/mp4",
            as_attachment=True,
            download_name=f"video_{job_id}.mp4"
        )

    except Exception as e:
        return jsonify({"error": str(e)}), 500

    finally:
        # Cleanup job directory after sending
        import shutil
        try:
            shutil.rmtree(job_dir, ignore_errors=True)
        except:
            pass


@app.route("/upload-music", methods=["POST"])
def upload_music():
    """Upload background music file"""
    if "file" not in request.files:
        return jsonify({"error": "No file provided"}), 400

    file = request.files["file"]
    if file.filename == "":
        return jsonify({"error": "No file selected"}), 400

    # Save to background_music folder
    dest = BACKGROUND_MUSIC_PATH / "background.mp3"
    file.save(str(dest))

    return jsonify({
        "success": True,
        "message": "Background music uploaded successfully!",
        "path": str(dest)
    })


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
