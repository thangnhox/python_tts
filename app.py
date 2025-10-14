import asyncio, edge_tts, tempfile, os, re, hashlib, json, time
from flask import Flask, request, jsonify, send_file, render_template
from pydub import AudioSegment

app = Flask(__name__)

CACHE_DIR = os.path.join(tempfile.gettempdir(), "tts_cache")
os.makedirs(CACHE_DIR, exist_ok=True)

progress_state = {}   # { job_id: {"done": int, "total": int, "status": "text"} }

# -----------------------------
# Utility helpers
# -----------------------------

def cache_key(text, voice):
    """Unique cache key per text+voice pair."""
    h = hashlib.sha1(f"{voice}|{text}".encode("utf-8")).hexdigest()
    return os.path.join(CACHE_DIR, f"{h}.mp3")

def parse_tags_into_segments(text, default_voice):
    """Parse [voice ...]: and [pause n] into segment list."""
    regex = re.compile(
        r'(\[voice\s+([^\]]+)\]\s*:)|(\[pause\s*(\d+(?:\.\d+)?)\s*(?:s|sec|seconds)?\])',
        re.I
    )
    segments = []
    last = 0
    for m in regex.finditer(text):
        if m.start() > last:
            before = text[last:m.start()].strip()
            if before:
                segments.append({'type': 'text', 'content': before})
        if m.group(1):
            segments.append({'type': 'voice', 'name': m.group(2).strip()})
        elif m.group(3):
            segments.append({'type': 'pause', 'duration': float(m.group(4))})
        last = m.end()
    tail = text[last:].strip()
    if tail:
        segments.append({'type': 'text', 'content': tail})
    return segments

async def edge_tts_save(text, voice, path):
    """Use edge-tts to synthesize text to path."""
    communicate = edge_tts.Communicate(text, voice)
    await communicate.save(path)

def synthesize_segments_to_mp3(segments, default_voice, job_id=None):
    """Synthesize parsed segments sequentially with caching + progress."""
    merged = AudioSegment.silent(duration=0)
    current_voice = default_voice
    total = len(segments)
    done = 0

    if job_id:
        progress_state[job_id] = {"done": 0, "total": total, "status": "Starting"}

    for seg in segments:
        if job_id:
            progress_state[job_id]["done"] = done
            progress_state[job_id]["status"] = f"Processing segment {done+1}/{total}"

        if seg["type"] == "voice":
            current_voice = seg["name"]
            done += 1
            continue

        elif seg["type"] == "pause":
            merged += AudioSegment.silent(duration=int(seg["duration"] * 1000))
            done += 1
            continue

        elif seg["type"] == "text":
            key_path = cache_key(seg["content"], current_voice)
            if not os.path.exists(key_path):
                try:
                    asyncio.run(edge_tts_save(seg["content"], current_voice, key_path))
                except Exception as e:
                    print(f"TTS error: {e}")
                    merged += AudioSegment.silent(duration=500)
                    done += 1
                    continue

            try:
                part = AudioSegment.from_file(key_path, format="mp3")
                merged += part
            except Exception as e:
                print(f"Cache read error: {e}")
                merged += AudioSegment.silent(duration=500)

            done += 1

    final_path = tempfile.mktemp(suffix=".mp3")
    merged.export(final_path, format="mp3")

    if job_id:
        progress_state[job_id] = {"done": total, "total": total, "status": "Complete"}

    return final_path

# -----------------------------
# Routes
# -----------------------------

@app.route("/voices")
def voices():
    """Return list of available Edge-TTS voices."""
    voices = []
    try:
        result = asyncio.run(edge_tts.list_voices())
        voices = [v["ShortName"] for v in result]
    except Exception as e:
        print("Voice list error:", e)
    return jsonify(voices)

@app.route("/tts", methods=["POST"])
def tts_route():
    data = request.get_json(force=True)
    text = data.get("text", "")
    voice = data.get("voice", "en-US-AriaNeural")
    if not text.strip():
        return jsonify({"error": "Missing text"}), 400

    segments = parse_tags_into_segments(text, voice)
    out_path = synthesize_segments_to_mp3(segments, voice)
    return send_file(out_path, mimetype="audio/mpeg", as_attachment=True, download_name=f"{voice}.mp3")

@app.route("/tts-all", methods=["POST"])
def tts_all_route():
    data = request.get_json(force=True)
    blocks = data.get("blocks", [])
    if not blocks:
        return jsonify({"error": "No blocks provided"}), 400

    job_id = str(int(time.time() * 1000))
    progress_state[job_id] = {"done": 0, "total": 1, "status": "Queued"}

    merged = AudioSegment.silent(duration=0)
    block_total = len(blocks)
    block_done = 0

    for block in blocks:
        text = block.get("text", "")
        voice = block.get("voice", "en-US-AriaNeural")
        segs = parse_tags_into_segments(text, voice)
        progress_state[job_id] = {
            "done": block_done,
            "total": block_total,
            "status": f"Synthesizing block {block_done+1}/{block_total}",
        }

        path = synthesize_segments_to_mp3(segs, voice, job_id=job_id)
        part = AudioSegment.from_file(path, format="mp3")
        merged += part + AudioSegment.silent(duration=500)
        os.remove(path)
        block_done += 1

    final_path = tempfile.mktemp(suffix=".mp3")
    merged.export(final_path, format="mp3")
    progress_state[job_id] = {"done": block_total, "total": block_total, "status": "Complete"}
    return jsonify({"job_id": job_id, "download": f"/download/{os.path.basename(final_path)}"})

@app.route("/download/<fname>")
def download(fname):
    path = os.path.join(tempfile.gettempdir(), fname)
    if not os.path.exists(path):
        return jsonify({"error": "file not found"}), 404
    return send_file(path, mimetype="audio/mpeg", as_attachment=True, download_name=fname)

@app.route("/progress/<job_id>")
def progress(job_id):
    """Return JSON progress for given job_id."""
    info = progress_state.get(job_id)
    if not info:
        return jsonify({"error": "no such job"}), 404
    pct = (info["done"] / max(info["total"], 1)) * 100
    return jsonify({"progress": round(pct, 1), "status": info["status"]})

@app.route("/")
def index():
    return render_template("index.html")

if __name__ == "__main__":
    app.run(debug=True, host='0.0.0.0', port=5000)
