from __future__ import annotations

import importlib.util
import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .runtime import activate_vendored_quickjs


@dataclass
class JSConfig:
    python: str = sys.executable

    # Maximum wall-clock time allowed for one JS operation.
    #
    # IMPORTANT:
    # This is enforced by the parent process / subprocess boundary.
    # We deliberately do NOT use quickjs.Context.set_time_limit(), because
    # this worker uses a synchronous JS -> Python callback (__safe_emit).
    timeout_seconds: float = 15.0

    memory_mb: int = 128

    # Safety limits for the JSON protocol.
    max_output_line: int = 250_000
    max_message_bytes: int = 1_000_000


class JSBackend:
    def start(self, snapshot: list[dict], source: str, location: str = "about:blank") -> None:
        raise NotImplementedError

    def event(self, event: dict) -> None:
        raise NotImplementedError

    def timer(self, timer_id: int, interval: bool = False) -> None:
        raise NotImplementedError

    def close(self, force: bool = False) -> None:
        raise NotImplementedError

    def poll(self, timeout: float = 0.0) -> list[dict]:
        raise NotImplementedError


class QuickJSNGWorker(JSBackend):
    """
    Runs quickjs-ng in a dedicated child process.

    Only JSON messages cross the process boundary.

    Safety model
    ------------
    - JavaScript executes in a separate OS process.
    - The child has OS resource limits.
    - The Python quickjs Context does NOT use set_time_limit().
      This is intentional because JS -> Python callbacks are used.
    - The parent tracks a per-operation deadline.
    - A timed-out worker is killed rather than reused.
    - Events/timers received after worker death are discarded instead of
      generating a second cascade of "worker is not running" errors.

    Protocol requirement
    --------------------
    For event/timer operations, js_worker.py MUST emit:

        {"op":"done","requestId":N}

    in a finally-style completion path after the corresponding JS operation
    returns to the Python worker loop. Without this acknowledgment the parent
    watchdog correctly assumes the child is hung and eventually kills it.

    The existing "ready", "error", timer, console, update, etc. messages are
    otherwise unchanged.
    """

    def __init__(self, config: JSConfig | None = None):
        self.config = config or JSConfig()

        self.proc: subprocess.Popen[str] | None = None

        self.queue: queue.Queue[dict] = queue.Queue()

        self.reader: threading.Thread | None = None
        self.stderr_reader: threading.Thread | None = None

        self.tmpdir: tempfile.TemporaryDirectory[str] | None = None

        self.dead = True

        # Deadline for the currently outstanding event/timer operation.
        self._deadline: float | None = None

        # Monotonically increasing request id.
        self._next_request_id = 1

        # Request currently waiting for completion.
        self._pending_request_id: int | None = None

        # Messages received before the initial "ready".
        self._startup_messages: list[dict] = []

        # Protects stdin and state transitions.
        self._lock = threading.RLock()

    @staticmethod
    def available() -> bool:
        activate_vendored_quickjs()
        return importlib.util.find_spec("quickjs") is not None

    @staticmethod
    def discover() -> str | None:
        return "python:quickjs-ng" if QuickJSNGWorker.available() else None

    # ------------------------------------------------------------------
    # Process lifecycle
    # ------------------------------------------------------------------

    def start(self, snapshot: list[dict], source: str, location: str = "about:blank") -> None:
        """
        Start a fresh worker and load the supplied document.

        If an old worker is still attached to this object, it is forcefully
        closed first. We do not try to reuse a partially-failed JS context.
        """
        if not self.available():
            raise RuntimeError(
                "JavaScript support is not installed. Install it with:\n"
                "python -m pip install 'quickjs-ng>=0.17.0.1,<0.18'"
            )

        # Never reuse an old process after failure/close.
        if self.proc is not None:
            self.close(force=True)

        worker = Path(__file__).with_name("js_worker.py")

        if not worker.is_file():
            raise RuntimeError(f"JavaScript worker file not found: {worker}")

        # Fresh protocol state for every process.
        self.queue = queue.Queue()
        self._startup_messages = []
        self._deadline = None
        self._pending_request_id = None
        self._next_request_id = 1
        self.dead = False

        self.tmpdir = tempfile.TemporaryDirectory(
            prefix="WebCat-js-"
        )

        kwargs: dict[str, Any] = {
            "stdin": subprocess.PIPE,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "text": True,
            "bufsize": 1,
            "cwd": self.tmpdir.name,
            "env": self._clean_env(),
        }

        if os.name != "nt":
            try:
                import resource

                memory = max(16, int(self.config.memory_mb)) * 1024 * 1024

                # CPU time is deliberately a little larger than the
                # wall-clock JS timeout because process startup/import work
                # also consumes CPU.
                timeout = max(0.1, float(self.config.timeout_seconds))
                cpu = max(1, int(timeout) + 2)

                def limit_resources() -> None:
                    resource.setrlimit(
                        resource.RLIMIT_CPU,
                        (cpu, cpu + 1),
                    )

                    resource.setrlimit(
                        resource.RLIMIT_AS,
                        (memory, memory),
                    )

                    resource.setrlimit(
                        resource.RLIMIT_FSIZE,
                        (2 * 1024 * 1024, 2 * 1024 * 1024),
                    )

                    resource.setrlimit(
                        resource.RLIMIT_NOFILE,
                        (32, 32),
                    )

                kwargs["preexec_fn"] = limit_resources

            except Exception:
                # Keep existing portability behavior:
                # if resource limits cannot be installed, the worker still
                # starts and the parent-side protocol limits remain active.
                pass

        try:
            proc = subprocess.Popen(
                [self.config.python, str(worker)],
                **kwargs,
            )
        except Exception:
            self.close(force=True)
            raise

        self.proc = proc

        # Capture stdout/stderr using separate threads so stderr cannot fill
        # its pipe and deadlock the child while stdout is being processed.
        self.reader = threading.Thread(
            target=self._read_stdout_loop,
            args=(proc,),
            daemon=True,
            name="WebCat-js-stdout",
        )
        self.reader.start()

        self.stderr_reader = threading.Thread(
            target=self._read_stderr_loop,
            args=(proc,),
            daemon=True,
            name="WebCat-js-stderr",
        )
        self.stderr_reader.start()

        try:
            self._send(
                {
                    "op": "load",
                    "snapshot": snapshot,
                    "source": source,
                    "location": location,
                    "memory": self.config.memory_mb,
                    "timeout": self.config.timeout_seconds,
                }
            )

            self._wait_ready()

        except Exception:
            self.close(force=True)
            raise

    def _clean_env(self) -> dict[str, str]:
        """
        Construct the intentionally restricted environment used by the JS
        worker.

        This preserves the original behavior: Python itself and its extension
        loading environment remain available, while common proxy/cloud
        credential variables and PYTHONPATH are removed.
        """
        env = dict(os.environ)

        for key in list(env):
            lower = key.lower()

            if lower in {
                "http_proxy",
                "https_proxy",
                "all_proxy",
                "no_proxy",
            }:
                env.pop(key, None)
                continue

            if lower.startswith(("aws_", "gcloud_", "azure_")):
                env.pop(key, None)

        env["SAFEHTMLTK_JS_WORKER"] = "1"

        # Do not let the child inherit arbitrary project/user Python modules.
        env.pop("PYTHONPATH", None)

        return env

    # ------------------------------------------------------------------
    # Child output
    # ------------------------------------------------------------------

    def _read_stdout_loop(
        self,
        proc: subprocess.Popen[str],
    ) -> None:
        stdout = proc.stdout

        try:
            if stdout is not None:
                for line in stdout:
                    if len(line) > self.config.max_output_line:
                        self.queue.put(
                            {
                                "op": "error",
                                "value": (
                                    "JavaScript worker output line "
                                    "exceeded limit"
                                ),
                            }
                        )
                        continue

                    try:
                        msg = json.loads(line)

                        if not isinstance(msg, dict):
                            self.queue.put(
                                {
                                    "op": "error",
                                    "value": (
                                        "Invalid JS worker output: "
                                        "message is not an object"
                                    ),
                                }
                            )
                            continue

                        # "done" is deliberately kept in the normal queue.
                        # poll() uses it to clear the operation deadline.
                        self.queue.put(msg)

                    except Exception as exc:
                        self.queue.put(
                            {
                                "op": "error",
                                "value": f"Invalid JS worker output: {exc}",
                            }
                        )
        finally:
            with self._lock:
                # Only mark the worker dead if this is still the active
                # process. An old reader thread must never mark a newly
                # started worker as dead.
                if self.proc is proc:
                    self.dead = True

    def _read_stderr_loop(
        self,
        proc: subprocess.Popen[str],
    ) -> None:
        stderr = proc.stderr

        if stderr is None:
            return

        try:
            err = stderr.read().strip()
        except Exception:
            err = ""

        if err:
            self.queue.put(
                {
                    "op": "worker_stderr",
                    "value": err[-10_000:],
                }
            )

    # ------------------------------------------------------------------
    # Protocol
    # ------------------------------------------------------------------

    def _is_running(self) -> bool:
        with self._lock:
            proc = self.proc

            if proc is None:
                return False

            if self.dead:
                return False

            if proc.poll() is not None:
                self.dead = True
                return False

            return True

    def _send(self, obj: dict) -> None:
        data = json.dumps(
            obj,
            separators=(",", ":"),
        )

        if len(data.encode("utf-8")) > self.config.max_message_bytes:
            raise RuntimeError("JavaScript message too large")

        with self._lock:
            proc = self.proc

            if proc is None or self.dead:
                raise RuntimeError("JavaScript worker is not running")

            if proc.poll() is not None:
                self.dead = True
                raise RuntimeError("JavaScript worker has exited")

            stdin = proc.stdin

            if stdin is None:
                self.dead = True
                raise RuntimeError("JavaScript worker stdin is unavailable")

            try:
                stdin.write(data + "\n")
                stdin.flush()
            except (BrokenPipeError, OSError, ValueError) as exc:
                self.dead = True
                raise RuntimeError(
                    f"JavaScript worker stopped: {exc}"
                ) from exc

    def _wait_ready(self) -> None:
        timeout = max(
            0.1,
            float(self.config.timeout_seconds),
        )

        # Preserve the original startup allowance:
        # configured execution timeout + 1 second.
        deadline = time.monotonic() + timeout + 1.0

        while True:
            remaining = deadline - time.monotonic()

            if remaining <= 0:
                self.close(force=True)
                raise TimeoutError("JavaScript startup timed out")

            try:
                msg = self.queue.get(
                    timeout=min(0.05, remaining)
                )
            except queue.Empty:
                if not self._is_running():
                    self.close(force=True)
                    raise RuntimeError(
                        "quickjs-ng worker exited during startup"
                    )
                continue

            op = msg.get("op")

            if op == "ready":
                self._startup_messages.clear()
                return

            if op == "error":
                value = msg.get(
                    "value",
                    "JavaScript startup failed",
                )
                self.close(force=True)
                raise RuntimeError(str(value))

            self._startup_messages.append(msg)

    # ------------------------------------------------------------------
    # Public operations
    # ------------------------------------------------------------------

    def _begin_operation(self) -> int | None:
        """
        Start the parent-side deadline for a JS event/timer.

        Returns None when the worker is already dead.

        A single quickjs-ng worker processes stdin sequentially, so there
        should normally be one outstanding operation at a time. If callers
        submit another operation before the previous one is acknowledged,
        this method deliberately resets the deadline for the newest request.
        """
        with self._lock:
            if not self._is_running():
                return None

            request_id = self._next_request_id
            self._next_request_id += 1

            self._pending_request_id = request_id
            self._deadline = (
                time.monotonic()
                + max(0.1, float(self.config.timeout_seconds))
            )

            return request_id

    def event(self, event: dict) -> None:
        request_id = self._begin_operation()

        # After a timeout the old JS context is intentionally unusable.
        # Do not turn one failure into dozens of secondary exceptions.
        if request_id is None:
            return

        try:
            self._send(
                {
                    "op": "event",
                    "event": event,
                    "requestId": request_id,
                }
            )
        except Exception:
            with self._lock:
                self._deadline = None
                self._pending_request_id = None
            raise

    def timer(
        self,
        timer_id: int,
        interval: bool = False,
    ) -> None:
        request_id = self._begin_operation()

        # See event(): once the worker is dead, discard later timer messages.
        if request_id is None:
            return

        try:
            self._send(
                {
                    "op": "timer",
                    "timerId": int(timer_id),
                    "interval": bool(interval),
                    "requestId": request_id,
                }
            )
        except Exception:
            with self._lock:
                self._deadline = None
                self._pending_request_id = None
            raise


    def network_response(self, fetch_request_id: int, response: dict) -> None:
        """Deliver an asynchronous network result back to the JS Promise layer."""
        operation_id = self._begin_operation()
        if operation_id is None:
            return
        try:
            self._send({
                "op": "network_response",
                "requestId": operation_id,
                "fetchRequestId": int(fetch_request_id),
                **response,
            })
        except Exception:
            with self._lock:
                self._deadline = None
                self._pending_request_id = None
            raise

    # ------------------------------------------------------------------
    # Polling / timeout handling
    # ------------------------------------------------------------------

    def _process_messages(
        self,
        messages: list[dict],
    ) -> None:
        """
        Apply protocol state transitions to already-received messages.
        """
        completed_request: int | None = None

        for msg in messages:
            op = msg.get("op")

            if op == "done":
                request_id = msg.get("requestId")

                if (
                    request_id is not None
                    and request_id == self._pending_request_id
                ):
                    completed_request = int(request_id)

        if completed_request is not None:
            with self._lock:
                if self._pending_request_id == completed_request:
                    self._pending_request_id = None
                    self._deadline = None

    def _drain_queue(self) -> list[dict]:
        messages: list[dict] = []

        while True:
            try:
                messages.append(self.queue.get_nowait())
            except queue.Empty:
                break

        return messages

    def poll(self, timeout: float = 0.0) -> list[dict]:
        """
        Return currently available worker messages.

        Timeout handling is deliberately conservative:

        1. Drain already-queued output first.
        2. Process "done" acknowledgements.
        3. Only kill the worker when the operation is still pending and the
           deadline has actually expired.

        This avoids the old race where a completed operation's queued output
        was present but poll() checked the deadline first and killed the
        worker anyway.
        """
        out: list[dict] = []

        # Messages accumulated before "ready".
        if self._startup_messages:
            out.extend(self._startup_messages)
            self._startup_messages.clear()

        # First consume everything already received.
        out.extend(self._drain_queue())

        self._process_messages(out)

        # If an error has already arrived, the operation is over from the
        # parent's perspective. Do not turn it into a timeout afterwards.
        if any(m.get("op") == "error" for m in out):
            with self._lock:
                self._deadline = None
                self._pending_request_id = None

        # Re-check state after consuming the queue. This ordering is
        # important: "done" may have been waiting in the queue.
        with self._lock:
            deadline = self._deadline
            pending = self._pending_request_id
            proc = self.proc
            dead = self.dead

        if (
            pending is not None
            and deadline is not None
            and time.monotonic() >= deadline
        ):
            # Give the queue one last chance in case the completion message
            # arrived concurrently with the deadline check.
            late = self._drain_queue()

            if late:
                out.extend(late)
                self._process_messages(late)

                with self._lock:
                    still_pending = (
                        self._pending_request_id is not None
                        and self._deadline is not None
                    )

                if not still_pending:
                    return out

            # The operation really exceeded its wall-clock budget.
            if proc is not None and not dead and proc.poll() is None:
                self.close(force=True)

            else:
                with self._lock:
                    self.dead = True
                    self._deadline = None
                    self._pending_request_id = None

            out.append(
                {
                    "op": "error",
                    "value": "JavaScript execution timed out",
                }
            )

            return out

        # If the worker disappeared independently, report it once.
        if not self._is_running():
            already_reported = any(
                m.get("op") == "error"
                for m in out
            )

            if not already_reported:
                out.append(
                    {
                        "op": "error",
                        "value": "quickjs-ng worker exited",
                    }
                )

            return out

        # Optional blocking wait for new output.
        wait_for = max(0.0, float(timeout))

        with self._lock:
            deadline = self._deadline
            pending = self._pending_request_id

        if (
            pending is not None
            and deadline is not None
        ):
            remaining = deadline - time.monotonic()

            if remaining <= 0:
                # Re-enter through the same timeout path.
                return out + self.poll(timeout=0.0)

            if wait_for <= 0:
                wait_for = 0.0
            else:
                wait_for = min(wait_for, remaining)

        if wait_for > 0:
            try:
                first = self.queue.get(timeout=wait_for)
                out.append(first)
            except queue.Empty:
                # No output during the requested wait. Re-run timeout/death
                # checks without blocking.
                out.extend(self._handle_post_wait_state())
                return out

        else:
            try:
                out.append(self.queue.get_nowait())
            except queue.Empty:
                return out

        # Drain messages that were produced together with the first one.
        out.extend(self._drain_queue())

        self._process_messages(out)

        if any(m.get("op") == "error" for m in out):
            with self._lock:
                self._deadline = None
                self._pending_request_id = None

        # A completion message may have arrived during the wait.
        # Therefore timeout is checked only after processing the queue.
        out.extend(self._handle_post_wait_state())

        return out

    def _handle_post_wait_state(self) -> list[dict]:
        """
        Handle timeout / child-exit state after a poll wait.
        """
        out: list[dict] = []

        # Give output that raced with the state check priority.
        raced = self._drain_queue()

        if raced:
            out.extend(raced)
            self._process_messages(raced)

            if any(m.get("op") == "error" for m in raced):
                with self._lock:
                    self._deadline = None
                    self._pending_request_id = None
                return out

        with self._lock:
            deadline = self._deadline
            pending = self._pending_request_id
            proc = self.proc
            dead = self.dead

        # Completed operation: nothing else to do.
        if pending is None or deadline is None:
            return out

        # Child died before acknowledging completion.
        if proc is None or dead or proc.poll() is not None:
            with self._lock:
                self.dead = True
                self._deadline = None
                self._pending_request_id = None

            out.append(
                {
                    "op": "error",
                    "value": "quickjs-ng worker exited",
                }
            )
            return out

        # Deadline expired: kill the isolated process.
        if time.monotonic() >= deadline:
            self.close(force=True)

            out.append(
                {
                    "op": "error",
                    "value": "JavaScript execution timed out",
                }
            )

        return out

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------

    def close(self, force: bool = False) -> None:
        """
        Shut down the child process.

        A timed-out process is never reused. This is intentional: once an
        operation has exceeded its limit, the safest assumption is that the
        JS runtime is no longer in a trustworthy state for continued use.
        """
        with self._lock:
            proc = self.proc

            # Clear the public process reference first. This prevents another
            # caller from racing into _send() while shutdown is happening.
            self.proc = None
            self.dead = True
            self._deadline = None
            self._pending_request_id = None

        if proc is not None:
            try:
                if force:
                    proc.kill()
                else:
                    proc.terminate()

                proc.wait(timeout=1)

            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass

                try:
                    proc.wait(timeout=1)
                except Exception:
                    pass

        tmpdir = self.tmpdir
        self.tmpdir = None

        if tmpdir is not None:
            try:
                tmpdir.cleanup()
            except Exception:
                pass


QJSWorker = QuickJSNGWorker


def available_backend(
    config: JSConfig | None = None,
) -> QuickJSNGWorker | None:
    if not QuickJSNGWorker.available():
        return None

    return QuickJSNGWorker(config)
