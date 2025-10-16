import os, re, time, asyncio, tempfile, hashlib, sqlite3, threading, json
from flask import Flask, request, jsonify, send_file, render_template
from pydub import AudioSegment
import edge_tts

# -----------------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------------
app = Flask(__name__)

CACHE_DIR = os.path.join(tempfile.gettempdir(), "tts_cache")
os.makedirs(CACHE_DIR, exist_ok=True)
DB_PATH = os.path.join(CACHE_DIR, "cache.db")

progress_state = {}  # { job_id: {"done":int,"total":int,"status":str} }

# -----------------------------------------------------------------------------
# SQLite cache index
# -----------------------------------------------------------------------------
def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS cache (
            hash TEXT PRIMARY KEY,
            voice TEXT,
            text TEXT,
            path TEXT,
            size INTEGER,
            mtime REAL
        )
    """)
    conn.commit()
    conn.close()

def db_add_cache(hashv, voice, text, path):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "INSERT OR REPLACE INTO cache(hash,voice,text,path,size,mtime) VALUES(?,?,?,?,?,?)",
        (hashv, voice, text, path, os.path.getsize(path), time.time()),
    )
    conn.commit()
    conn.close()

def db_touch(hashv):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("UPDATE cache SET mtime=? WHERE hash=?", (time.time(), hashv))
    conn.commit()
    conn.close()

def db_cleanup(max_age_hours=6, max_total_mb=500):
    """Remove cache files older than X hours or when total size exceeds limit."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT hash,path,size,mtime FROM cache")
    rows = cur.fetchall()
    conn.close()

    now = time.time()
    total_size = sum(r[2] for r in rows)
    rows.sort(key=lambda r: r[3])  # oldest first

    for hashv, path, size, mtime in rows:
        if (now - mtime) > max_age_hours * 3600 or total_size > max_total_mb * 1e6:
            try:
                if os.path.exists(path):
                    os.remove(path)
                    total_size -= size
                    print(f"[cache] removed {path}")
            except Exception as e:
                print("cache cleanup error:", e)
            conn = sqlite3.connect(DB_PATH)
            conn.execute("DELETE FROM cache WHERE hash=?", (hashv,))
            conn.commit()
            conn.close()

# -----------------------------------------------------------------------------
# Cleanup helpers
# -----------------------------------------------------------------------------
def schedule_delete(path, delay=10):
    """Delete file after a short delay to avoid send_file race."""
    def _delete():
        try:
            if os.path.exists(path):
                os.remove(path)
                print(f"[cleanup] deleted {path}")
        except Exception as e:
            print(f"[cleanup error] {e}")
    threading.Timer(delay, _delete).start()

# -----------------------------------------------------------------------------
# Voice listing
# -----------------------------------------------------------------------------
@app.route("/voices")
def voices():
    """Return available Edge-TTS voice short names."""
    try:
        result = asyncio.run(edge_tts.list_voices())
        names = sorted({v["ShortName"] for v in result})
    except Exception as e:
        print("voice list error:", e)
        names = ["en-US-AriaNeural", "en-US-GuyNeural"]
    return jsonify(names)

# -----------------------------------------------------------------------------
# TTS helpers
# -----------------------------------------------------------------------------
def cache_key(text, voice):
    return hashlib.sha1(f"{voice}|{text}".encode("utf-8")).hexdigest()

async def edge_tts_save(text, voice, path):
    com = edge_tts.Communicate(text, voice)
    await com.save(path)

def parse_tags_into_segments(text, default_voice):
    """Parse text for [voice id]: and [pause n]."""
    regex = re.compile(
        r'\[voice\s+([^\]]+)\]\s*:\s*([^\[]*)|\[pause\s*(\d+(?:\.\d+)?)\s*(?:s|sec|seconds)?\]',
        re.I,
    )
    segments, last = [], 0
    for m in regex.finditer(text):
        if m.start() > last:
            before = text[last:m.start()].strip()
            if before:
                segments.append({"type": "text", "content": before})
        if m.group(1):
            voice_name = m.group(1).strip()
            inline_text = (m.group(2) or "").strip()
            segments.append({"type": "voice", "name": voice_name})
            if inline_text:
                segments.append({"type": "text", "content": inline_text})
        elif m.group(3):
            segments.append({"type": "pause", "duration": float(m.group(3))})
        last = m.end()
    tail = text[last:].strip()
    if tail:
        segments.append({"type": "text", "content": tail})
    return segments

