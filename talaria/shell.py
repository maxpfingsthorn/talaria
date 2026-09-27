from __future__ import annotations

import subprocess
from dataclasses import dataclass


@dataclass
class Result:
    returncode: int
    stdout: str = ""
    stderr: str = ""


class CommandError(Exception):
    def __init__(self, argv, result: Result):
        self.argv = list(argv)
        self.result = result
        tail = (result.stderr or result.stdout).strip().splitlines()[-5:]
        super().__init__(f"{' '.join(self.argv[:3])} … exited {result.returncode}: "
                         + " | ".join(tail))


class Shell:
    def run(self, argv, *, input: str | None = None, check: bool = True,
            timeout: float | None = None) -> Result:
        argv = [str(a) for a in argv]
        p = subprocess.run(argv, input=input, capture_output=True, text=True,
                           timeout=timeout)
        r = Result(p.returncode, p.stdout, p.stderr)
        if check and r.returncode != 0:
            raise CommandError(argv, r)
        return r
