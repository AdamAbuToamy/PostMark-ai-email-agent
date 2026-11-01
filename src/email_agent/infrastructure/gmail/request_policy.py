"""Cross-process request pacing shared by every Gmail client in this install."""
import fcntl
import json
import random
import time
from contextlib import contextmanager
from email.utils import parsedate_to_datetime
from pathlib import Path

from googleapiclient.errors import HttpError

DATA = Path(__file__).resolve().parents[4] / "data"


class SyncBusyError(RuntimeError):
    """Another sync already owns this account."""


@contextmanager
def sync_lock(account_id: str, directory: Path = DATA):
    if account_id not in ("personal", "university"):
        raise ValueError("Unknown account")
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / f"sync-{account_id}.lock").open("a+") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise SyncBusyError("Account synchronization already running") from exc
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


class RequestGate:
    # Global gate is deliberately stricter than a per-account gate, including
    # when two local account aliases refer to the same Gmail user.
    def __init__(self, path=DATA / "gmail-requests.lock", clock=time.time, sleep=time.sleep):
        self.path, self.clock, self.sleep = Path(path), clock, sleep

    @contextmanager
    def request(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a+") as handle:
            deadline = self.clock() + 120
            while True:
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if self.clock() >= deadline:
                        raise TimeoutError("Gmail request gate busy; retry later")
                    self.sleep(0.1)
            try:
                handle.seek(0)
                raw = handle.read()
                due = float(raw) if raw else 0
                delay = max(0, due - self.clock())
                if delay > 60:
                    raise TimeoutError("Gmail quota cooldown active; retry later")
                self.sleep(delay)
                self._store(handle, self.clock() + 0.7)
                yield handle
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def _store(self, handle, due):
        handle.seek(0)
        handle.truncate()
        handle.write(str(due))
        handle.flush()

    def defer(self, handle, seconds):
        self._store(handle, self.clock() + seconds)


def retry_delay(error, attempt):
    if isinstance(error, HttpError):
        status = int(error.resp.status)
        try:
            reasons = {e.get("reason") for e in json.loads(error.content).get("error", {}).get("errors", [])}
        except (ValueError, TypeError, AttributeError):
            reasons = set()
        if status not in (429, 500, 502, 503, 504) and not (
            status == 403 and reasons & {"rateLimitExceeded", "userRateLimitExceeded"}
        ):
            return None
        header = error.resp.get("retry-after", "0")
        try:
            retry_after = float(header)
        except (ValueError, TypeError):
            try:
                retry_after = parsedate_to_datetime(header).timestamp() - time.time()
            except (ValueError, TypeError, OverflowError):
                retry_after = 0
    elif isinstance(error, (TimeoutError, ConnectionError)):
        retry_after = 0
    else:
        return None
    return max(retry_after, min(2 ** (attempt + 1), 60) + random.random())


def execute(request, gate, attempts=6, before_attempt=None):
    for attempt in range(attempts):
        with gate.request() as handle:
            try:
                # Disable SDK retries so all attempts pass through this gate.
                if before_attempt is not None:before_attempt()
                return request.execute(num_retries=0)
            except (HttpError, TimeoutError, ConnectionError) as exc:
                delay = retry_delay(exc, attempt)
                if delay is None:
                    raise
                gate.defer(handle, delay)
                if attempt == attempts - 1 or delay > 60:
                    raise
