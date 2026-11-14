"""
FFmpeg render server for the n8n "AI Fantasy YouTube Shorts Factory" workflow.

POST /render  -> accepts the JSON from the "Build Render Payload" node, returns an MP4.
GET  /health  -> {"ok": true}

Auth: send header  X-API-Key: <RENDER_API_KEY>
"""
import base64
import os
import shutil
import subprocess
import tempfile

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

API_KEY = os.environ.get("RENDER_API_KEY", "")
app = FastAPI(title="Shorts FFmpeg Render Server")


def run(cmd):
    """Run a command; raise HTTP 500 with the tail of stderr on failure."""
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise HTTPException(500, f"Command failed: {' '.join(cmd[:3])}... {r.stderr[-1500:]}")
    return r.stdout


def b64_to_file(b64, path):
    """Decode base64 (optionally a data: URL) to a file."""
    if not b64:
        raise HTTPException(400, f"Empty base64 data for {os.path.basename(path)}")
    if "," in b64[:100]:
        b64 = b64.split(",", 1)[1]
    with open(path, "wb") as f:
        f.write(base64.b64decode(b64))


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/render")
async def render(request: Request, x_api_key: str = Header(default="")):
    if API_KEY and x_api_key != API_KEY:
        raise HTTPException(401, "Invalid API key")

    job = await request.json()
    w = int(job.get("width", 1080))
    h = int(job.get("height", 1920))
    fps = int(job.get("fps", 30))
    scenes = sorted(job.get("scenes") or [], key=lambda s: s.get("index", 0))
    if not scenes:
        raise HTTPException(400, "No scenes in payload")
    narration = job.get("narration") or {}

    tmp = tempfile.mkdtemp()
    try:
        # 1. Narration audio -> duration
        narr = os.path.join(tmp, "narration.mp3")
        b64_to_file(narration.get("b64", ""), narr)
        total = float(run([
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "csv=p=0", narr,
        ]).strip())
        wsum = sum(max(float(s.get("weight", 1)), 1) for s in scenes)

        # 2. One motion clip per scene, length proportional to its weight
        clips = []
        for s in scenes:
            idx = s.get("index", len(clips))
            img = os.path.join(tmp, f"s{idx}.png")
            b64_to_file(s.get("image_b64", ""), img)
            frames = max(int(total * max(float(s.get("weight", 1)), 1) / wsum * fps), 1)
            motion = s.get("motion", "zoom_in")
            z = {
                "zoom_in": "min(1+0.0015*on,1.3)",
                "zoom_out": "max(1.3-0.0015*on,1)",
            }.get(motion, "1.2")
            x = {
                "pan_right": f"(iw-iw/zoom)*on/{frames}",
                "pan_left": f"(iw-iw/zoom)*(1-on/{frames})",
            }.get(motion, "iw/2-(iw/zoom/2)")
            vf = (
                f"scale={w*2}:{h*2}:force_original_aspect_ratio=increase,crop={w*2}:{h*2},"
                f"zoompan=z='{z}':x='{x}':y='ih/2-(ih/zoom/2)':d={frames}:s={w}x{h}:fps={fps},"
                f"format=yuv420p"
            )
            out = os.path.join(tmp, f"c{idx}.mp4")
            run([
                "ffmpeg", "-y", "-i", img, "-vf", vf, "-frames:v", str(frames),
                "-c:v", "libx264", "-preset", "veryfast", out,
            ])
            clips.append(out)

        # 3. Concatenate clips + add narration
        lst = os.path.join(tmp, "list.txt")
        with open(lst, "w") as f:
            f.writelines(f"file '{c}'\n" for c in clips)
        final = os.path.join(tmp, "final.mp4")
        run([
            "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", lst, "-i", narr,
            "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac",
            "-shortest", "-movflags", "+faststart", final,
        ])
    except Exception:
        shutil.rmtree(tmp, ignore_errors=True)
        raise

    return FileResponse(
        final,
        media_type="video/mp4",
        filename=f"{job.get('job_id', 'short')}.mp4",
        background=BackgroundTask(shutil.rmtree, tmp, ignore_errors=True),
    )
