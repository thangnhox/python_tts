## 🗣️ Text-to-Speech Web App

A lightweight, self-contained **Text-to-Speech (TTS) web app** using **Flask** (Python backend) and **Edge-TTS** (Microsoft Neural voices).
Supports multiple text blocks, per-block voice selection, inline control tags, and MP3 export.

---

### 📂 Project Structure

```
project/
├── app.py
└── templates/
    └── index.html   ← contains all HTML, CSS, and JS
```

No external static files are needed — everything is bundled inside `index.html`.

---

### 🧩 Features

* 🎙️ **Multiple blocks** — each with its own language and voice
* 💬 **Inline control tags**:

  * `[voice id]: text` → switch to specific voice mid-script
  * `[pause n]` → pause for *n* seconds
* ▶️ **Speak one / Speak all**
* ⏹️ **Immediate stop** (aborts both playback and TTS generation)
* ⏯️ **Resume** playback from where you stopped
* 💾 **Export to MP3** (single or combined)
* ⚡ **Voice caching** for faster replays
* 🧹 **Automatic temp cleanup**

---

### ⚙️ Requirements

Install dependencies:

```bash
pip install flask edge-tts pydub tqdm
```

#### 🩹 In case of this error:

```
ModuleNotFoundError: No module named 'audioop'
```

It means you’re using **Python 3.13+** where `audioop` was removed.
Fix it by installing the compatibility package:

```bash
pip install audioop-lts
```

Then rerun:

```bash
python3 app.py
```

---

### 🚀 Run the App

```bash
python3 app.py
```

By default, Flask will start on:

```
http://localhost:8000/
```

You’ll see a web interface like this:

* Type or paste text into a block
* Choose language and voice
* Click **Speak** or **Speak All**
* Use **Stop** and **Resume** controls
* Export MP3 files if desired

---

### 🧠 Example Script Syntax

You can embed commands directly in your text:

```
[voice en-US-JennyNeural]: Hello! [pause 1.5] How are you today?
[voice en-AU-WilliamNeural]: G’day mate! Enjoying your morning?
```

---

### 🧹 Temporary Files & Cache

* Audio files are stored in:

  ```
  $TMPDIR/tts_cache/
  ```
* Automatically deleted after a few hours or when space exceeds ~500 MB.

You don’t need to clean them manually.

---

### 🛠️ Notes

* Works best in **Microsoft Edge** or **Chrome** (Edge offers the widest voice set).
* Compatible with Linux, macOS, and Windows.
* Internet connection is required for Edge-TTS voices.
* If `ffmpeg` is not installed, install it via your package manager for proper MP3 handling:

  ```bash
  sudo apt install ffmpeg
  ```

---

### 💡 Credits

* [Flask](https://flask.palletsprojects.com/) – lightweight Python web framework
* [Edge-TTS](https://github.com/rany2/edge-tts) – Microsoft TTS wrapper
* [Pydub](https://github.com/jiaaro/pydub) – audio processing
* [audioop-lts](https://pypi.org/project/audioop-lts/) – Python 3.13+ audio compatibility
