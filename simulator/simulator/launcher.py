"""Sandbox deployment runtime for payment-service.

The only way code reaches the running service. It accepts exactly one operation:
"deploy this commit", authenticated with a bearer token, and it

1. exports the commit from the demo repository into releases/<sha>/ (git archive),
2. stops the running service and starts the new release,
3. waits for readiness and checks that /health reports the deployed sha,
4. automatically reverts to the previous release on any failure,
5. appends a record to deployments.jsonl.

It runs no shell commands from requests and accepts no free-form input beyond a commit sha.
"""

import asyncio
import hmac
import json
import os
import re
import signal
import subprocess
import sys
import tarfile
import time
from datetime import UTC, datetime
from io import BytesIO

import httpx
import uvicorn
from fastapi import FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, Field, ValidationError

from simulator.paths import (
    APP_LOG,
    DEMO_REPO,
    DEPLOYMENTS,
    LAUNCHER_PORT,
    LOG_DIR,
    PAYMENTS_DB,
    RELEASES,
    SERVICE_HOST,
    SERVICE_NAME,
    SERVICE_PORT,
    STATE,
)

TOKEN = os.environ.get("SIM_LAUNCHER_TOKEN", "")
SCORING_SECONDS = os.environ.get("SIM_SCORING_SECONDS", "0.1")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def git(*args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(DEMO_REPO), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class DeployRequest(BaseModel):
    sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    deploy_id: str = Field(pattern=r"^[0-9]{1,6}$")
    source: str = Field(pattern=r"^[a-z][a-z0-9-]{1,40}$")
    approval_id: str | None = Field(default=None, max_length=120)


class Launcher:
    def __init__(self) -> None:
        self.proc: subprocess.Popen | None = None
        self.current: dict | None = None
        self.lock = asyncio.Lock()

    def _stage(self, sha: str) -> str:
        target = RELEASES / sha
        if not (target / ".release").exists():
            target.mkdir(parents=True, exist_ok=True)
            archive = subprocess.run(
                ["git", "-C", str(DEMO_REPO), "archive", "--format=tar", sha],
                check=True,
                capture_output=True,
            ).stdout
            with tarfile.open(fileobj=BytesIO(archive)) as tar:
                tar.extractall(target, filter="data")
            (target / ".release").write_text(git("rev-parse", f"{sha}^{{tree}}"))
        # Tree check: the staged files are exactly the commit's tree.
        if (target / ".release").read_text() != git("rev-parse", f"{sha}^{{tree}}"):
            raise RuntimeError("staged release does not match the commit tree")
        return str(target)

    def _stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.send_signal(signal.SIGTERM)
            try:
                self.proc.wait(timeout=6)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
        self.proc = None

    def _start(self, release: str, sha: str, deploy_id: str) -> None:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        env = {
            **os.environ,
            "PYTHONPATH": os.path.join(release, "src"),
            "SIM_GIT_SHA": sha,
            "SIM_DEPLOY_ID": deploy_id,
            "SIM_DB_URL": f"sqlite+aiosqlite:///{PAYMENTS_DB}",
            "SIM_SCORING_SECONDS": SCORING_SECONDS,
            "SIM_LOG_FILE": str(APP_LOG),
        }
        self.proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "payment_service.main:app",
                "--host",
                SERVICE_HOST,
                "--port",
                str(SERVICE_PORT),
                "--log-level",
                "warning",
                "--no-access-log",
            ],
            cwd=release,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=open(LOG_DIR / "service.stderr.log", "ab"),
        )

    async def _ready(self, sha: str, timeout: float = 20.0) -> None:
        deadline = time.monotonic() + timeout
        async with httpx.AsyncClient(timeout=2) as http:
            while time.monotonic() < deadline:
                if self.proc and self.proc.poll() is not None:
                    raise RuntimeError(f"service exited with code {self.proc.returncode}")
                try:
                    r = await http.get(f"http://{SERVICE_HOST}:{SERVICE_PORT}/health")
                    if r.status_code == 200:
                        if r.json().get("git_sha") != sha:
                            raise RuntimeError("running service reports a different git sha")
                        return
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(0.3)
        raise RuntimeError(f"readiness timeout after {timeout:.0f}s")

    def _record(self, entry: dict) -> None:
        DEPLOYMENTS.parent.mkdir(parents=True, exist_ok=True)
        with open(DEPLOYMENTS, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")

    def _commit_meta(self, sha: str) -> dict:
        parent = git("rev-parse", f"{sha}^") if git("rev-list", "--count", sha) != "1" else None
        files = git("diff", "--name-only", parent, sha).splitlines() if parent else []
        stat = git("diff", "--shortstat", parent, sha) if parent else ""
        return {
            "parent_sha": parent,
            "commit_message": git("log", "-1", "--format=%s", sha),
            "author": git("log", "-1", "--format=%an", sha),
            "changed_files": files,
            "diff_stat": stat,
        }

    async def deploy(self, req: DeployRequest, record: bool = True) -> dict:
        async with self.lock:
            git("cat-file", "-e", f"{req.sha}^{{commit}}")
            previous = dict(self.current) if self.current else None
            started = now()
            t0 = time.monotonic()
            try:
                release = self._stage(req.sha)
                self._stop()
                self._start(release, req.sha, req.deploy_id)
                await self._ready(req.sha)
                status, error = "succeeded", None
                self.current = {"sha": req.sha, "deploy_id": req.deploy_id}
            except Exception as exc:  # noqa: BLE001 - reported and reverted below
                status, error = "failed", f"{type(exc).__name__}: {exc}"
                self._stop()
                if previous:
                    self._start(
                        self._stage(previous["sha"]), previous["sha"], previous["deploy_id"]
                    )
                    await self._ready(previous["sha"])
                    self.current = previous
            entry = {
                "deploy_id": req.deploy_id,
                "git_sha": req.sha,
                "service": SERVICE_NAME,
                "environment": "staging-sandbox",
                "started_at": started,
                "finished_at": now(),
                "ready_ms": round((time.monotonic() - t0) * 1000),
                "status": status,
                "error": error,
                "source": req.source,
                "approval_id": req.approval_id,
                "reverted_to": previous["sha"] if status == "failed" and previous else None,
                **self._commit_meta(req.sha),
            }
            if record:
                self._record(entry)
            STATE.write_text(json.dumps(self.current or {}))
            return entry


launcher = Launcher()
app = FastAPI(title="sandbox-launcher")


def _auth(authorization: str | None) -> None:
    expected = f"Bearer {TOKEN}"
    if not TOKEN or not hmac.compare_digest(authorization or "", expected):
        raise HTTPException(status_code=401, detail="unauthorized")


@app.post("/deploy")
async def deploy(request: Request, authorization: str | None = Header(default=None)):
    # Authenticate before reading or validating anything the caller sent.
    _auth(authorization)
    try:
        req = DeployRequest.model_validate(await request.json())
    except (ValueError, ValidationError) as exc:
        raise HTTPException(status_code=422, detail="invalid deploy request") from exc
    try:
        return await launcher.deploy(req)
    except subprocess.CalledProcessError as exc:
        raise HTTPException(status_code=400, detail="unknown commit") from exc


@app.get("/status")
async def status():
    running = bool(launcher.proc and launcher.proc.poll() is None)
    return {"current": launcher.current, "running": running}


@app.on_event("startup")
async def _resume() -> None:
    """Restart the last deployed release (without writing a new deployment record)."""
    if STATE.exists():
        state = json.loads(STATE.read_text() or "{}")
        if state.get("sha"):
            await launcher.deploy(
                DeployRequest(sha=state["sha"], deploy_id=state["deploy_id"], source="resume"),
                record=False,
            )


@app.on_event("shutdown")
async def _shutdown() -> None:
    launcher._stop()


if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("SIM_LAUNCHER_TOKEN must be set")
    uvicorn.run(app, host="127.0.0.1", port=LAUNCHER_PORT, log_level="warning")
