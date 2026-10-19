#!/usr/bin/env python3
"""Two-UID Linux smoke for the Facade-owned artifact writer boundary."""
from __future__ import annotations

import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import signal
import sqlite3
import stat
import subprocess
import sys
import time
import urllib.request

APP = Path("/app")
SMOKE = APP / "facade_runtime_smoke.py"
STATE = Path("/var/lib/amusant-results")
DB = STATE / "facade-ledger.sqlite3"
ARTIFACTS = STATE / "artifacts"
OWNER_KEY = "two-uid-smoke-owner-key"
HERMES_KEY = "two-uid-smoke-hermes-key"
WRITER_KEY = "two-uid-smoke-writer-key"
SESSION_ID = "amusant-session-two-uid-runtime-smoke-1234567890"
TASK_ID = "task-two-uid-runtime-smoke"
RUN_ID = "run_two_uid_runtime_smoke_123"
REPORT = "Synthetic private report committed through the writer channel."
PRESENTATION = json.dumps({
    "schema_version": 1,
    "speak": "The synthetic report is ready.",
    "card": {"title": "Report ready", "lines": ["One bounded summary."]},
    "details_md": "Short legacy detail.",
}, ensure_ascii=False, separators=(",", ":"))


def _demote(uid: int, gid: int):
    os.setgroups([20000])
    os.setgid(gid)
    os.setuid(uid)


def _child(args: list[str], *, uid: int, gid: int, env: dict[str, str]):
    result = subprocess.run(
        [sys.executable, "-B", str(SMOKE), *args],
        env=env,
        text=True,
        capture_output=True,
        timeout=15,
        preexec_fn=lambda: _demote(uid, gid),
    )
    if result.returncode:
        raise RuntimeError(
            f"UID {uid} child failed ({result.returncode}): "
            + (result.stderr[-1200:] or result.stdout[-1200:])
        )
    return result.stdout.strip()


def _write_secret(path: Path, value: str):
    path.write_text(value + "\n", encoding="utf-8")
    os.chown(path, 0, 20000)
    os.chmod(path, 0o440)


def _runtime_env(secret_dir: Path):
    return {
        **os.environ,
        "PYTHONPATH": str(APP),
        "PYTHONDONTWRITEBYTECODE": "1",
        "AMUSANT_RESULT_STATE_DIR": str(STATE),
        "AMUSANT_FACADE_API_KEY_FILE": str(secret_dir / "facade.key"),
        "AMUSANT_HERMES_API_KEY_FILE": str(secret_dir / "hermes.key"),
        "AMUSANT_FACADE_WRITER_API_KEY_FILE": str(secret_dir / "writer.key"),
        "AMUSANT_FACADE_BIND_IP": "127.0.0.1",
        "AMUSANT_FACADE_WRITER_BIND_IP": "127.0.0.1",
        "AMUSANT_FACADE_WRITER_URL": "http://127.0.0.1:8644",
        "API_SERVER_KEY": HERMES_KEY,
    }


def _start_facade(env):
    proc = subprocess.Popen(
        [sys.executable, "-B", str(APP / "facade.py")],
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        preexec_fn=lambda: _demote(10003, 10003),
    )
    for _ in range(100):
        if proc.poll() is not None:
            stdout, stderr = proc.communicate(timeout=1)
            raise RuntimeError("facade exited during startup: " + (stderr[-1200:] or stdout[-1200:]))
        try:
            with urllib.request.urlopen("http://127.0.0.1:8643/health", timeout=1) as response:
                if response.status == 200:
                    return proc
        except Exception:
            time.sleep(0.05)
    proc.terminate()
    raise RuntimeError("facade did not become ready")


def _stop_facade(proc):
    if proc.poll() is None:
        proc.send_signal(signal.SIGTERM)
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=2)


def _seed_as_facade(env):
    return _child(["seed"], uid=10003, gid=10003, env=env)


def _plugin_call(env):
    return json.loads(_child(["plugin"], uid=10000, gid=10000, env=env))


