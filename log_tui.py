#!/usr/bin/env python3
"""
Terminal Log Viewer (curses-based) for multi-service Docker Compose deployments.

Purpose:
- View and follow logs for four microservices via Docker or host file fallback.
- Lightweight, single-file, standard library only; works well over SSH.

Configure:
- Edit the SERVICES mapping below to reflect your service names and optional host log paths.

Run:
- python3 log_tui.py

Security note:
- This tool reads logs via the Docker CLI or host files. Do not run as root unless necessary.
- If Docker access requires sudo on your system, consider configuring your user for Docker access,
  or run this tool with appropriate privileges. The UI will display helpful messages when Docker
  is unavailable or access is denied.
"""

import curses
import curses.textpad
import os
import re
import shlex
import signal
import subprocess
import threading
import time
from collections import deque
from typing import Deque, List, Optional, Tuple, Dict

# =========================
# Top-of-file CONFIG
# =========================
SERVICES: Dict[str, Tuple[str, str, str]] = {
    "1": ("edge-gateway", "edge-gateway", "/var/log/edge-gateway.log"),
    "2": ("user-management", "qr-table-order-api-user-management-1", "/var/log/user-management.log"),
    "3": ("restaurant-management", "qr-table-order-api-restaurant-management-1", "/var/log/restaurant-management.log"),
    "4": ("integration-management", "qr-table-order-api-integration-management-1", "/var/log/integration-management.log"),
}

# =========================
# Constants
# =========================
MAX_LINES = 5000
DEFAULT_TAIL = 500
FOLLOW_POLL_INTERVAL = 0.25
IDLE_UI_SLEEP = 0.05

LEVELS = ["ANY", "DEBUG", "INFO", "WARN", "ERROR", "CRITICAL"]


# =========================
# Utilities
# =========================
REMOTE_SSH = os.environ.get("LOG_TUI_SSH", "").strip()
REMOTE_SSH_OPTS = shlex.split(os.environ.get("LOG_TUI_SSH_OPTS", "")) if REMOTE_SSH else []
DOCKER_SUDO = os.environ.get("LOG_TUI_DOCKER_SUDO", "0").strip() in ("1", "true", "yes", "on")
FILE_SUDO = os.environ.get("LOG_TUI_FILE_SUDO", "0").strip() in ("1", "true", "yes", "on")
USE_DOCKER_JSON = os.environ.get("LOG_TUI_USE_DOCKER_JSON", "0").strip() in ("1", "true", "yes", "on")

def is_remote() -> bool:
    return bool(REMOTE_SSH)

def wrap_cmd_for_remote(local_args: List[str]) -> List[str]:
    if not is_remote():
        return local_args
    # Use '--' to separate ssh options from remote command args
    return ["ssh", *REMOTE_SSH_OPTS, REMOTE_SSH, "--", *local_args]

def docker_cmd(args: List[str]) -> List[str]:
    # Ensure 'docker' is the first element
    if not args or args[0] != "docker":
        args = ["docker", *args]
    if DOCKER_SUDO:
        # Use non-interactive sudo to avoid hanging
        args = ["sudo", "-n", *args]
    return wrap_cmd_for_remote(args)

def need_file_sudo(path: str) -> bool:
    # Docker JSON logs typically require sudo
    return FILE_SUDO or path.startswith("/var/lib/docker/containers/")

ANSI_ESCAPE_RE = re.compile(r"\x1B\[[0-?]*[ -/]*[@-~]")

def sanitize_line(s: str) -> str:
    # Strip ANSI sequences
    s = ANSI_ESCAPE_RE.sub("", s)
    # Replace tabs with spaces for alignment
    s = s.replace("\t", "    ")
    # Remove other control characters except standard whitespace
    s = "".join(ch for ch in s if ch == "\n" or ch == "\r" or ch == "\t" or (32 <= ord(ch) <= 126) or (ord(ch) >= 160))
    return s

def safe_truncate(s: str, width: int) -> str:
    if width <= 0:
        return ""
    if len(s) <= width:
        return s
    if width <= 1:
        return s[:width]
    return s[: width - 1] + "…"


