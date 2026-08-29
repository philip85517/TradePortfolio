#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Detach ETFStrategy dashboard from Terminal.")
    parser.add_argument("--python-bin", required=True)
    parser.add_argument("--script", required=True)
    parser.add_argument("--db", required=True)
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", required=True)
    parser.add_argument("--workdir", required=True)
    parser.add_argument("--log-file", required=True)
    parser.add_argument("--pid-file", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    read_fd, write_fd = os.pipe()

    first_pid = os.fork()
    if first_pid > 0:
        os.close(write_fd)
        with os.fdopen(read_fd, "r", encoding="utf-8") as reader:
            payload = reader.read().strip()
        if not payload:
            print("daemonizer did not return a pid", file=sys.stderr)
            return 1
        print(payload)
        return 0

    os.close(read_fd)
    os.setsid()

    second_pid = os.fork()
    if second_pid > 0:
        with os.fdopen(write_fd, "w", encoding="utf-8") as writer:
            writer.write(str(second_pid))
        os._exit(0)

    os.close(write_fd)
    os.chdir(args.workdir)
    os.umask(0o022)

    log_path = Path(args.log_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    pid_path = Path(args.pid_file)
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    pid_path.write_text(str(os.getpid()), encoding="utf-8")

    devnull = os.open(os.devnull, os.O_RDONLY)
    os.dup2(devnull, 0)
    os.close(devnull)

    log_fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    os.dup2(log_fd, 1)
    os.dup2(log_fd, 2)
    if log_fd > 2:
        os.close(log_fd)

    argv = [
        args.python_bin,
        args.script,
        "--db",
        args.db,
        "--host",
        args.host,
        "--port",
        args.port,
    ]
    os.execv(args.python_bin, argv)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
