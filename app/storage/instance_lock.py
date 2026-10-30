import os
from contextlib import ExitStack
from pathlib import Path


class InstanceAlreadyRunning(RuntimeError):
    pass


class InstanceLock:
    """OS-released lock: no stale PID heuristics and no concurrent writers."""
    def __init__(self, root: Path):
        root.mkdir(parents=True, exist_ok=True)
        self.path = root / ".agent.lock"
        self.file = None

    def __enter__(self):
        self.file = self.path.open("a+b")
        self.file.seek(0, os.SEEK_END)
        if self.file.tell() == 0:
            self.file.write(b"0")
            self.file.flush()
        self.file.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.file.close()
            self.file = None
            raise InstanceAlreadyRunning("이 저장 폴더를 사용하는 앱이 이미 실행 중입니다.") from None
        return self

    def __exit__(self, *_):
        if self.file:
            self.file.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file, fcntl.LOCK_UN)
            self.file.close()


class RecordingRootsLock:
    """Protect previous locations too, before history/recovery can inspect them."""
    def __init__(self, current, previous):
        self.current = Path(current).resolve()
        self.previous = previous
        self.stack = ExitStack()

    def __enter__(self):
        try:
            self.stack.enter_context(InstanceLock(self.current))
            for root in sorted(set(Path(p).resolve() for p in self.previous)):
                if root != self.current and root.is_dir():
                    self.stack.enter_context(InstanceLock(root))
        except BaseException:
            self.stack.close()
            raise
        return self

    def __exit__(self, *args):
        return self.stack.__exit__(*args)
