# MiliKara HTTP API (local WebUI)

Served by `milikara serve` (FastAPI, default `http://127.0.0.1:8765`). All JSON unless noted.
Times are integer ms on the original audio timeline, intervals `[start_ms, end_ms)`.
Errors: HTTP 4xx/5xx with `{"detail": "<human readable message>"}` (pydantic body validation errors are FastAPI's 422 list).

`Project`, `LyricsDoc`, `Line`, `Segment`, `Unit`, `Calibration`, `AudioAsset`, `VideoAsset`,
`AlignmentResult`, `UnitTiming`, `Issue`, `Candidate`, `MixSettings`, `KaraokeStyle`, `AiRoundtrip` are the
pydantic models in `kara_align/models.py`, serialized as-is. `AppSettings` / `TaskStyleOptions` are in
`kara_align/settings.py`, `PipelineTask` in `kara_align/pipeline.py`.

## 快速上手：用脚本提交任务

下面是用脚本驱动极简模式的完整流程（服务默认在 `http://127.0.0.1:8765`；脚本请求不需要浏览器的 `Origin` 头，只要发往本机地址）。完整的接口说明在后面的英文参考里。

**1. 添加任务**：`file` 是视频或音频；`lyrics` 是网易云 / QQ 音乐的歌曲链接或 LRC / 纯文本歌词；`background`（可选）是背景图片或循环播放的背景视频；`style`（可选）是这首歌的字幕选项（JSON，不给则用上次的选择）。

```bash
curl -F file=@song.mp3 -F background=@cover.jpg \
     -F lyrics='https://music.163.com/song?id=505665083' -F mode=lrc \
     http://127.0.0.1:8765/api/tasks
```

返回的 `PipelineTask` 里有任务 `id`。

**2. 查看进度**：`GET /api/tasks` 返回所有任务（新的在前）。`status` 为 `preparing` / `queued` / `running` 时继续等待；`succeeded` 时 `outputs.video.url` 就是成品视频的下载地址；`failed` 时看 `error`。

**3. 任务在等你（`status` 为 `waiting`）**：看哪个步骤的 `status` 是 `waiting`。

- `calibrate`（确认第一句从哪里开始唱）：`task.calibration` 里有第一句的歌词和它在 LRC 里的时间 `lrc_ms`。
  `POST /api/tasks/{id}/calibration`，body `{"marked_ms": 14684}`（第一句实际开始唱的毫秒数），或 `{"plain": true}` 改用普通模式。
  设置里把“歌词开头对齐”改成自动检测（`simple.calibration: "auto"`）后，只有没把握时才会停在这里。
- `readings`（AI 注音选了“手动（网页聊天）”）：`GET /api/tasks/{id}/readings/prompt` 取提示词，发给任意 AI，
  再 `POST /api/tasks/{id}/readings`，body `{"text": "<AI 的完整回复>"}`；不想注音就 `{"skip": true}`。回复完全不能用时返回 400 和原因，任务继续等待。

**4. 下载视频**：`GET` 第 2 步里的 `outputs.video.url`。

只用 Python 标准库的例子（添加任务后一直等到完成，第一句的位置已知时自动确认）：

```python
import json, time, urllib.request, uuid

BASE = "http://127.0.0.1:8765"

def call(method, path, body=None):
    req = urllib.request.Request(BASE + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req) as r:
        return json.load(r)

def add_task(media, lyrics, background=None, mode="lrc"):
    boundary, body = uuid.uuid4().hex, b""
    for k, v in {"lyrics": lyrics, "mode": mode}.items():
        body += f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
    for k, path in {"file": media, "background": background}.items():
        if path:
            name = path.rsplit("/", 1)[-1]
            body += f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"; filename="{name}"\r\n\r\n'.encode()
            body += open(path, "rb").read() + b"\r\n"
    body += f"--{boundary}--\r\n".encode()
    req = urllib.request.Request(BASE + "/api/tasks", data=body, method="POST",
                                 headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req) as r:
        return json.load(r)

task = add_task("song.mp3", "https://music.163.com/song?id=505665083", background="cover.jpg")
while True:
    t = next(x for x in call("GET", "/api/tasks") if x["id"] == task["id"])
    if t["status"] == "waiting":
        waiting = next(s["key"] for s in t["stages"] if s["status"] == "waiting")
        if waiting == "calibrate":
            call("POST", f"/api/tasks/{t['id']}/calibration", {"marked_ms": 14684})
        elif waiting == "readings":
            call("POST", f"/api/tasks/{t['id']}/readings", {"skip": True})
    elif t["status"] in ("succeeded", "failed", "cancelled"):
        print(t["status"], t.get("outputs"), t.get("error"))
        break
    time.sleep(2)
```

## Access and status codes

- **Local only.** Requests whose `Host` is not `127.0.0.1`, `localhost` or `[::1]` get **403** (DNS rebinding);
  more host names can be allowed with `milikara serve --allow-host NAME` (repeatable; `--host` with a named
  address allows that name too). A request other than GET / HEAD / OPTIONS that carries an `Origin` of another
  site (scheme not http/https, or host:port ≠ `Host`) gets **403** (CSRF).
- **400**: invalid input or a state that does not allow the operation (`ServiceError`, `ProjectError`, mix / fetch errors).
- **404**: unknown or invalid project id (ids must match `^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$`), unknown result id
  (`/results/{rid}/…`, `result_id=` of an export), job, task, exported file.
- **409**: see [Conflicts](#conflicts-409).
- **413**: text body > 5 MB, upload > 8 GB (media), project.json > 64 MB, package > 4 GB.
- **422**: body does not match its schema, e.g. an unknown `kind` in `PATCH …/lines/{id}` (`lyric|translation|romanization|meta|blank`)
  or an unknown lyrics `origin` (`paste|upload|netease|qq|project|manual`).

## General

| Method | Path | Body | Response |
| --- | --- | --- | --- |
| GET | `/api/info` | – | `{version, backends: [{name, description, languages, default_model, default_revision, license, available, missing}], separation_presets: [{name, model_filename, architecture, notes, license_note, leading_padding_samples}], separation_available: bool, tasks_elsewhere: bool, export_formats: {fmt: {filename, description}}}` |
| GET | `/api/jobs/{job_id}` | – | `Job` = `{id, kind, project_id, status: queued\|running\|succeeded\|failed\|cancelled, progress 0..1, message, error, created, finished, output}` |
| POST | `/api/jobs/{job_id}/cancel` | – | `Job` |
| GET | `/api/projects/{pid}/jobs` | – | `[Job]` |

Job kinds: `align`, `separate`, `ai`, `calibrate`, `mix`, `video`, `burn`. Heavy jobs (align, separate, calibrate, burn)
run one at a time and share one lock with the simple-mode queue. Cancelling a job (or stopping the server) stops its
subprocesses: separation, AI CLI processes (the whole process group), ffmpeg. Jobs live in memory: after a restart
they are gone (the UI shows an operation that was running as failed: the local server was restarted).

`tasks_elsewhere: true` means another server process on the same workspace runs the simple-mode task queue (see below).

## Projects

| Method | Path | Body | Response |
| --- | --- | --- | --- |
| GET | `/api/projects` | – | `[{id, name, mode, updated, size}]` newest first (`size`: bytes of the project folder; unreadable projects are left out) |
| POST | `/api/projects` | `{name, mode: "plain"\|"lrc"}` | `ProjectView` |
| GET | `/api/projects/{pid}` | – | `ProjectView` |
| PATCH | `/api/projects/{pid}` | `{name?, mode?, config?: AlignConfig (partial ok), mix?: MixSettings (partial ok)}` | `ProjectView`; every value is checked before anything changes (NaN / ∞ → 400) |
| DELETE | `/api/projects/{pid}` | – | `{ok}`; deletes the project folder (audio, stems, exports) and the playback / waveform cache of audio no other project has. 409 while a simple-mode task or a job works on it |
| POST | `/api/projects/import` | multipart `file` (project.json or .kara.zip) | `ProjectView`; the imported project always gets a **new id** (an id in the file is never used as a folder name) |
| GET | `/api/projects/{pid}/package?include_audio=1` | – | zip download (built in a temporary folder, not kept in `exports/`) |

`ProjectView` = `{project: Project, view: {effective_starts: {line_id: {ms, kind: "soft"|"hard"}}, calibration_issues: [Issue], mode_notice: str|null, results: [ResultSummary], capability_warnings: [str], audio: {role: {asset_id, duration_ms, sample_rate, available: bool, outdated: bool}}, picture: {source: "background"|"video"|"black", width, height, kind?: "image"|"video", filename?}}}` (`picture`: what a burned video shows by default and its frame size).

- `ResultSummary` = `{id, created, mode, stale, stale_reason, coverage, parent_result_id, n_units, n_failed, n_issues, n_manual, audio_role, backend}`.
- `audio.*.outdated`: a vocals / instrumental stem separated from an original that has since been replaced; such stems are
  not `available` (not used for alignment, listening, mixes or reduced-vocal videos) until separated again.
- Some endpoints add fields to the `ProjectView`: `messages` (lyrics/apply), `report` (readings/prepare),
  `summary` (ai/apply), `result_id` (results/import), `paired` (lyrics/fetch-translation).

Result staleness is recomputed on every read: results whose input snapshot no longer matches the current
lyrics text / readings / voices, unit flags, segment languages, anchor tolerances, end marks (`stats.detail_revision`) /
calibration / mode / audio get `stale: true` with a reason (still viewable).

Stored data is read leniently: `MixSettings` values outside their ranges go back to the default, a damaged
`project.json` is replaced by `project.json.bak` (the damaged file is kept as `project.broken-<time>.json`).
Content hashes (`sha256`) must be 64 lowercase hex characters.

## Lyrics input (paste and upload share the same path: uploads are read as text by the browser and sent with `origin: "upload"` + `filename`)

| Method | Path | Body | Response |
| --- | --- | --- | --- |
| POST | `/api/projects/{pid}/lyrics/parse` | `{text, origin: "paste"\|"upload", filename?}` | `LyricsPreview` |
| POST | `/api/projects/{pid}/lyrics/apply` | `{preview_id}` | `ProjectView` + `messages: [str]` (replaces the lyrics doc; old results become stale) |
| POST | `/api/projects/{pid}/lyrics/track/preview` | `{text, kind: "translation"\|"romanization", origin, filename?}` | `{kind, pairs: [{line_id, line_text, text, method: "time"\|"nearest"\|"order", delta_ms}], unmatched_line_ids: [str], unmatched: [text]}` |
| POST | `/api/projects/{pid}/lyrics/track/apply` | `{kind, pairs: [{line_id, text}]}` | `ProjectView` |
| POST | `/api/projects/{pid}/lyrics/fetch-translation` | – | `ProjectView` + `paired: int` (the translation of the NetEase / QQ song the lyrics came from; 400 when there is none) |
| POST | `/api/lyrics/link` | `{text}` (URL / share text / short link / `netease:123` / `qq:mid`) | `{kind: "song", song: FetchedSong}` or `{kind: "collection", platform, title, songs: [{platform, song_id, title, artists, album, duration_ms}]}` |
| POST | `/api/lyrics/song` | `{platform, song_id}` | `{kind: "song", song: FetchedSong}` |
| POST | `/api/projects/{pid}/lyrics/from-song` | `{platform, song_id}` | `LyricsPreview` (original track; translation/romanization offered as `extra_tracks`; plus `song` without `tracks`) |

`lyrics/parse` also accepts `prepared.json` (lyrics with readings). For project / alignment / reading-patch JSON it returns an `error` plus `route` naming where that file belongs.

`LyricsPreview` = `{preview_id, detected, warnings: [str], error: str|null, doc: LyricsDoc, extra_tracks: {kind: text}}`. When `error` is set (e.g. LRC mode without valid times) the preview cannot be applied; the UI must offer to add times or switch mode.
`FetchedSong` = `{platform, song_id, title, artists: [str], album, duration_ms, tracks: {original?, translation?, romanization?}, has_timestamps: {track: bool}, …}`.

Applying other lyrics un-confirms the LRC calibration (unless every timed line and `[offset]` is unchanged); the reference
mark and check marks stay only on lines with the same text and time, and a shift that belonged to other lyrics is reset
to 0 with a message (undo: `calibration/undo`). QQ Music's `//` placeholder lines never become translations.

## Lines and readings

| Method | Path | Body | Response |
| --- | --- | --- | --- |
| PATCH | `/api/projects/{pid}/lines/{line_id}` | `{text?, sing?, kind?, translation?, voice?, countdown?: "auto"\|"on"\|"off"}` | `ProjectView` (a line edited, or switched back to a sung lyric without units, gets rule readings at once) |
| POST | `/api/projects/{pid}/lines/merge` | `{line_ids}` | `ProjectView` |
| POST | `/api/projects/{pid}/lines/{line_id}/split` | `{at: int (char index)}` | `ProjectView` |
| PUT | `/api/projects/{pid}/lines/{line_id}/anchor` | `{abs_ms: int\|null, hard: bool, tolerance_ms}` | `ProjectView` |
| POST | `/api/projects/{pid}/readings/prepare` | `{overwrite_rule: bool}` | `ProjectView` + `report` |
| PUT | `/api/projects/{pid}/lines/{line_id}/segments/{segment_id}` | `{reading, units?: [str], confirm: bool}` | `ProjectView`（读音用片假名写、而原文不是假名时，记为 `katakana`：字幕注音显示片假名） |
| PUT | `/api/projects/{pid}/lines/{line_id}/segments/{segment_id}/hidden` | `{hidden: bool}` | `ProjectView`（`hidden`：括号里是前一个词的读音，字幕里不显示、不演唱；只能用于没有读音的片段） |
| POST | `/api/projects/{pid}/ai/prompt` | `{line_ids?: [str]}` | `{prompt, snapshot_id, roundtrip_id}` |
| POST | `/api/projects/{pid}/ai/validate` | `{text}` (raw chat reply or JSON) | `{report_id, report: PatchReport}` |
| POST | `/api/projects/{pid}/ai/auto` | `{line_ids?}` | `Job` (kind `ai`; output = the `/ai/validate` response + `meta {provider, attempts: [{provider, model, elapsed_s, cost_usd}], cost_usd}`; nothing applied). 400 when the provider is `manual` (use `/ai/prompt` + `/ai/validate`) |
| POST | `/api/projects/{pid}/ai/apply` | `{report_id, line_ids?: [str]}` | `ProjectView` + `summary`. 409 when a line of the report no longer exists (lines merged / split since the validation) |

`PatchReport` = `{ok: bool, snapshot, roundtrip_id, errors: [str], warnings: [str], missing_line_ids: [str], lines: [{line_id, status: "ok"|"stale_text"|"stale_reading"|"unknown_line"|"locked_skipped"|"invalid"|"duplicate", reasons: [str], segments: [..], diff: [{surface, old_reading, new_reading, old_units: [str], new_units: [str], changed, locked}]}]}`.

The project keeps each round trip as an `AiRoundtrip`; its `report` is `{line_hashes, line_texts, validation}`: the
per-line reading hashes and texts taken when the prompt was made (so a later validation still sees readings changed since)
and the last `PatchReport`.

## Audio

| Method | Path | Body | Response |
| --- | --- | --- | --- |
| PUT | `/api/projects/{pid}/background` | multipart `file`: a picture (PNG / JPG / WebP / BMP) or a video (MP4 / MOV / MKV / WebM / GIF …, ≤ 4 GiB) | `ProjectView`. Stored as `project.background` `{id, sha256, path, filename, kind: "image"\|"video", width, height, duration_ms}`; from then on preview and burn (`background: "auto"`) show it instead of the project's video / black: a picture held for the whole song, a video looped (its own sound never used), scaled to cover a frame of its aspect ratio with the longer side 1920 px; the audio is the project's (timeline from the audio start). 400 for anything else (checked by extension, content and ffprobe); 409 while a task works on the project |
| POST | `/api/projects/{pid}/background/cover` | – | `ProjectView`: the song's cover as the background — the cover blurred and darkened over a 1920×1080 frame, the cover itself in the upper middle, the bottom darker for the lyrics (`kara_align/karaoke/cover.py`). The cover is the one of the music link the lyrics were fetched from (its URL kept in the source snapshot's `fetched_meta.cover_url`; lyrics fetched before: the platform is asked again); only from the platforms' image hosts (`p*.music.126.net`, `y.gtimg.cn`). 400: lyrics not from a link, no cover, cover unreadable. `view.cover` says whether there is one. Simple-mode tasks with audio only, lyrics from a link and no background of their own get it automatically (a warning when it fails) |
| DELETE | `/api/projects/{pid}/background` | – | `ProjectView` (back to the video / black; the file is deleted) |
| GET | `/api/projects/{pid}/background/file` | – | the background file (404 without one) |
| POST | `/api/projects/{pid}/audio` | multipart `file`, form `role: original\|vocals\|instrumental` | `ProjectView` (+ stems get `sync_report`). The file may be a **video**: its first audio track is extracted losslessly (FLAC) and used; a video uploaded as the original is kept as `project.video` (with `audio_offset_s`) for re-muxing. |
| GET | `/api/projects/{pid}/audio/{asset_id}/playback.wav` | – | decoded PCM WAV (same decoder as alignment → identical time origin). Supports Range. |
| GET | `/api/projects/{pid}/audio/{asset_id}/peaks?per_second=200` | – | `{sample_rate, duration_ms, per_second, mins: [float], maxs: [float]}` (mono, first peak at 0 ms) |
| POST | `/api/projects/{pid}/separate` | `{preset, device?: "auto"\|"cpu"}` | `Job` (on success adds vocals + instrumental assets). `preset` must be one of `/api/info.separation_presets` (`melband-roformer`, `bs-roformer`, `mdx-fast`, `demucs-htdemucs`), else 400. Stopped after duration × 5 + 10 min |
| POST | `/api/projects/{pid}/mix/preview-gain` | `MixSettings` | `{bus_gain, peak_before}` |
| POST | `/api/projects/{pid}/mix/export` | `MixSettings` | `Job` (kind `mix`); output `{filename, url, report}`; `url` downloads the WAV |
| POST | `/api/projects/{pid}/video/export` | `MixSettings` | `Job` (kind `video`); output `{filename, url, report}`: the original video's picture copied unchanged, the mix as its only soundtrack, at the original audio offset. Needs `project.video` and current stems. |

`MixSettings` = `{vocal_keep_pct: 0–100, instrumental_pct: 0–100, master: 0–4, limiter: "none"|"normalize_peak"}`; a value out of
range, NaN or not a number is refused with 400 (before a job starts).

Mix rule (same in browser and export): `mix = master × (p/100·V + q/100·I)`; bus gain `min(1, 10^(-0.3/20)/peak)` when `limiter = normalize_peak`. Browser playback computes it with GainNodes; only the export applies a precomputed bus gain from the full-file peak (the UI shows the same number from `/mix/preview-gain`).

## Exported files

| Method | Path | Response |
| --- | --- | --- |
| GET | `/api/projects/{pid}/exports` | `[{filename, url, size, modified}]`: files in the project's `exports/` folder, newest first (partial files whose name starts with `.` are left out). Every export gets a new name, so an earlier one is never overwritten: `<song>-karaoke[-vocal<N>\|-noaudio]-<YYYYMMDD-HHMMSS>.mp4` (burn), `<video name>-vocal<N>-<time>.<ext>` (reduced-vocal video), `<song>-mix-v<N>-i<M>-<time>.wav` (mix); local time, `-2`, `-3` … when two are made in the same second |
| GET | `/api/projects/{pid}/exports/{filename}` | the file (download); 404 for anything that is not a plain file name in `exports/` |

Every `url` returned for an exported file (mix, video, burn, task video, this list) is
`/api/projects/{pid}/exports/{filename}` with both parts URL-encoded (names may contain `#`, `?`, `%`, spaces …).

## Calibration (LRC mode)

| Method | Path | Body | Response |
| --- | --- | --- | --- |
| POST | `/api/projects/{pid}/calibration/mark` | `{line_id, marked_ms}` | `ProjectView` |
| POST | `/api/projects/{pid}/calibration/shift` | `{user_shift_ms}` | `ProjectView` |
| POST | `/api/projects/{pid}/calibration/confirm-zero` | – | `ProjectView` |
| POST | `/api/projects/{pid}/calibration/check` | `{line_id, marked_ms}` | `ProjectView` (check residual in `calibration.checks`, warnings in `view.calibration_issues`) |
| POST | `/api/projects/{pid}/calibration/undo` | – | `ProjectView` |
| POST | `/api/projects/{pid}/calibration/suggest` | – | `Job` (kind `calibrate`; output `{shift_ms, agree, tight, lines_checked, confident, reason, drift_ms, line_starts, vocal_onset_ms, audio_role}`; nothing is saved). `agree` / `tight`: share of lines within 0.7 s / 0.3 s of `shift_ms`; `confident` false with a `reason` when the estimate should not be used without listening (see `kara_align/auto_calibrate.py`). Uses the vocals only when the stems are current |

## Alignment and results

| Method | Path | Body | Response |
| --- | --- | --- | --- |
| POST | `/api/projects/{pid}/align` | `{line_ids?: [str], audio_role?: "original"\|"vocals", config?: partial AlignConfig}` | `Job` (output `{result_id}`) |
| GET | `/api/projects/{pid}/results/{rid}` | – | `AlignmentResult` (with fresh `stale`) |
| POST | `/api/projects/{pid}/results/import` | `{text}` (alignment.json content) | `ProjectView` + `result_id` (non-active; staleness recomputed) |
| POST | `/api/projects/{pid}/results/{rid}/activate` | – | `ProjectView` |
| PUT | `/api/projects/{pid}/results/{rid}/units/{uid}` | `{start_ms, end_ms, locked}` | `UnitTiming` |
| DELETE | `/api/projects/{pid}/results/{rid}/units/{uid}/manual` | – | `UnitTiming` |
| POST | `/api/projects/{pid}/results/{rid}/units/{uid}/lock` | `{locked}` | `UnitTiming`; 400 when the unit has no times to lock |
| POST | `/api/projects/{pid}/results/{rid}/units/{uid}/restore` | `{manual: ManualEdit\|null}` | `UnitTiming` (undo/redo support) |
| POST | `/api/projects/{pid}/results/{rid}/lines/{lid}/retime` | `{start_ms?, end_ms?}` | `{units: [UnitTiming]}`: the line's timed units shifted so the first starts at `start_ms`, or with `end_ms` too mapped onto `[start_ms, end_ms)` (proportions kept; `end_ms` alone moves only the end); each becomes a locked manual edit. 400 when the line has no timed units or the times are out of order / past the audio |
| POST | `/api/projects/{pid}/results/{rid}/units/retime` | `{unit_ids, start_ms?, end_ms?}` | `{units: [UnitTiming]}`: the same for any set of timed units (may span lines), taken together by their outer edges |
| POST | `/api/projects/{pid}/results/{rid}/adopt` | `{from_result_id?, candidate_id?, line_ids: [str]}` | `AlignmentResult` (copies non-locked unit times of the lines from a local rerun or a candidate; locked units untouched) |

A local rerun (`align` with `line_ids`) creates a new partial result with `parent_result_id`; it never overwrites the parent.
In plain mode it decodes only the stretch between the neighbouring lines' times in the previous result. The UI compares and adopts per line.

`AlignmentResult.stats` includes `algorithm` (currently `"kara-align-decoder/3"`), `original_sha256` (the original recording the
times refer to) and `detail_revision`. Manual edits of the previous result are applied before the checks run; they are not
carried to a different original (issue `manual_audio_changed`, the edits stay in the unit's `manual_history`), follow a reading
change by position inside the segment (`manual_reading_changed`) or are reported as dropped (`manual_dropped`). A segment with
letters / digits but no reading gives `segment_no_reading`; lines whose LRC time is after the end of the audio are left out
(`lines_after_audio`).

## Export

| Method | Path | Response |
| --- | --- | --- |
| GET | `/api/projects/{pid}/export/{fmt}?result_id=&download=1` | file download (Content-Disposition with a UTF-8 file name; `X-Export-Warnings: <count>` when there are warnings) |
| GET | `/api/projects/{pid}/export/{fmt}?result_id=` | `{filename, media_type, content, warnings}` |

`fmt` ∈ `alignment, prepared, project, csv, lrc-line, lrc-unit, lrc-calibrated, karaoke-ass`; stems via `/audio/{asset_id}/playback.wav`, mix via `/mix/export`.
Warnings say when the result is stale or partial and how many lines were skipped because they are after the end of the audio.
`karaoke-ass` always uses the active result and the project's saved style (see below).

## Karaoke subtitles

| Method | Path | Body | Response |
| --- | --- | --- | --- |
| GET | `/api/fonts` | – | `{default, families: [{family, names, bold}]}` (fonts that can render Japanese, via fontconfig) |
| GET | `/api/karaoke/styles` | – | `[{id, name, builtin, updated, style: KaraokeStyle}]` (built-in 默认 / 暖阳 first, then the saved ones) |
| POST | `/api/karaoke/styles` | `{name, style, id?}` | the saved entry (`id` given: overwrite it; else a new one, or the one with this name). 400 for a built-in name / id or an invalid style |
| DELETE | `/api/karaoke/styles/{style_id}` | – | `{ok}` (built-ins cannot be deleted) |
| GET | `/api/karaoke/themes` | – | `{templates: [{id: "plain"\|"glow", label}], swatches}` |
| POST | `/api/karaoke/theme` | `{template, color, secondary?, base?: KaraokeStyle}` | `{palette, style}`: the colour template applied on top of `base` (default: the simple mode's style) |
| GET | `/api/projects/{pid}/karaoke` | – | `KaraokeStyle` |
| PUT | `/api/projects/{pid}/karaoke` | `KaraokeStyle` | `KaraokeStyle` (validated strictly, see below) |
| GET | `/api/projects/{pid}/karaoke/info` | – | `{fields: {field: text}, labels, text: str\|null}` (title card data; `text` = the project's own text) |
| PUT | `/api/projects/{pid}/karaoke/info` | `{text: str\|null}` | same as GET (`null` goes back to the song data) |
| PUT | `/api/projects/{pid}/karaoke/singers` | `KaraokeSingers` | `KaraokeStyle` (only the style's singers change; validated strictly) |
| POST | `/api/projects/{pid}/karaoke/singers/preset` | `{id}` | `ProjectView` + `lines` + `kept`: the singers of a saved set (below) used in this song. Parts already assigned stay with the same person: matched by name, an unnamed singer by number; one the preset lacks but the lyrics use is added after the preset's (`kept`: their names), with a free key. 404: no such preset |
| GET | `/api/karaoke/singer-presets` | – | `[{id, name, updated, singers: KaraokeSingers}]` by name (`<home>/singer_presets.json`, shared by every project) |
| POST | `/api/karaoke/singer-presets` | `{name, singers, id?}` | the saved preset (no `id`: a new one, or the one of that name replaced; 400: no name, no singers, invalid singers) |
| DELETE | `/api/karaoke/singer-presets/{id}` | – | `{ok}` |
| DELETE | `/api/projects/{pid}/karaoke/singers/{n}` | – | `ProjectView` + `changed` (lines whose assignment changed): singer `n` (1-based) removed, its parts go back to the line's other singers, later numbers move down by one (in combinations too; one left with fewer than two singers is removed) |
| POST | `/api/karaoke/singer-colors` | `{members: [KaraokeSinger]}` | `[{sung, unsung, outline, glow_sung, glow_unsung, translation, sparkle}]`: each singer's colours with the derived ones filled in |
| PUT | `/api/projects/{pid}/singers` | `{lines: [{line_id, singers, spans: [{start, end, singers}], text?}]}` | `ProjectView`: who sings these lines (replaces their assignment; `text`: the line's text the spans were made for, 400 when it changed since) |
| GET | `/api/projects/{pid}/singers/markers` | – | `{lines: [{line_id, text, prefix, names, everyone}], names, existing}`: lines that start with singer names (“A：”, “（XX）”, “【XX】”; a name must start at least two lines) |
| POST | `/api/projects/{pid}/singers/markers` | `{names?: [str], strip?: true}` | `ProjectView` + `messages`: those lines assigned to the named singers (new names added to the style; “全员 / 合 / ALL …” = every one of them); `strip` takes the names out of the lyrics (the text changes: results become outdated) |
| POST | `/api/projects/{pid}/karaoke/preview` | `{t_ms, style?, background?: "auto"\|"black"}` | `image/png` of the whole frame at `t_ms` (libass, the video's displayed size) |
| POST | `/api/projects/{pid}/karaoke/burn` | `{background?: "auto"\|"black", audio?: "original"\|"mix"\|"none", quality?: "standard"\|"high", vocal_keep_pct?: 0–100}` | `Job` (kind `burn`); output `{filename, url, warnings}` |

**Countdown** (开唱倒计时): `KaraokeStyle.countdown = {intro: true, interlude: true, min_gap_ms: 6000 (2000–30000), dots: 3 (2–5)}`:
dots above the start of the first line (`intro`) and of a line after a pause of at least `min_gap_ms` since everything before it
was sung (`interlude`); in the last `dots` seconds one goes each second (evenly over a shorter wait), the last as the line's sweep
starts (so with `advance_ms`). A line can override the rules: `Line.countdown` true / false (null = the rules), set with
`PATCH …/lines/{id}` `{countdown: "on"|"off"|"auto"}`. Such a line appears as its countdown begins (`dots` seconds before it is sung, not earlier with `early_show`), so the first dot goes a second after it appears. In the ASS the dots are
drawings with the style `KDots`. Simple-mode tasks: `task_style.countdown_intro` / `countdown_interlude` (null = as the style says).

**Singers** (多人演唱): `KaraokeStyle.singers = {members: [{name, key, color, color_unsung, color_sung, outline_color, glow_unsung,
glow_sung}] (any number; "" colours are derived from `color`), mix: "split"|"gradient", direction: "vertical"|"horizontal",
ruby: "auto"|"split"|"first", combos: [{key, singers, mix?, direction?}]}` (vertical: every character top to bottom; horizontal:
each run sung together left to right; `ruby`: the reading over a part sung together split like its lyric, in the first singer's
colours, or "auto" = the first singer's when split top to bottom, else split; `combos`: singers who sing together, with their own
`mix` / `direction` (null: the singers' setting) — a part sung by exactly a combination's singers, in its order or else in any
order, takes the combination's look; the same singers in the same order twice is refused when saved, dropped when loaded). `key`: the key that assigns a singer / combination on the
演唱者 page, one character of `123456789abcdefghijkmnoqrstuvwxyz` (1–9, then a–z without l and p) or "" (none); new ones take
the first free one in that order. A key that is not usable or used twice, or a combination of fewer than two singers, is
refused when saved; when loaded the key is cleared (the combination dropped). Saved before keys existed: singer n has key n
(1–9), a combination's number key becomes that character.
A translation keeps its own colours (`KTrans`); with `translation.singer_glow` (default true) its glow takes the line's singers' glow colours, several blended from left to right in the order they sing (never top to bottom); false: the translation's own glow.
Who sings is kept in the lyrics: `Line.singers` (numbers, 1-based; several = together; empty = the style's own colours) and
`Line.singer_spans: [{start, end, singers}]` (character ranges of `Line.text` sung by others than the line's singers;
blanks belong to nobody: inside a part they join it, between parts they keep the line's own singers). They
only change the subtitles' colours, never an alignment; text edits, merging and splitting lines carry them along. In the
ASS, each singer has styles `KMain_n` / `KRuby_n` / `KTrans_n` and its name in the events' Name field; a part sung together
is drawn once per singer, each copy cut to its band (`\clip`), or in thin blended strips (gradient). The project view's
`view.singer_markers` counts lines that start with singer names.

Styles are validated strictly when saved (`PUT …/karaoke`, `POST /api/karaoke/styles`: 400 for a colour that is not
`#RRGGBB` / `#RGB` or a number out of range); styles read from projects, `styles.json` or `settings.json`, the style of
`PUT /api/settings` and the `style` of a preview are clamped / fixed up instead (`#RGB` → `#RRGGBB`, numbers into their range,
unknown values → default).

## App settings and AI

| Method | Path | Body | Response |
| --- | --- | --- | --- |
| GET | `/api/update` | `?refresh=1` | `{enabled, current, latest, newer, portable, updater, url, error}`: is a newer version out (the latest GitHub release's `manifest.json`, asked at most every 6 h; `refresh=1` asks now). With `check_updates: false` in the settings (and no `refresh`): `{enabled: false, current}`, nothing is asked. `portable`: running from a portable package, updated with its `updater` (`更新.bat` / `更新.command`) |
| GET | `/api/storage` | – | `{root, disk: {total, free}, projects: [{id, name, mode, updated, size, parts: {media, stems, background, exports, unused, other}, exports: [{filename, size, modified}], stems, busy}], projects_size, cache: {size, parts}, models: {size, path}, leftovers: {size, parts: {asset?, upload?, folder?, deleted?}}, working}` (bytes; projects largest first). `unused`: files in `assets/` no entry points to; leftovers also count a task's staged upload once it is imported, project folders without a project file and `.deleted-*` folders (files younger than 10 min are left out) |
| POST | `/api/storage/clean` | `{cache?, leftovers?}` | the same view plus `freed`. The cache is refused (409) while a task or operation runs; leftovers skip projects something is working on |
| POST | `/api/projects/{pid}/storage/clean` | `{exports?: true \| [filename…], stems?: true}` | the same view plus `freed`: exported files (all or the ones named), the separated stems (their entries and files). 409 while a task or operation works on the project |
| GET | `/api/diagnostics` | `?task=&job=` | `{text}`: a report to paste into a bug report (version, system, Python, torch / GPU, ffmpeg + libass, the video encoder, the main settings, the task's / job's error, stages and traceback when given, the end of `~/.kara_align/logs/milikara.log`); the home folder is shown as `~`, never an API key |
| GET | `/api/settings` | – | `AppSettings` (`check_updates`: look for a newer version when the app is opened; `hardware_encoding` (default true): burn videos with a working GPU encoder, falling back to libx264; `ai.api_key` is never returned; `ai.has_api_key`, `ai.env_key_present` instead). `ai.enabled`: AI readings on / off (simple-mode tasks); `ai.provider`: `manual` (copy the prompt into any web chat and paste the reply) \| `claude` \| `codex` \| `openai`. Settings from before the switch are read as: `provider: "none"` → off + `manual`; a CLI / API provider → on, unless the old `simple.ai_readings` was false |
| PUT | `/api/settings` | partial `{ai?, simple?}` (nested merge); `ai.api_key` replaces the key only when non-empty, `ai.clear_api_key: true` removes it, `simple.reset_karaoke: true` resets the simple mode's style to the built-in 暖阳 | `AppSettings`; 400 `设置无效：…` for an invalid value |
| GET | `/api/ai/providers?refresh=0` | – | `[{id: "claude"\|"codex"\|"openai", label, available, version, detail}]`; for the CLIs also `locations: [{source: "path"\|"app"\|"wsl"\|"custom", program, distro, version, where, label}]` (every place found: PATH, a desktop app's own copy — the Claude app's `…/Claude/claude-code/<version>/`, the ChatGPT / Codex app's `codex-cli` — and, on Windows, each WSL distribution, probed with the user's login shell), `where` (the setting) and `chosen` (the one used, or null with the reason in `detail`). WSL results are cached 10 min; `refresh=1` looks again. Settings: `ai.claude_cli` / `ai.codex_cli` = `{where: "auto"\|"path"\|"app"\|"wsl:<distro>"\|"custom", path}`; "auto" tries PATH, the app's copy, then WSL |
| POST | `/api/ai/test` | optional overrides of the AI settings | `{ok, reply?, model?, elapsed_s?, cost_usd?, error?}` |

- `ai.base_url` must be an `http://` or `https://` URL without user / password; `ai.api_key_env` must match
  `^[A-Z][A-Z0-9_]*(KEY|TOKEN)$`; `simple.separation_preset` must be a known preset.
- `/api/ai/test` with a `base_url` other than the saved one needs the `api_key` for it in the same request: the saved key
  (or the key variable) is only ever sent to the saved address.
- `settings.json` (`<KARA_ALIGN_HOME>/settings.json`, default `~/.kara_align`, mode 600): a field that is no longer valid
  falls back to its default, every other field (the API key above all) is kept; an unreadable file is kept as
  `settings.broken-<time>.json` and the defaults are used. An unreadable `styles.json` (saved styles) is kept as
  `styles.broken.json` (or `styles.broken-<time>-<id>.json` when that exists); a saved entry this version cannot read is
  skipped and written back unchanged.

## Simple-mode task queue

| Method | Path | Body | Response |
| --- | --- | --- | --- |
| GET | `/api/tasks` | – | `[PipelineTask]` newest first (without `karaoke`, `detail`, `warning_stage`) |
| POST | `/api/tasks` | multipart: `file` (video / audio), `lyrics` (music link or lyrics text), `mode` (`lrc`\|`plain`), `name?`, `style?` (JSON `TaskStyleOptions`: source / template / colours / saved preset / translation / title card / ruby / video sound; omitted = the last choices), `background?` (a picture or a video played in a loop behind the subtitles, as `PUT …/background`; checked before the task is added, 400 when unusable) | `PipelineTask` (with its `karaoke`, `video` and `processing` snapshots) |
| POST | `/api/tasks/{id}/cancel` | – | `PipelineTask` |
| POST | `/api/tasks/{id}/retry` | – | `PipelineTask` (continues from the stage that did not finish) |
| POST | `/api/tasks/{id}/calibration` | `{marked_ms}` (first sung onset of `calibration.line_id`) or `{plain: true}` | `PipelineTask` (only while `waiting`; the task continues) |
| GET | `/api/tasks/{id}/readings/prompt` | – | `{prompt, lines, snapshot_id}` (AI readings by hand: the prompt to copy into a web chat; 400 when the task has none) |
| POST | `/api/tasks/{id}/readings` | `{text}` (the chat's reply) or `{skip: true}` | `PipelineTask` (only while the `readings` stage waits; the reply is checked like `/ai/validate` and the lines that pass are applied; 400 with the reasons when no line is usable — the task keeps waiting; 409 while a detailed-mode job runs on the project) |
| DELETE | `/api/tasks/{id}` | – | `{ok}` (the project stays) |

Adding a task from a script (e.g. with audio downloaded elsewhere): no browser `Origin` header is needed, only a local `Host`.

```bash
curl -F file=@song.mp3 -F background=@cover.jpg -F lyrics='https://music.163.com/song?id=347230' -F mode=lrc \
     http://127.0.0.1:8765/api/tasks
```

`PipelineTask` = `{id, created, finished, name, mode, media_filename, background_filename, lyrics_kind: "link"|"text", lyrics_input, status, project_id, project_deleted, progress, message, error, warnings: [str], current_stage, stages: [{key, label, status, progress, message, failed_soft}], outputs: {video?: {filename, url}}, calibration, calibration_confirmed, video: TaskVideo, processing: TaskProcessing, style_label, style_colors: [str], style_applied, name_auto}` (+ `karaoke: KaraokeStyle`, `detail` (traceback) and `warning_stage` in the responses of the POST endpoints, not in the list).

- `status`: `preparing|queued|running|waiting|succeeded|failed|cancelled|interrupted`; stage keys `import, lyrics, calibrate, readings, separate, align, export` in the order they run (tasks whose AI readings go through a web chat by hand — `processing.ai_provider: "manual"` — run `readings` in the preparation lane and wait there with `readings_request: {roundtrip_id, snapshot_id, lines, chars}`; tasks with `processing.calibration: "auto"` run `calibrate` — labelled 检测偏移 — after `separate`), stage status `pending|running|waiting|done|skipped|failed`.
- `calibration` (LRC mode, while `waiting`): `{line_id, line_text, lrc_ms, lines: [{id, text, lrc_ms}], check_line, asset_id, duration_ms, lines_after_audio, lines_total}`; the list adds `current_ms` (the project's current offset applied to `lrc_ms`, when one was set in the detailed mode, else `null`); after a confirmation it holds `confirmed_ms`. Automatic tasks that were not sure enough add `auto: {shift_ms, tight, lines, tight_lines, drift_ms, reason, confident}` (only `{reason}` when no estimate could be made); an automatic task that was sure goes on without waiting.
- `video` = `{auto_export, video_audio, vocal_keep_pct, quality}`, `processing` = `{ai_provider, ai_model, ai_readings (= `ai.enabled` when added), separate, separation_preset, separation_device, calibration: "manual"|"auto"}` (`calibration` from `simple.calibration`), both fixed when the task is added. `style_label` / `style_colors` describe the task's subtitle style for the list.
- `project_deleted: true`: the project was deleted in the detailed mode; the task stays listed without links and cannot be retried.
- When `tasks.json` cannot be written (disk full …) the running task gets a warning.

One server process per workspace runs the queue: it holds an exclusive lock on `<workspace>/.tasks/lock`. Another process
on the same workspace only shows the tasks (`/api/info.tasks_elsewhere: true`) and answers 409 to every change.
Stopping the server: tasks that had not started yet go back to `queued` / `preparing` (continued after the restart), running
ones become `interrupted` (retry continues them). An unreadable `tasks.json` is kept as `tasks.broken-<time>.json`.

## Conflicts (409)

- Task queue: removing a running task; retrying a task that is not failed / cancelled / interrupted; any change
  (add / cancel / retry / confirm / remove) while another server process runs the queue.
- While a simple-mode task is working on a project (preparing / waiting / queued / running), `POST …/align`, `…/separate`,
  `…/ai/auto`, `…/karaoke/burn`, `…/audio`, `…/calibration/suggest` and `DELETE /api/projects/{pid}` answer 409 with the task's name in `detail`.
- Conversely `POST /api/tasks/{id}/retry` and `/calibration` answer 409 while a detailed-mode `align`, `separate` or `ai` job
  runs on the task's project (jobs that only read the project — burns, exports, the offset suggestion — do not block them).
- `DELETE /api/projects/{pid}` while any job of the project is queued or running.
- `POST …/ai/apply` with a report whose lines no longer exist (lyrics merged / split since the validation).