def synthesize_segments_to_mp3(segments, default_voice, job_id=None):
    merged = AudioSegment.silent(duration=0)
    current_voice = default_voice
    total = len(segments)
    done = 0
    if job_id:
        progress_state[job_id] = {"done": 0, "total": total, "status": "Starting"}

    for seg in segments:
        if job_id:
            progress_state[job_id].update(done=done, status=f"Segment {done+1}/{total}")

        if seg["type"] == "voice":
            current_voice = seg["name"]
            done += 1
            continue
        elif seg["type"] == "pause":
            merged += AudioSegment.silent(duration=int(seg["duration"] * 1000))
            done += 1
            continue
        elif seg["type"] == "text":
            text = seg["content"].strip()
            if not text:
                done += 1
                continue

            key = cache_key(text, current_voice)
            cached_path = os.path.join(CACHE_DIR, key + ".mp3")

            if os.path.exists(cached_path):
                db_touch(key)
            else:
                try:
                    asyncio.run(edge_tts_save(text, current_voice, cached_path))
                    db_add_cache(key, current_voice, text, cached_path)
                except Exception as e:
                    print("edge-tts error:", e)
                    merged += AudioSegment.silent(duration=500)
                    done += 1
                    continue

            try:
                part = AudioSegment.from_file(cached_path, format="mp3")
                merged += part
            except Exception as e:
                print("pydub error:", e)
                merged += AudioSegment.silent(duration=500)

            done += 1

    final_path = tempfile.mktemp(suffix=".mp3")
    merged.export(final_path, format="mp3")
    if job_id:
        progress_state[job_id] = {"done": total, "total": total, "status": "Complete"}
    return final_path

# -----------------------------------------------------------------------------
# Routes
# -----------------------------------------------------------------------------
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/tts", methods=["POST"])
def tts_single():
    data = request.get_json(force=True)
    text = data.get("text", "")
    voice = data.get("voice", "en-US-AriaNeural")
    if not text.strip():
        return jsonify({"error": "Missing text"}), 400

    segs = parse_tags_into_segments(text, voice)
    out_path = synthesize_segments_to_mp3(segs, voice)
    resp = send_file(out_path, mimetype="audio/mpeg", as_attachment=True,
                     download_name=f"{voice}.mp3")
    schedule_delete(out_path)
    db_cleanup()
    return resp

@app.route("/tts-all", methods=["POST"])
def tts_all():
    data = request.get_json(force=True)
    blocks = data.get("blocks", [])
    if not blocks:
        return jsonify({"error": "No blocks"}), 400

    job_id = str(int(time.time() * 1000))
    progress_state[job_id] = {"done": 0, "total": len(blocks), "status": "Queued"}

    merged = AudioSegment.silent(duration=0)
    for i, b in enumerate(blocks):
        text = b.get("text", "")
        voice = b.get("voice", "en-US-AriaNeural")
        segs = parse_tags_into_segments(text, voice)
        progress_state[job_id].update(done=i, status=f"Block {i+1}/{len(blocks)}")
        block_path = synthesize_segments_to_mp3(segs, voice, job_id)
        merged += AudioSegment.from_file(block_path, format="mp3")
        merged += AudioSegment.silent(duration=500)
        schedule_delete(block_path, delay=5)

    final_path = tempfile.mktemp(suffix=".mp3")
    merged.export(final_path, format="mp3")
    progress_state[job_id] = {"done": len(blocks), "total": len(blocks), "status": "Complete"}
    db_cleanup()
    schedule_delete(final_path, delay=600)
    return jsonify({"job_id": job_id, "download": f"/download/{os.path.basename(final_path)}"})

@app.route("/download/<fname>")
def download(fname):
    path = os.path.join(tempfile.gettempdir(), fname)
    if not os.path.exists(path):
        return jsonify({"error": "not found"}), 404
    resp = send_file(path, mimetype="audio/mpeg", as_attachment=True, download_name=fname)
    schedule_delete(path)
    return resp

@app.route("/progress/<job_id>")
def progress(job_id):
    info = progress_state.get(job_id)
    if not info:
        return jsonify({"error": "no such job"}), 404
    pct = round(100 * info["done"] / max(1, info["total"]), 1)
    return jsonify({"progress": pct, "status": info["status"]})

# -----------------------------------------------------------------------------
# Startup
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Run TTS Flask server")
    parser.add_argument("--host", default=os.environ.get("TTS_HOST", "0.0.0.0"),
                        help="Host to bind (env: TTS_HOST)")
    parser.add_argument("--port", type=int, default=int(os.environ.get("TTS_PORT", 8000)),
                        help="Port to bind (env: TTS_PORT)")
    parser.add_argument("--debug", action="store_true",
                        default=os.environ.get("TTS_DEBUG", "") in ("1", "true", "True"),
                        help="Enable Flask debug mode (env: TTS_DEBUG=1)")
    args = parser.parse_args()

    init_db()
    db_cleanup()
    app.run(debug=args.debug, host=args.host, port=args.port)