def _prove_hermes_isolation(env):
    return json.loads(_child(["attack"], uid=10000, gid=10000, env=env))


def _read_as_facade(env):
    return json.loads(_child(["read"], uid=10003, gid=10003, env=env))


def _check_permissions():
    db = DB.stat()
    artifact_dir = ARTIFACTS.stat()
    if db.st_uid != 10003 or stat.S_IMODE(db.st_mode) != 0o600:
        raise RuntimeError("facade database owner/mode is not private")
    if artifact_dir.st_uid != 10003 or stat.S_IMODE(artifact_dir.st_mode) != 0o700:
        raise RuntimeError("artifact directory owner/mode is not private")
    for suffix in ("-wal", "-shm"):
        path = Path(str(DB) + suffix)
        if path.exists():
            info = path.stat()
            if info.st_uid != 10003 or stat.S_IMODE(info.st_mode) != 0o600:
                raise RuntimeError("facade SQLite sidecar owner/mode is not private")


def _check_report_permissions(reference: str):
    if not re.fullmatch(r"art_[A-Za-z0-9_-]{20,80}", reference):
        raise RuntimeError("facade returned an invalid artifact reference")
    path = ARTIFACTS / (reference + ".report")
    info = path.stat()
    if info.st_uid != 10003 or stat.S_IMODE(info.st_mode) != 0o600:
        raise RuntimeError("report file owner/mode is not private")


def _seed_mode():
    import facade

    os.umask(0o077)
    facade.initialize()
    owner_scope = facade.owner_scope_from_header(OWNER_KEY)
    body_hash = hashlib.sha256(b"synthetic-input").hexdigest()
    with facade.connect() as conn:
        conn.execute(
            """INSERT INTO requests
               (owner_scope, task_id, session_id, body_hash, run_id, submit_state,
                artifact_status, upstream_post_count, created_at)
               VALUES (?, ?, ?, ?, ?, 'accepted', 'pending', 1, 1.0)""",
            (owner_scope, TASK_ID, SESSION_ID, body_hash, RUN_ID),
        )
    print(json.dumps({"seeded": True}, separators=(",", ":")))


def _plugin_mode():
    from hermes_plugin import save_report, transform_llm_output

    committed = json.loads(save_report({"report": REPORT}, session_id=SESSION_ID))
    output = transform_llm_output(PRESENTATION, session_id=SESSION_ID)
    if not isinstance(output, str):
        raise RuntimeError("plugin output hook did not receive a projected envelope")
    print(json.dumps({"committed": committed, "output": output}, separators=(",", ":")))


def _attack_mode():
    conn = http.client.HTTPConnection("127.0.0.1", 8644, timeout=3)
    forged = json.dumps(
        {"report": "forged", "artifact_ref": "art_attacker_supplied_reference"},
        separators=(",", ":"),
    ).encode("utf-8")
    try:
        conn.request(
            "POST",
            "/internal/v1/artifacts",
            body=forged,
            headers={
                "Authorization": "Bearer " + WRITER_KEY,
                "X-Amusant-Session-ID": SESSION_ID,
                "Content-Type": "application/json",
            },
        )
        response = conn.getresponse()
        raw = response.read(8193)
        status = response.status
    finally:
        conn.close()
    if status != 400 or b"art_attacker_supplied_reference" in raw:
        raise RuntimeError("writer accepted or echoed a caller-supplied artifact reference")

    binding_mutation = "denied"
    try:
        with sqlite3.connect(f"file:{DB}?mode=rw", uri=True, timeout=1) as db:
            db.execute(
                "UPDATE requests SET owner_scope=?, task_id=?, run_id=? WHERE session_id=?",
                ("attacker-owner", "attacker-task", "run_attacker", SESSION_ID),
            )
            db.commit()
        binding_mutation = "allowed"
    except (OSError, sqlite3.Error):
        pass
    if binding_mutation != "denied":
        raise RuntimeError("Hermes UID mutated the Facade-owned request binding")

    artifact_write = "denied"
    try:
        (ARTIFACTS / "art_attacker_supplied_reference.report").write_bytes(b"forged")
        artifact_write = "allowed"
    except OSError:
        pass
    if artifact_write != "denied":
        raise RuntimeError("Hermes UID wrote into the Facade artifact directory")

    database_unlink = "denied"
    try:
        DB.unlink()
        database_unlink = "allowed"
    except OSError:
        pass
    if database_unlink != "denied":
        raise RuntimeError("Hermes UID removed the Facade-owned ledger")

    print(json.dumps({
        "writer_rejected_forged_ref": True,
        "binding_mutation": binding_mutation,
        "artifact_write": artifact_write,
        "database_unlink": database_unlink,
    }, separators=(",", ":")))


