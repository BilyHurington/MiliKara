"""End-to-end flows through the service layer and the HTTP API (scripted backend)."""

import json
import time

import numpy as np
import pytest
import soundfile as sf

from kara_align import service as S
from kara_align.align.backends.fake import ScriptedBackend

SCRIPT = [
    ("ki", 1000, 1200), ("mi", 1200, 1400), ("to", 1400, 1700),
    ("a", 3000, 3200), ("ru", 3200, 3400), ("i", 3400, 3600), ("ta", 3600, 3900),
]
LRC = "[ti:test]\n[offset:500]\n[00:01.50]きみと\n[00:03.50]あるいた\n"


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("KARA_ALIGN_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(ScriptedBackend, "default_script", SCRIPT)


def _wav(path, seconds=6.0, sr=22050):
    rng = np.random.default_rng(0)
    sf.write(path, (rng.standard_normal(int(seconds * sr)) * 0.05).astype(np.float32), sr)
    return path


def _project(tmp_path, mode):
    h = S.create_dir(tmp_path / "proj", "t", mode)
    S.update_settings(h, config={"backend": "scripted"})
    pv = S.parse_lyrics(h, LRC, origin="paste")
    assert pv["error"] is None
    S.apply_lyrics(h, pv["preview_id"])
    S.add_audio(h, _wav(tmp_path / "song.wav"), "original")
    return h


def _starts(result):
    return [(u.reading, u.start_ms, u.end_ms) for u in result.units]


def _close(result, tol=45):
    got = _starts(result)
    assert len(got) == len(SCRIPT)
    for (reading, st, en), (_, est, een) in zip(got, SCRIPT):
        assert st is not None and abs(st - est) <= tol, (reading, st, est)
        assert en is not None and abs(en - een) <= tol, (reading, en, een)


def test_plain_mode_end_to_end(tmp_path):
    h = _project(tmp_path, "plain")
    assert all(ln.imported_start_ms is None for ln in h.project.lyrics.lines)  # plain: text only
    r = S.run_align(h)
    _close(r)
    assert r.mode == "plain" and r.coverage.full
    assert h.project.active_result_id == r.id
    # reopening the project gives the same data
    h2 = S.open_dir(h.dir)
    assert h2.project.result().units[0].start_ms == r.units[0].start_ms


def test_lrc_mode_calibration_and_offset_applied_once(tmp_path):
    h = _project(tmp_path, "lrc")
    doc = h.project.lyrics
    assert doc.embedded_shift_ms == -500
    first = doc.sung_lines()[0]
    S.calibration_op(h, "mark", line_id=first.id, marked_ms=1000)
    assert h.project.calibration.user_shift_ms == 0  # 1500 - 500 = 1000 → no extra shift
    S.calibration_op(h, "mark", line_id=first.id, marked_ms=1050)
    assert h.project.calibration.user_shift_ms == 50  # recomputed, not accumulated
    S.calibration_op(h, "mark", line_id=first.id, marked_ms=1000)
    r = S.run_align(h)
    _close(r)
    assert all(lt.anchor_ms is not None for lt in r.lines)
    cal = S.export(h, "lrc-calibrated")
    assert "[offset" not in cal.content
    assert "[00:01.00]きみと" in cal.content and "[00:03.00]あるいた" in cal.content
    # re-import of the calibrated LRC does not shift again
    pv = S.parse_lyrics(h, cal.content, origin="paste")
    assert pv["doc"]["embedded_shift_ms"] == 0


def test_manual_lock_survives_rerun_and_staleness(tmp_path):
    from kara_align.project import edits

    h = _project(tmp_path, "plain")
    r1 = S.run_align(h)
    uid = r1.units[0].unit_id
    edits.set_manual(r1, uid, 990, 1210)
    h.save()
    r2 = S.run_align(h)
    u = next(x for x in r2.units if x.unit_id == uid)
    assert (u.start_ms, u.end_ms) == (990, 1210) and u.locked
    assert u.model_start_ms is not None
    # editing the lyrics text marks results stale (still viewable)
    line = h.project.lyrics.sung_lines()[1]
    S.update_line(h, line.id, text="あるいたよ")
    view = S.project_view(h)
    assert all(x["stale"] for x in view["view"]["results"])


def test_retime_line_shifts_or_stretches_every_unit(tmp_path):
    from fastapi.testclient import TestClient
    from kara_align.project import edits
    from kara_align.web.server import create_app

    h = _project(tmp_path, "plain")
    r = S.run_align(h)
    line = h.project.lyrics.sung_lines()[0].id
    units = [u for u in r.units if u.line_id == line]
    before = [(u.start_ms, u.end_ms) for u in units]
    s0, e0 = min(b[0] for b in before), max(b[1] for b in before)
    # a new start alone: the whole line moves, lengths kept, every unit locked
    changed = edits.retime_line(r, line, s0 + 250, None)
    assert [(u.start_ms, u.end_ms) for u in changed] == [(a + 250, b + 250) for a, b in before]
    assert all(u.locked and u.manual.note == "整行平移" for u in changed)
    lt = next(x for x in r.lines if x.line_id == line)
    assert (lt.start_ms, lt.end_ms) == (s0 + 250, e0 + 250)
    # start and end: the line is stretched onto the new span, proportions kept
    changed = edits.retime_line(r, line, s0, s0 + 2 * (e0 - s0))
    assert [(u.start_ms, u.end_ms) for u in changed] == [(s0 + 2 * (a - s0), s0 + 2 * (b - s0)) for a, b in before]
    assert changed[0].manual.note == "整行伸缩"
    with pytest.raises(edits.EditError):
        edits.retime_line(r, line, 5000, 4000)
    h.save()
    # over HTTP: one call, the changed units back (the workspace keeps a project in a folder named by its id)
    import shutil

    pid = h.project.id
    root = tmp_path / "root"
    root.mkdir()
    shutil.move(str(h.dir), str(root / pid))
    client = TestClient(create_app(root))
    res = client.post(f"/api/projects/{pid}/results/{r.id}/lines/{line}/retime", json={"start_ms": s0})
    assert res.status_code == 200, res.text
    got = res.json()["units"]
    assert [(u["start_ms"], u["end_ms"]) for u in got] == [(s0 + 2 * (a - s0), s0 + 2 * (b - s0))
                                                          for a, b in before]
    assert client.post(f"/api/projects/{pid}/results/{r.id}/lines/{line}/retime",
                       json={"start_ms": 10, "end_ms": 5}).status_code == 400


def test_retime_units_moves_a_selection_across_lines(tmp_path):
    from kara_align.project import edits

    h = _project(tmp_path, "plain")
    r = S.run_align(h)
    # the last unit of the first line and the first two of the second: one selection
    l1, l2 = [ln.id for ln in h.project.lyrics.sung_lines()[:2]]
    sel = [u for u in r.units if u.line_id == l1][-1:] + [u for u in r.units if u.line_id == l2][:2]
    before = {u.unit_id: (u.start_ms, u.end_ms) for u in sel}
    others = {u.unit_id: (u.start_ms, u.end_ms) for u in r.units if u.unit_id not in before}
    s0 = min(a for a, _ in before.values())
    changed = edits.retime_units(r, list(before), s0 - 40, None)
    assert {u.unit_id: (u.start_ms, u.end_ms) for u in changed} == {k: (a - 40, b - 40) for k, (a, b) in before.items()}
    assert all(u.locked and u.manual.note == "平移" for u in changed)
    assert {u.unit_id: (u.start_ms, u.end_ms) for u in r.units if u.unit_id in others} == others  # the rest stays
    # both lines' ranges follow
    for lid in (l1, l2):
        lt = next(x for x in r.lines if x.line_id == lid)
        us = [u for u in r.units if u.line_id == lid]
        assert (lt.start_ms, lt.end_ms) == (min(u.start_ms for u in us), max(u.end_ms for u in us))
    with pytest.raises(edits.EditError):
        edits.retime_units(r, ["nope"], 0, None)


def test_local_rerun_is_partial_and_adoptable(tmp_path):
    h = _project(tmp_path, "plain")
    full = S.run_align(h)
    second = h.project.lyrics.sung_lines()[1].id
    part = S.run_align(h, line_ids=[second])
    assert not part.coverage.full and part.parent_result_id == full.id
    assert h.project.active_result_id == full.id  # never overwrites
    S.adopt_lines(h, full.id, [second], from_result_id=part.id)
    assert "adopted" in next(u for u in full.units if u.line_id == second).flags


def test_ai_roundtrip(tmp_path):
    h = S.create_dir(tmp_path / "p", "t", "plain")
    pv = S.parse_lyrics(h, "君と歩いた\n", origin="paste")
    S.apply_lyrics(h, pv["preview_id"])
    out = S.ai_prompt(h)
    assert out["snapshot_id"] in out["prompt"]
    line = h.project.lyrics.lines[0]
    patch = {"format": "kara-align/reading-patch", "version": 1, "snapshot": out["snapshot_id"],
             "lines": [{"id": line.id, "text": line.text, "segments": [
                 {"surface": "君", "reading": "きみ", "units": ["き", "み"]},
                 {"surface": "と", "reading": "と", "units": ["と"]},
                 {"surface": "歩", "reading": "ある", "units": ["あ", "る"]},
                 {"surface": "いた", "reading": "いた", "units": ["い", "た"]}]}]}
    reply = "好的：\n```json\n" + json.dumps(patch, ensure_ascii=False) + "\n```"
    val = S.ai_validate(h, reply)
    assert val["report"]["lines"][0]["status"] == "ok", val
    S.ai_apply(h, val["report_id"])
    readings = [u.reading for u in h.project.lyrics.lines[0].units()]
    assert readings == ["き", "み", "と", "あ", "る", "い", "た"]
    assert h.project.ai_roundtrips[-1].status == "applied"
    # time keys are rejected
    bad = dict(patch)
    bad["lines"] = [dict(patch["lines"][0], start_ms=100)]
    val2 = S.ai_validate(h, json.dumps(bad, ensure_ascii=False))
    assert not val2["report"]["ok"]


def test_exports(tmp_path):
    h = _project(tmp_path, "plain")
    S.run_align(h)
    for fmt in ("alignment", "prepared", "project", "csv", "lrc-line", "lrc-unit"):
        out = S.export(h, fmt)
        assert out.content
    data = json.loads(S.export(h, "alignment").content)
    assert data["format"] == "kara-align/alignment" and data["units"][0]["start_ms"] is not None
    # LRC exports follow the karaoke lead time (150 ms by default); alignment.json keeps the real times
    assert "[00:00.85]" in S.export(h, "lrc-line").content


# ---------------------------------------------------------------------------
# HTTP API
# ---------------------------------------------------------------------------


def _wait(client, job):
    for _ in range(400):
        j = client.get(f"/api/jobs/{job['id']}").json()
        if j["status"] in ("succeeded", "failed", "cancelled"):
            return j
        time.sleep(0.02)
    raise AssertionError("job did not finish")


def test_http_api_flow(tmp_path):
    from fastapi.testclient import TestClient

    from kara_align.web.server import create_app

    client = TestClient(create_app(tmp_path / "projects"))
    assert client.get("/api/info").status_code == 200
    pv = client.post("/api/projects", json={"name": "web", "mode": "lrc"}).json()
    pid = pv["project"]["id"]
    client.patch(f"/api/projects/{pid}", json={"config": {"backend": "scripted"}})

    # LRC mode without times → explicit error, not a silent downgrade
    bad = client.post(f"/api/projects/{pid}/lyrics/parse", json={"text": "きみと\n", "origin": "paste"}).json()
    assert bad["error"] and bad["preview_id"] is None

    prev = client.post(f"/api/projects/{pid}/lyrics/parse",
                       json={"text": LRC, "origin": "upload", "filename": "a.lrc"}).json()
    view = client.post(f"/api/projects/{pid}/lyrics/apply", json={"preview_id": prev["preview_id"]}).json()
    assert len(view["view"]["effective_starts"]) == 2

    wav = _wav(tmp_path / "s.wav")
    with open(wav, "rb") as f:
        r = client.post(f"/api/projects/{pid}/audio", files={"file": ("s.wav", f, "audio/wav")},
                        data={"role": "original"})
    assert r.status_code == 200, r.text
    asset_id = r.json()["view"]["audio"]["original"]["asset_id"]
    assert client.get(f"/api/projects/{pid}/audio/{asset_id}/playback.wav").status_code == 200
    pk = client.get(f"/api/projects/{pid}/audio/{asset_id}/peaks?per_second=100").json()
    assert abs(len(pk["maxs"]) - 600) <= 2

    line_id = view["project"]["lyrics"]["lines"][0]["id"]
    v = client.post(f"/api/projects/{pid}/calibration/mark", json={"line_id": line_id, "marked_ms": 1000}).json()
    assert v["project"]["calibration"]["user_shift_ms"] == 0

    job = client.post(f"/api/projects/{pid}/align", json={}).json()
    done = _wait(client, job)
    assert done["status"] == "succeeded", done
    rid = done["output"]["result_id"]
    res = client.get(f"/api/projects/{pid}/results/{rid}").json()
    uid = res["units"][0]["unit_id"]
    ut = client.put(f"/api/projects/{pid}/results/{rid}/units/{uid}",
                    json={"start_ms": 995, "end_ms": 1205, "locked": True}).json()
    assert ut["start_ms"] == 995 and ut["model_start_ms"] is not None
    bad_edit = client.put(f"/api/projects/{pid}/results/{rid}/units/{uid}", json={"start_ms": 1300, "end_ms": 1200})
    assert bad_edit.status_code == 400
    ut = client.post(f"/api/projects/{pid}/results/{rid}/units/{uid}/restore", json={"manual": None}).json()
    assert ut["manual"] is None

    exp = client.get(f"/api/projects/{pid}/export/lrc-unit").json()
    assert exp["content"].startswith("[ti:test]")
    dl = client.get(f"/api/projects/{pid}/export/alignment?download=1")
    assert "attachment" in dl.headers["content-disposition"]

    # mixing without stems is refused clearly
    assert client.post(f"/api/projects/{pid}/mix/export", json={"vocal_keep_pct": 20}).status_code == 400
    # the UI is served
    assert client.get("/").status_code in (200, 404)


def test_prepared_and_result_json_roundtrip(tmp_path):
    h = _project(tmp_path, "plain")
    r = S.run_align(h)
    prepared = S.export(h, "prepared").content
    alignment = S.export(h, "alignment").content
    h2 = S.create_dir(tmp_path / "p2", "t2", "plain")
    pv = S.parse_lyrics(h2, prepared, origin="upload", filename="prepared.json")
    assert pv["detected"] == "json-prepared" and pv["error"] is None
    S.apply_lyrics(h2, pv["preview_id"])
    assert [u.id for u in h2.project.lyrics.lines[0].units()] == [u.id for u in h.project.lyrics.lines[0].units()]
    S.add_audio(h2, tmp_path / "song.wav", "original")
    imported = S.import_result_json(h2, alignment)
    assert imported.units[0].start_ms == r.units[0].start_ms
    # routing hints for non-lyrics JSON
    assert S.parse_lyrics(h2, alignment)["route"] == "json-alignment"


def test_separation_stores_each_stems_own_sync_report(tmp_path, monkeypatch):
    from kara_align.audio import separation as sep_mod

    h = _project(tmp_path, "plain")

    def fake_separate(src, out_dir, preset, cancel=None, progress=None, device="auto", stall_s=None):
        v, i = out_dir / "v.wav", out_dir / "i.wav"
        _wav(v)
        _wav(i)
        report = {"model_filename": "m.ckpt", "audio_separator_version": "0.30.2",
                  "sync": {"vocals": {"role": "vocals", "ok": True, "lag_ms": 0.0},
                           "instrumental": {"role": "instrumental", "ok": False, "lag_ms": 0.0}}}
        return sep_mod.SeparationOutput(v, i, report)

    monkeypatch.setattr(sep_mod, "separate", fake_separate)
    S.run_separation(h, "melband-roformer")
    reports = {a.role: a.sync_report for a in h.project.audio if a.role != "original"}
    assert reports["vocals"]["role"] == "vocals" and reports["vocals"]["ok"] is True
    assert reports["instrumental"]["role"] == "instrumental" and reports["instrumental"]["ok"] is False


def test_calibration_checks_and_mismatch_warning(tmp_path):
    h = _project(tmp_path, "lrc")
    first, second = [ln.id for ln in h.project.lyrics.sung_lines()]
    S.calibration_op(h, "mark", line_id=first, marked_ms=1000)
    S.calibration_op(h, "check", line_id=second, marked_ms=3040)
    cal = S.open_dir(h.dir).project.calibration  # persisted and loadable
    assert [(c.line_id, c.residual_ms) for c in cal.checks] == [(second, 40)]
    assert not S.project_view(h)["view"]["calibration_issues"]
    S.calibration_op(h, "check", line_id=second, marked_ms=3900)  # replaces, now way off
    view = S.project_view(h)["view"]
    assert len(h.project.calibration.checks) == 1
    assert any(i["severity"] == "warning" for i in view["calibration_issues"])