def run_cmd_capture(args: List[str], timeout: Optional[float] = None) -> Tuple[int, str, str]:
    try:
        proc = subprocess.Popen(wrap_cmd_for_remote(args), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    except FileNotFoundError as e:
        return (127, "", f"{e}")
    try:
        out, err = proc.communicate(timeout=timeout)
        return (proc.returncode, out, err)
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
        except Exception:
            pass
        return (124, "", "Command timed out")


def tail_lines(path: str, n: int) -> Tuple[List[str], Optional[str]]:
    """
    Read last n lines from file efficiently by seeking from the end.
    Returns (lines, error_message). error_message is None on success.
    """
    # In remote mode, use 'tail' over SSH to avoid copying files
    if is_remote():
        args = (["sudo", "-n"] if need_file_sudo(path) else []) + ["tail", "-n", str(max(0, n)), path]
        code, out, err = run_cmd_capture(args)
        if code == 0:
            lines = [sanitize_line(l) for l in out.splitlines()]
            return lines[-n:], None
        msg = err.strip() or f"tail exited {code}"
        if "No such file" in err:
            msg = f"File not found: {path}"
        elif "Permission denied" in err:
            msg = f"Permission denied reading: {path}"
        return [], f"[ssh] {msg}"

    if n <= 0:
        return [], None
    try:
        with open(path, "rb") as f:
            # Read in blocks from the end until we have enough lines
            avg_line_len = 100
            to_read = n * avg_line_len
            file_size = f.seek(0, os.SEEK_END)
            pos = file_size
            blocks: List[bytes] = []
            while pos > 0 and len(b"\n".join(blocks).splitlines()) <= n:
                read_size = min(to_read, pos)
                pos -= read_size
                f.seek(pos)
                data = f.read(read_size)
                blocks.insert(0, data)
                if to_read < file_size:
                    to_read *= 2
            content = b"".join(blocks)
            lines = content.splitlines()
            tail = lines[-n:] if len(lines) >= n else lines
            # Decode robustly
            text_lines = [sanitize_line(l.decode("utf-8", errors="replace")) for l in tail]
            return text_lines, None
    except FileNotFoundError:
        return [], f"File not found: {path}"
    except PermissionError:
        return [], f"Permission denied reading: {path}"
    except IsADirectoryError:
        return [], f"Is a directory, not a file: {path}"
    except Exception as e:
        return [], f"Error reading {path}: {e}"


def is_docker_available() -> Tuple[bool, Optional[str]]:
    try:
        proc = subprocess.Popen(docker_cmd(["docker", "--version"]), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    except FileNotFoundError:
        code, out, err = 127, "", "docker not found"
    except Exception as e:
        code, out, err = 1, "", str(e)
    else:
        out, err = proc.communicate(timeout=5)
        code = proc.returncode
    if code == 0:
        return True, None
    where = "remote host" if is_remote() else "local system"
    msg = f"Docker CLI not available on {where}. Consider installing Docker or using sudo/root if required."
    if code == 127:
        msg = f"Docker CLI not found in PATH on {where}. Install Docker or adjust PATH."
    elif (err or "").lower().find("permission denied") != -1:
        msg = f"Docker permission denied on {where}. Add user to docker group or run with sudo."
    return False, msg


def _list_docker_container_names() -> Tuple[List[str], Optional[str]]:
    args = docker_cmd(["docker", "ps", "--format", "{{.Names}}"])
    try:
        proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        out, err = proc.communicate(timeout=10)
        if proc.returncode == 0:
            names = [ln.strip() for ln in out.splitlines() if ln.strip()]
            return names, None
        return [], err.strip() or f"docker ps exited {proc.returncode}"
    except Exception as e:
        return [], str(e)

def _best_match_container(preferred: str, friendly: str) -> Optional[str]:
    names, _ = _list_docker_container_names()
    if not names:
        return None
    tokens = [preferred, preferred.replace("-", "_"), friendly, friendly.replace(" ", "-"), friendly.replace(" ", "_")]
    tokens = [t for t in tokens if t]
    # Exact match first
    for n in names:
        if n == preferred:
            return n
    # Contains-based heuristics
    lower_names = [(n, n.lower()) for n in names]
    for t in tokens:
        lt = t.lower()
        for orig, low in lower_names:
            if lt and lt in low:
                return orig
    return names[0] if names else None

def _get_container_id(resolved_name: str) -> Optional[str]:
    args = docker_cmd(["docker", "ps", "-a", "--format", "{{.ID}}\t{{.Names}}"])
    try:
        proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        out, err = proc.communicate(timeout=10)
        if proc.returncode != 0:
            return None
        for line in out.splitlines():
            parts = line.strip().split("\t", 1)
            if len(parts) == 2:
                cid, name = parts
                if name == resolved_name:
                    return cid
        return None
    except Exception:
        return None

def _docker_json_log_path(container_id: str) -> str:
    return f"/var/lib/docker/containers/{container_id}/{container_id}-json.log"

def docker_logs_tail(service: str, n: int, friendly_name: Optional[str] = None) -> Tuple[List[str], Optional[str], str]:
    """
    Get last n lines via docker logs without follow.
    Returns (lines, error_message, resolved_container_name)
    """
    container = service
    args = docker_cmd(["docker", "logs", "--since", "0s", "--tail", str(max(0, n)), container])
    try:
        proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        out, err = proc.communicate(timeout=20)
        code = proc.returncode
    except Exception as e:
        code, out, err = 1, "", str(e)
    if code == 0:
        lines = [sanitize_line(l) for l in out.splitlines()]
        return lines[-n:], None, container
    # Try to provide helpful message
    err = err or ""
    # If container not found, try discovery once
    if "No such container" in err or "is not running" in err or code != 0:
        candidate = _best_match_container(service, friendly_name or service)
        if candidate and candidate != service:
            args = docker_cmd(["docker", "logs", "--since", "0s", "--tail", str(max(0, n)), candidate])
            try:
                proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                out, err = proc.communicate(timeout=20)
                code = proc.returncode
            except Exception as e:
                code, out, err = 1, "", str(e)
            if code == 0:
                lines = [sanitize_line(l) for l in out.splitlines()]
                return lines[-n:], None, candidate

    err_msg = err.strip() or f"docker logs exited with code {code}."
    if "No such container" in err:
        err_msg = f"Container '{service}' not found. Is it created/running?"
    elif "is not running" in err:
        err_msg = f"Container '{service}' is not running."
    elif code == 127:
        err_msg = "Docker CLI not found. Install Docker or adjust PATH."
    elif err.lower().find("permission denied") != -1:
        err_msg = "Docker permission denied. Try sudo or add user to docker group."
    return [], err_msg, container


# =========================
# Log Model
# =========================
class LogModel:
    def __init__(self, name: str, docker_service: str, fallback_path: str):
        self.name = name
        self.docker_service = docker_service
        self.fallback_path = fallback_path

        self.tail_n = DEFAULT_TAIL
        self.follow_enabled = False

        self.level_filter = "ANY"  # one of LEVELS
        self.text_filter = ""

        self.lines: Deque[str] = deque(maxlen=MAX_LINES)
        self.lock = threading.Lock()

        # Runtime follow artifacts
        self._follow_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._source_type = "unknown"  # "docker" or "file" or "unknown"
        self._source_id = ""          # container name or file path (with remote prefix if any)

        # Docker subprocess when following
        self._docker_proc: Optional[subprocess.Popen] = None

    @property
    def source_type(self) -> str:
        return self._source_type

    @property
    def source_id(self) -> str:
        return self._source_id

    def append_message(self, msg: str) -> None:
        with self.lock:
            self.lines.append(msg)

    def set_tail(self, n: int) -> None:
        self.tail_n = max(0, n)

    def set_level(self, level: str) -> None:
        level = (level or "ANY").upper()
        if level not in LEVELS:
            level = "ANY"
        self.level_filter = level

    def set_text_filter(self, text: str) -> None:
        self.text_filter = text or ""

    def _clear_lines(self) -> None:
        with self.lock:
            self.lines.clear()

    def load(self) -> None:
        """
        Load last tail_n lines using docker if available; fallback to file.
        Does not start follow mode.
        """
        self.stop_follow()  # ensure clean state
        self._clear_lines()

        # Try docker first
        docker_ok, docker_msg = is_docker_available()
        used_docker = False
        if docker_ok:
            data, err, resolved = docker_logs_tail(self.docker_service, self.tail_n, self.name)
            if err is None:
                used_docker = True
                with self.lock:
                    for ln in data:
                        self.lines.append(ln)
                self._source_type = "docker"
                self._source_id = (f"{REMOTE_SSH}:" if is_remote() else "") + resolved
            else:
                # fall back to file, but include error message
                self.append_message(f"[docker] {err}")
        else:
            if docker_msg:
                self.append_message(f"[docker] {docker_msg}")

        if not used_docker:
            # If requested, try Docker JSON logs (remote) before fallback_path
            if is_remote() and USE_DOCKER_JSON:
                candidate = _best_match_container(self.docker_service, self.name)
                cid = _get_container_id(candidate) if candidate else None
                if cid:
                    dj_path = _docker_json_log_path(cid)
                    data, ferr = tail_lines(dj_path, self.tail_n)
                    if not ferr:
                        with self.lock:
                            for ln in data:
                                self.lines.append(ln)
                        self._source_type = "file"
                        self._source_id = (f"{REMOTE_SSH}:" if is_remote() else "") + dj_path
                        self.append_message(f"[info] Using docker JSON log file {dj_path}")
                        # Show tail loaded message
                        self.append_message(f"[info] Loaded last {self.tail_n} lines from {self._source_type}: {self._source_id}")
                        return

            data, ferr = tail_lines(self.fallback_path, self.tail_n)
            if ferr:
                self.append_message(f"[file] {ferr}")
            else:
                with self.lock:
                    for ln in data:
                        self.lines.append(ln)
            self._source_type = "file"
            self._source_id = (f"{REMOTE_SSH}:" if is_remote() else "") + self.fallback_path

        # Show tail loaded message
        self.append_message(f"[info] Loaded last {self.tail_n} lines from {self._source_type}: {self._source_id}")

    # ========= Follow Management =========
    def start_follow(self) -> None:
        """
        Start follow mode for the currently selected best source (docker preferred).
        """
        if self._follow_thread and self._follow_thread.is_alive():
            return
        self._stop_event.clear()
        self.follow_enabled = True

        # Choose source: prefer docker when available and container is running
        docker_ok, _ = is_docker_available()
        if docker_ok:
            # We'll attempt docker follow; if it fails, fallback within the thread
            self._source_type = "docker"
            self._source_id = (f"{REMOTE_SSH}:" if is_remote() else "") + self.docker_service
            t = threading.Thread(target=self._follow_docker_thread, name=f"follow-docker-{self.docker_service}", daemon=True)
            self._follow_thread = t
            t.start()
            return

        # Fallback to file
        self._source_type = "file"
        self._source_id = (f"{REMOTE_SSH}:" if is_remote() else "") + self.fallback_path
        target = self._follow_file_thread_remote if is_remote() else self._follow_file_thread
        t = threading.Thread(target=target, name=f"follow-file-{self.fallback_path}", daemon=True)
        self._follow_thread = t
        t.start()

    def stop_follow(self) -> None:
        self.follow_enabled = False
        self._stop_event.set()
        # Stop docker proc if any
        if self._docker_proc is not None:
            try:
                if self._docker_proc.poll() is None:
                    try:
                        self._docker_proc.terminate()
                    except Exception:
                        pass
                    # Give it a moment; then kill if needed
                    for _ in range(10):
                        if self._docker_proc.poll() is not None:
                            break
                        time.sleep(0.05)
                    if self._docker_proc.poll() is None:
                        try:
                            self._docker_proc.kill()
                        except Exception:
                            pass
            finally:
                self._docker_proc = None

        # Join follow thread
        t = self._follow_thread
        if t is not None and t.is_alive():
            # Don't block indefinitely
            t.join(timeout=1.5)
        self._follow_thread = None

    def toggle_follow(self) -> None:
        if self.follow_enabled:
            self.stop_follow()
        else:
            self.start_follow()

    # ========= Source Threads =========
    def _follow_docker_thread(self) -> None:
        """
        Follow docker logs: 'docker logs -f --since 0s --tail <n> <service>'.
        On failure, fall back to file follow gracefully.
        """
        # resolve container name once before follow
        container = _best_match_container(self.docker_service, self.name) or self.docker_service
        args = [
            "docker",
            "logs",
            "-f",
            "--since",
            "0s",
            "--tail",
            str(max(0, self.tail_n)),
            container,
        ]
        try:
            self._docker_proc = subprocess.Popen(
                docker_cmd(args),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
        except FileNotFoundError:
            self.append_message("[docker] Docker CLI not found. Falling back to file follow.")
            self._source_type = "file"
            self._source_id = (f"{REMOTE_SSH}:" if is_remote() else "") + self.fallback_path
            if is_remote():
                self._follow_file_thread_remote()
            else:
                self._follow_file_thread()
            return
        except Exception as e:
            self.append_message(f"[docker] Failed to start docker logs: {e}. Falling back to file follow.")
            self._source_type = "file"
            self._source_id = (f"{REMOTE_SSH}:" if is_remote() else "") + self.fallback_path
            if is_remote():
                self._follow_file_thread_remote()
            else:
                self._follow_file_thread()
            return

        proc = self._docker_proc
        self.append_message(f"[docker] Following container '{container}' (tail={self.tail_n})")

        stdout = proc.stdout
        stderr = proc.stderr
        if stdout is None:
            self.append_message("[docker] No stdout from docker logs process.")
        if stderr is None:
            self.append_message("[docker] No stderr from docker logs process.")

        # Read loop
        try:
            while not self._stop_event.is_set():
                # Read line non-blocking-ish
                if stdout:
                    line = stdout.readline()
                    if line:
                        with self.lock:
                            self.lines.append(sanitize_line(line.rstrip("\n")))
                    else:
                        # If no line and process exited, break
                        if proc.poll() is not None:
                            break
                else:
                    time.sleep(FOLLOW_POLL_INTERVAL)

            # Drain any remaining stderr for error info if exited unexpectedly
            if proc.poll() is not None and stderr:
                err_tail = stderr.read()
                err_tail = (err_tail or "").strip()
                if err_tail:
                    self.append_message(f"[docker] {err_tail}")
        except Exception as e:
            self.append_message(f"[docker] Error while following: {e}")
        finally:
            # Mark end
            rc = proc.poll()
            if rc is None:
                try:
                    proc.terminate()
                except Exception:
                    pass
            else:
                self.append_message(f"[docker] Follow ended (exit={rc}).")
            self._docker_proc = None

            # If follow is still desired and docker ended, inform user
            if self.follow_enabled and not self._stop_event.is_set():
                self.append_message("[docker] Process ended. You can toggle follow to restart or press 'r' to reload.")

    def _follow_file_thread(self) -> None:
        """
        Follow a regular file with truncation/rotation handling.
        """
        path = self.fallback_path
        self.append_message(f"[file] Following file '{path}' (tail={self.tail_n})")

        last_pos = 0
        file_obj = None

        def open_and_seek_end():
            nonlocal file_obj, last_pos
            try:
                file_obj = open(path, "r", encoding="utf-8", errors="replace")
                file_obj.seek(0, os.SEEK_END)
                last_pos = file_obj.tell()
                return True, None
            except FileNotFoundError:
                return False, f"[file] File not found: {path}"
            except PermissionError:
                return False, f"[file] Permission denied: {path}"
            except IsADirectoryError:
                return False, f"[file] Is a directory, not a file: {path}"
            except Exception as e:
                return False, f"[file] Error opening {path}: {e}"

        ok, err = open_and_seek_end()
        if not ok:
            self.append_message(err or "[file] Unknown error opening file")
            return

        try:
            while not self._stop_event.is_set():
                try:
                    # Detect truncation/rotation
                    try:
                        cur_size = os.path.getsize(path)
                    except Exception:
                        cur_size = None
                    if cur_size is not None and cur_size < last_pos:
                        # file truncated/rotated
                        self.append_message("[file] Detected truncation/rotation. Reopening and seeking end.")
                        try:
                            if file_obj:
                                file_obj.close()
                        except Exception:
                            pass
                        ok, err = open_and_seek_end()
                        if not ok:
                            self.append_message(err or "[file] Reopen failed.")
                            time.sleep(FOLLOW_POLL_INTERVAL)
                            continue

                    # Read new lines
                    assert file_obj is not None
                    line = file_obj.readline()
                    if line:
                        last_pos = file_obj.tell()
                        with self.lock:
                            self.lines.append(sanitize_line(line.rstrip("\n")))
                    else:
                        time.sleep(FOLLOW_POLL_INTERVAL)
                except Exception as e:
                    self.append_message(f"[file] Error while following: {e}")
                    time.sleep(FOLLOW_POLL_INTERVAL)
        finally:
            try:
                if file_obj:
                    file_obj.close()
            except Exception:
                pass
            if self.follow_enabled and not self._stop_event.is_set():
                self.append_message("[file] Follow ended unexpectedly. Toggle follow to restart.")

    def _follow_file_thread_remote(self) -> None:
        """
        Follow a remote file using 'tail -n <N> -F <path>' over SSH.
        """
        path = self.fallback_path
        self._follow_file_thread_remote_path(path)
        
    def _follow_file_thread_remote_path(self, path: str, label_prefix: str = "[file][ssh]") -> None:
        self.append_message(f"{label_prefix} Following file '{REMOTE_SSH}:{path}' (tail={self.tail_n})")
        base = (["sudo", "-n"] if need_file_sudo(path) else [])
        args = base + ["tail", "-n", str(max(0, self.tail_n)), "-F", path]
        try:
            proc = subprocess.Popen(
                wrap_cmd_for_remote(args),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
        except Exception as e:
            self.append_message(f"{label_prefix} Failed to start remote tail: {e}")
            return

        self._docker_proc = None  # ensure separated
        try:
            stdout = proc.stdout
            stderr = proc.stderr
            while not self._stop_event.is_set():
                if stdout:
                    line = stdout.readline()
                    if line:
                        with self.lock:
                            self.lines.append(sanitize_line(line.rstrip("\n")))
                    else:
                        if proc.poll() is not None:
                            break
                else:
                    time.sleep(FOLLOW_POLL_INTERVAL)
            if proc.poll() is not None and stderr:
                err_tail = (stderr.read() or "").strip()
                if err_tail:
                    self.append_message(f"{label_prefix} {err_tail}")
        except Exception as e:
            self.append_message(f"{label_prefix} Error while following: {e}")
        finally:
            rc = proc.poll()
            if rc is None:
                try:
                    proc.terminate()
                except Exception:
                    pass
            else:
                self.append_message(f"{label_prefix} Follow ended (exit={rc}).")
            if self.follow_enabled and not self._stop_event.is_set():
                self.append_message(f"{label_prefix} Process ended. Toggle follow to restart or press 'r' to reload.")

    # ========= Filtering =========
    def _passes_level_filter(self, line: str) -> bool:
        level = self.level_filter
        if level == "ANY":
            return True
        # Word-boundary regex for token, case-insensitive
        try:
            return re.search(rf"\b{re.escape(level)}\b", line, flags=re.IGNORECASE) is not None
        except re.error:
            return True

    def _passes_text_filter(self, line: str) -> bool:
        if not self.text_filter:
            return True
        return self.text_filter.lower() in line.lower()

    def filtered_lines(self) -> List[str]:
        with self.lock:
            data = list(self.lines)
        if not data:
            return []
        result = []
        for ln in data:
            if self._passes_level_filter(ln) and self._passes_text_filter(ln):
                result.append(ln)
        return result


# =========================
# Curses UI
# =========================
class LogTUI:
    def __init__(self, stdscr):
        self.stdscr = stdscr
        curses.curs_set(0)
        # Colors
        if curses.has_colors():
            curses.start_color()
            curses.use_default_colors()
            try:
                curses.init_pair(1, curses.COLOR_BLACK, curses.COLOR_CYAN)    # header
                curses.init_pair(2, curses.COLOR_BLACK, curses.COLOR_WHITE)   # footer
                curses.init_pair(3, curses.COLOR_YELLOW, -1)                  # status/info
                curses.init_pair(4, curses.COLOR_RED, -1)                     # error
                curses.init_pair(5, curses.COLOR_CYAN, -1)                    # accent
                curses.init_pair(6, curses.COLOR_WHITE, curses.COLOR_BLUE)    # menu highlight
            except Exception:
                pass
        self.stdscr.nodelay(True)
        self.stdscr.keypad(True)

        # Model per service
        self.models: Dict[str, LogModel] = {
            key: LogModel(name, docker, path) for key, (name, docker, path) in SERVICES.items()
        }
        # Allow default service via env LOG_TUI_DEFAULT_KEY (e.g., "2")
        default_key = os.environ.get("LOG_TUI_DEFAULT_KEY", "1")
        self.active_key = default_key if default_key in self.models else ("1" if "1" in self.models else next(iter(self.models.keys())))
        self.active_model = self.models[self.active_key]

        # State
        self.scroll_offset = 0  # 0 means at tail (latest)
        self.status_msg = ""
        self.last_resize = time.time()

        # Initial load
        self.active_model.load()

    def run(self) -> None:
        while True:
            self.draw()
            ch = self.stdscr.getch()
            if ch == -1:
                time.sleep(IDLE_UI_SLEEP)
                continue

            # Handle input
            if ch in (ord('q'), ord('Q')):
                self.shutdown()
                return

            if ch in (ord('1'), ord('2'), ord('3'), ord('4')):
                key = chr(ch)
                if key in self.models:
                    self.switch_service(key)
                else:
                    self.set_status(f"No service configured for key {key}")
                continue

            if ch in (ord('r'), ord('R')):
                self.reload_tail()
                continue

            if ch in (ord('f'), ord('F')):
                self.toggle_follow()
                continue

            if ch == ord('L'):
                self.prompt_level()
                continue

            if ch == ord('/'):
                self.prompt_text_search()
                continue

            if ch in (ord('t'), ord('T')):
                self.prompt_tail()
                continue

            if ch in (ord('+'), ):
                self.bump_tail(50)
                continue

            if ch in (ord('-'), ):
                self.bump_tail(-50)
                continue

            if ch in (ord('g'), ):
                # go to tail/end
                self.scroll_offset = 0
                continue

            if ch in (ord('G'), ):
                # go to top
                self.scroll_offset = 10**9  # large number to clamp later
                continue

            if ch == curses.KEY_UP:
                self.scroll_offset += 1
                continue

            if ch == curses.KEY_DOWN:
                self.scroll_offset = max(0, self.scroll_offset - 1)
                continue

            if ch == curses.KEY_PPAGE:  # PgUp
                height, _ = self.stdscr.getmaxyx()
                body_h = max(1, height - 3)
                self.scroll_offset += body_h
                continue

            if ch == curses.KEY_NPAGE:  # PgDn
                height, _ = self.stdscr.getmaxyx()
                body_h = max(1, height - 3)
                self.scroll_offset = max(0, self.scroll_offset - body_h)
                continue

            if ch == curses.KEY_RESIZE:
                # Handled next draw via getmaxyx; just record
                self.last_resize = time.time()
                continue

            if ch in (ord('c'), ord('C')):
                self.open_container_menu()
                continue

    def shutdown(self) -> None:
        # Stop all follow threads
        for m in self.models.values():
            m.stop_follow()

    def switch_service(self, key: str) -> None:
        if key == self.active_key:
            return
        # Stop current
        self.active_model.stop_follow()
        # Switch
        self.active_key = key
        self.active_model = self.models[self.active_key]
        self.scroll_offset = 0
        self.status_msg = ""
        self.active_model.load()

    def reload_tail(self) -> None:
        was_following = self.active_model.follow_enabled
        self.active_model.stop_follow()
        self.active_model.load()
        if was_following:
            self.active_model.start_follow()

    def toggle_follow(self) -> None:
        self.active_model.toggle_follow()
        # If following and user at tail, keep them at tail as new lines arrive
        # scroll_offset logic is handled in draw

    def prompt_level(self) -> None:
        choice = self.prompt("Level (ANY/DEBUG/INFO/WARN/ERROR/CRITICAL): ").strip().upper()
        if not choice:
            return
        if choice not in LEVELS:
            self.set_status(f"Invalid level: {choice}")
            return
        self.active_model.set_level(choice)
        # Keep view position
        self.set_status(f"Level filter set to {choice}")

    def prompt_text_search(self) -> None:
        text = self.prompt("Search substring (empty to clear): ")
        self.active_model.set_text_filter(text)
        if text:
            self.set_status(f"Text filter set to '{text}'")
        else:
            self.set_status("Text filter cleared")

    def prompt_tail(self) -> None:
        val = self.prompt("Tail lines (integer): ").strip()
        if not val:
            return
        try:
            n = int(val)
            n = max(0, n)
        except ValueError:
            self.set_status(f"Invalid number: {val}")
            return
        self.active_model.set_tail(n)
        self.reload_tail()
        self.set_status(f"Tail set to {n}")

    def bump_tail(self, delta: int) -> None:
        n = max(0, self.active_model.tail_n + delta)
        self.active_model.set_tail(n)
        self.reload_tail()
        self.set_status(f"Tail set to {n}")

    def set_status(self, msg: str) -> None:
        self.status_msg = msg

    def prompt(self, prompt_text: str) -> str:
        # Render prompt on the second last line
        curses.echo()
        try:
            height, width = self.stdscr.getmaxyx()
            prompt_y = max(0, height - 2)
            self.stdscr.move(prompt_y, 0)
            self.stdscr.clrtoeol()
            self.stdscr.addstr(prompt_y, 0, safe_truncate(prompt_text, width - 1))
            self.stdscr.refresh()

            # Create a textbox for input
            input_y = prompt_y
            input_x = len(prompt_text)
            max_input_w = max(1, width - input_x - 1)
            win = curses.newwin(1, max_input_w, input_y, input_x)
            tb = curses.textpad.Textbox(win, insert_mode=True)
            # Read until Enter
            text = tb.edit().strip()
            return text
        finally:
            curses.noecho()

    def draw(self) -> None:
        self.stdscr.erase()
        height, width = self.stdscr.getmaxyx()

        # Header line (row 0)
        model = self.active_model
        remote_prefix = f"remote={REMOTE_SSH} | " if is_remote() else ""
        header = f"[{self.active_key}] {model.name} | {remote_prefix}source={model.source_type}:{model.source_id} | tail={model.tail_n} | follow={'ON' if model.follow_enabled else 'OFF'} | level={model.level_filter} | search='{model.text_filter}'"
        try:
            self.stdscr.addstr(0, 0, safe_truncate(header, width - 1), curses.color_pair(1))
        except Exception:
            self.stdscr.addstr(0, 0, safe_truncate(header, width - 1))

        # Help/footer (last line)
        help_text = "q quit  1-4 svc  c choose-container  r reload  f follow  L level  / search  t tail  +/- tail  ↑/↓ scroll  PgUp/PgDn page  g end  G top"
        try:
            self.stdscr.addstr(height - 1, 0, safe_truncate(help_text, width - 1), curses.color_pair(2))
        except Exception:
            self.stdscr.addstr(height - 1, 0, safe_truncate(help_text, width - 1))

        # Status/prompt line (second last)
        if self.status_msg:
            try:
                self.stdscr.addstr(height - 2, 0, safe_truncate(self.status_msg, width - 1), curses.color_pair(3))
            except Exception:
                self.stdscr.addstr(height - 2, 0, safe_truncate(self.status_msg, width - 1))

        # Body
        body_top = 1
        body_bottom = max(1, height - 3)
        body_h = body_bottom - body_top + 1

        data = model.filtered_lines()

        # Compute start/end indices based on scroll_offset
        # scroll_offset is number of lines from the tail (0 = tail)
        total = len(data)
        # Clamp
        if self.scroll_offset < 0:
            self.scroll_offset = 0
        if self.scroll_offset > max(0, total - 1):
            self.scroll_offset = max(0, total - 1)

        # If following and offset is 0, we auto-stick to bottom; else we show older lines
        end_idx = total - self.scroll_offset
        start_idx = max(0, end_idx - body_h)
        view = data[start_idx:end_idx]

        # If we asked to go to top via large offset, ensure we clamp to beginning
        if self.scroll_offset >= total - 1:
            view = data[:body_h]

        # Render lines
        y = body_top
        for ln in view[-body_h:]:
            # Truncate long lines for display
            self.stdscr.addstr(y, 0, safe_truncate(ln, width - 1))
            y += 1
            if y > body_bottom:
                break

        self.stdscr.refresh()

    # ======= Container chooser =======
    def open_container_menu(self) -> None:
        names, err = _list_docker_container_names()
        if err:
            self.set_status(f"Docker list error: {err}")
            return
        if not names:
            self.set_status("No running containers found")
            return

        # Create centered window
        h, w = self.stdscr.getmaxyx()
        menu_h = min(len(names) + 4, max(8, int(h * 0.6)))
        menu_w = min(max(len(n) for n in names) + 6, max(30, int(w * 0.6)))
        top = max(0, (h - menu_h) // 2)
        left = max(0, (w - menu_w) // 2)
        win = curses.newwin(menu_h, menu_w, top, left)
        win.keypad(True)
        win.border()

        title = "Select Docker Container (↑/↓, Enter, Esc)"
        try:
            win.addstr(0, 2, safe_truncate(title, menu_w - 4), curses.color_pair(5))
        except Exception:
            win.addstr(0, 2, safe_truncate(title, menu_w - 4))

        idx = 0
        offset = 0
        list_area = menu_h - 4
        while True:
            # Render list
            win.erase()
            win.border()
            try:
                win.addstr(0, 2, safe_truncate(title, menu_w - 4), curses.color_pair(5))
            except Exception:
                win.addstr(0, 2, safe_truncate(title, menu_w - 4))

            if idx < offset:
                offset = idx
            if idx >= offset + list_area:
                offset = idx - list_area + 1

            for i in range(list_area):
                j = offset + i
                y = 2 + i
                if j >= len(names):
                    break
                label = names[j]
                if j == idx:
                    try:
                        win.addstr(y, 2, safe_truncate(label, menu_w - 4), curses.color_pair(6))
                    except Exception:
                        win.addstr(y, 2, safe_truncate(label, menu_w - 4), curses.A_REVERSE)
                else:
                    win.addstr(y, 2, safe_truncate(label, menu_w - 4))

            win.refresh()

            key = win.getch()
            if key in (curses.KEY_UP, ord('k')):
                idx = (idx - 1) % len(names)
            elif key in (curses.KEY_DOWN, ord('j')):
                idx = (idx + 1) % len(names)
            elif key in (curses.KEY_ENTER, 10, 13):
                chosen = names[idx]
                # Apply to current service and reload
                self.active_model.stop_follow()
                self.active_model.docker_service = chosen
                self.active_model.load()
                self.set_status(f"Container set to {chosen}")
                break
            elif key in (27, ord('q')):  # Esc or q
                break


def main(stdscr) -> None:
    ui = LogTUI(stdscr)
    try:
        ui.run()
    finally:
        ui.shutdown()


if __name__ == "__main__":
    # Ensure SIGINT interrupts properly in curses context
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    curses.wrapper(main)