def _read_mode():
    import facade
    from hermes_plugin.reprojection import _load_committed_artifact

    row = facade.request_by_session(SESSION_ID)
    artifact, state = _load_committed_artifact(row or {})
    if not artifact or state != "ready":
        raise RuntimeError("Facade could not read the committed artifact after restart")
    print(json.dumps({
        "status": state,
        "owner_scope": row["owner_scope"],
        "task_id": row["task_id"],
        "run_id": row["run_id"],
        "artifact_ref": artifact["artifact_ref"],
        "sha256": artifact["sha256"],
        "byte_length": artifact["byte_length"],
    }, separators=(",", ":")))


def main():
    if len(sys.argv) > 1:
        mode = sys.argv[1]
        {"seed": _seed_mode, "plugin": _plugin_mode, "attack": _attack_mode, "read": _read_mode}[mode]()
        return 0

    secret_dir = Path("/tmp/facade-smoke-secrets")
    secret_dir.mkdir(mode=0o700)
    os.chown(secret_dir, 0, 20000)
    os.chmod(secret_dir, 0o710)
    _write_secret(secret_dir / "facade.key", OWNER_KEY)
    _write_secret(secret_dir / "hermes.key", HERMES_KEY)
    _write_secret(secret_dir / "writer.key", WRITER_KEY)
    env = _runtime_env(secret_dir)

    facade = _start_facade(env)
    try:
        _seed_as_facade(env)
        _check_permissions()
        isolation = _prove_hermes_isolation(env)
        if isolation != {
            "writer_rejected_forged_ref": True,
            "binding_mutation": "denied",
            "artifact_write": "denied",
            "database_unlink": "denied",
        }:
            raise RuntimeError("Hermes writer isolation evidence did not match")

        first = _plugin_call(env)
        if first["committed"] != {"committed": True}:
            raise RuntimeError("plugin report commit acknowledgement was invalid")
        first_envelope = json.loads(first["output"])
        reference = first_envelope.get("artifact_ref")
        _check_report_permissions(reference)
        if REPORT.encode("utf-8") != (ARTIFACTS / (reference + ".report")).read_bytes():
            raise RuntimeError("committed artifact bytes did not match the synthetic report")
        first_ref = reference
    finally:
        _stop_facade(facade)

    facade = _start_facade(env)
    try:
        second = _plugin_call(env)
        if second["committed"] != {"committed": True}:
            raise RuntimeError("writer replay after restart was not idempotent")
        second_envelope = json.loads(second["output"])
        if second_envelope.get("artifact_ref") != first_ref:
            raise RuntimeError("restart changed the Facade-minted artifact reference")
        readback = _read_as_facade(env)
        if (
            readback.get("status") != "ready"
            or readback.get("task_id") != TASK_ID
            or readback.get("run_id") != RUN_ID
            or readback.get("sha256") != hashlib.sha256(REPORT.encode("utf-8")).hexdigest()
            or readback.get("byte_length") != len(REPORT.encode("utf-8"))
        ):
            raise RuntimeError("Facade restart readback did not match the durable report")
        _check_permissions()
    finally:
        _stop_facade(facade)

    print(json.dumps({
        "facade_uid": 10003,
        "plugin_uid": 10000,
        "restart_readback": "passed",
        "binding_mutation": "denied",
        "forged_reference": "rejected",
        "database_unlink": "denied",
    }, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"facade two-UID runtime smoke failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise
