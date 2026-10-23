#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""校园网自动登录 —— 本校适配层。

设计思路：**认证协议本身一行都不自己写**。登录、在线判定、断线重连这些容易
踩坑的部分，全部复用成熟开源项目 ``campusnet``（MIT，支持 Dr.COM / 深澜 /
锐捷 / 华为 eportal，零第三方依赖，仓库里 ``campusnet/`` 就是它的源码）。

本文件只做四件事：

1. 把本校门户地址（172.30.255.42:801）和认证方式（新版 eportal portal/login）
   固定下来 —— 上游的自动指纹识别在本校 801 端口认不出来，必须显式指定。
2. 交互式录入账号密码，密码用 Windows DPAPI 加密后单独存放（不进配置文件）。
3. 开机自启：写一个当前用户的注册表启动项，登录后自动进入守护模式。
4. 把每一次调用上游引擎的过程记到日志里，出问题能查。

常用命令::

    python autologin.py --setup      # 首次配置（输入账号密码）
    python autologin.py              # 立即检查并登录一次
    python autologin.py --watch      # 常驻守护（开机自启用的就是它）
    python autologin.py --status     # 看看现在联没联网
    python autologin.py --install    # 安装开机自启
    python autologin.py --uninstall  # 移除开机自启
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import getpass
import io
import ipaddress
import json
import os
import socket
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import dpapi  # noqa: E402  同目录模块，必须在 sys.path 之后导入
from campusnet.config import Config, default_config_path  # noqa: E402
from campusnet.session import Session, local_ip  # noqa: E402

# --------------------------------------------------------------------------- 默认参数
#: 默认认证服务器（本校）。801 是 Dr.COM eportal 的认证端口（80 端口只是门户页面）。
#: 注意：这只是"没配置时的兜底值"，运行时会优先用配置文件里的 portal_ip，
#: 所以别的学校不用改代码，跑一次 `python -m campusnet setup` 或填配置即可。
PORTAL = "http://172.30.255.42:801"
#: 默认认证方式。本校是新版 eportal：GET /eportal/portal/login（JSONP 回调 dr1003）
PROVIDER = "eportal_portal"

APP_NAME = "CampusNetAutoLogin"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"

#: 是否运行在 PyInstaller 打包出来的 exe 里。打包后没有 python.exe 可以起子进程，
#: 上游引擎改为在同一个进程里调用。
FROZEN = bool(getattr(sys, "frozen", False))

CONFIG_PATH = Path(default_config_path())
DATA_DIR = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / APP_NAME
SECRET_PATH = DATA_DIR / "password.dat"
LOG_PATH = DATA_DIR / "autologin.log"
LOCK_PORT = 47311


def portal_of(cfg: Config) -> str:
    """实际使用的门户地址：配置优先，没配置才用默认值。"""
    return (cfg.portal_ip or PORTAL).rstrip("/")


def provider_of(cfg: Config) -> str:
    """实际使用的认证方式；``auto`` / 空串表示交给上游自动识别。"""
    name = (cfg.provider or "").strip()
    return "" if name in ("", "auto") else name


def portal_host(url: str) -> str:
    """从 URL 里取主机名，例如 http://1.2.3.4:801/x -> 1.2.3.4。"""
    return url.split("//", 1)[-1].split("/")[0].split(":")[0]


def portal_port(url: str) -> int:
    """从 URL 里取端口，默认 80。"""
    netloc = url.split("//", 1)[-1].split("/")[0]
    if ":" in netloc:
        try:
            return int(netloc.rsplit(":", 1)[1])
        except ValueError:
            pass
    return 80


# --------------------------------------------------------------------------- 小工具
def say(text: str = "") -> None:
    """pythonw.exe 下 sys.stdout 是 None，直接 print 会炸，统一走这里。

    另外：输出被重定向（管道/任务计划）时 stdout 是 GBK 等窄编码，上游输出里的
    ``✔`` ``•`` 这类符号编不出来。以前直接跳过整行，会让人以为状态行丢了，
    这里改成用 ``?`` 替换 —— 宁可符号变问号，也不能少一整行信息。
    """
    stream = sys.stdout
    if stream is None:
        return
    try:
        print(text)
        return
    except (UnicodeEncodeError, ValueError):
        pass
    encoding = getattr(stream, "encoding", None) or "utf-8"
    try:
        stream.write(text.encode(encoding, "replace").decode(encoding, "replace") + "\n")
        stream.flush()
    except (UnicodeEncodeError, ValueError, OSError):
        pass


def log(message: str) -> None:
    """写日志（追加、带时间戳、超过 512KB 轮转一份）。"""
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        if LOG_PATH.exists() and LOG_PATH.stat().st_size > 512 * 1024:
            LOG_PATH.replace(LOG_PATH.with_name("autologin.log.1"))
        with LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write("[{}] {}\n".format(datetime.now().strftime("%Y-%m-%d %H:%M:%S"), message))
    except OSError:
        pass


def python_executable(console: bool = True) -> str:
    """自启任务要用 pythonw.exe，否则每次开机闪一个黑框。"""
    exe = sys.executable or "python"
    if os.name == "nt" and not console:
        candidate = Path(exe).with_name("pythonw.exe")
        if candidate.exists():
            return str(candidate)
    return exe


def console_python() -> str:
    """调用上游引擎时用带控制台解释器的 python.exe。

    自启时我们是 pythonw.exe（没有 stdout），如果再拿 ``sys.executable`` 去起子进程，
    子进程也没有 stdout，出错时连一句错误都留不下来。这里换成同目录的 python.exe，
    再用 CREATE_NO_WINDOW 保证不弹窗。
    """
    exe = sys.executable or "python"
    if os.name == "nt":
        path = Path(exe)
        if path.name.lower() == "pythonw.exe":
            candidate = path.with_name("python.exe")
            if candidate.exists():
                return str(candidate)
    return exe


def _no_window() -> dict:
    if os.name == "nt":
        return {"creationflags": 0x08000000}  # CREATE_NO_WINDOW
    return {}


# --------------------------------------------------------------------------- 凭据
def load_password() -> str:
    if not SECRET_PATH.is_file():
        return ""
    try:
        raw = SECRET_PATH.read_text(encoding="utf-8").strip()
        return dpapi.unprotect(raw)
    except Exception as exc:  # noqa: BLE001 - 解密失败必须让上层知道
        log("读取已保存密码失败：{}".format(exc))
        return ""


def save_password(password: str) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    payload = dpapi.protect(password)
    SECRET_PATH.write_text(payload + "\n", encoding="utf-8")
    if os.name != "nt":
        try:
            os.chmod(SECRET_PATH, 0o600)
        except OSError:
            pass


def load_config() -> Config:
    try:
        return Config.load(str(CONFIG_PATH))
    except (ValueError, OSError):
        return Config(path=str(CONFIG_PATH))


# --------------------------------------------------------------------------- 调上游引擎
class _CaptureStream(io.StringIO):
    """给上游 CLI 用的假 stdout：能收、能扛 reconfigure，打包后也能拿到输出。"""

    @property
    def encoding(self) -> str:
        return "utf-8"

    def isatty(self) -> bool:
        return False

    def reconfigure(self, **kwargs) -> None:
        return None

    def flush(self) -> None:
        return None


def _run_campusnet_inproc(argv, env_extra=None):
    """在同一个进程里调用上游 CLI（打包成 exe 后没有 python.exe 可起子进程）。"""
    from campusnet import cli as campusnet_cli

    saved = {}
    for key, value in (env_extra or {}).items():
        saved[key] = os.environ.get(key)
        os.environ[key] = str(value)

    buffer = _CaptureStream()
    code = 1
    try:
        with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
            code = campusnet_cli.main(list(argv))
    except SystemExit as exc:  # 上游偶尔会 sys.exit
        code = int(exc.code or 0)
    except Exception as exc:  # noqa: BLE001 - 打包环境里不能把主程序带崩
        return 1, "上游引擎出错：{}: {}".format(type(exc).__name__, exc)
    finally:
        for key, old in saved.items():
            if old is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old
    return int(code or 0), buffer.getvalue().strip()


def call_campusnet(args, env_extra=None, timeout=180, stream=False):
    """执行上游引擎，返回 (返回码, 输出)。

    源码运行时起子进程（输出可与界面隔离）；打包成 exe 后在同进程内调用。
    ``stream=True`` 时边跑边显示输出，避免网络检查期间看起来像卡死。
    """
    if FROZEN:
        return _run_campusnet_inproc(args, env_extra)
    env = os.environ.copy()
    env.update(env_extra or {})
    # 避免外部环境里的学校配置干扰（比如测试时残留的变量）
    for key in ("CAMPUSNET_USERNAME", "CAMPUSNET_PASSWORD"):
        if key not in (env_extra or {}):
            env.pop(key, None)
    # 管道不是终端，子进程默认按 GBK 写；强成 UTF-8 才能被下面按 UTF-8 解回来，
    # 否则中文日志到这儿全是乱码。UNBUFFERED 让它逐行吐，别攒到最后。
    env["PYTHONIOENCODING"] = "utf-8"
    env.setdefault("PYTHONUTF8", "1")
    env["PYTHONUNBUFFERED"] = "1"

    command = [console_python(), "-m", "campusnet"] + [str(a) for a in args]

    if not stream:
        try:
            proc = subprocess.run(
                command,
                cwd=str(ROOT),
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                **_no_window(),
            )
        except subprocess.TimeoutExpired:
            return 1, "执行超时（{} 秒）".format(timeout)
        except OSError as exc:
            return 1, "无法启动上游引擎：{}".format(exc)
        return proc.returncode, ((proc.stdout or "") + (proc.stderr or "")).strip()

    # ---------------- 流式模式 ----------------
    try:
        proc = subprocess.Popen(
            command,
            cwd=str(ROOT),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            **_no_window(),
        )
    except OSError as exc:
        return 1, "无法启动上游引擎：{}".format(exc)

    collected = []
    finished = {}

    def _pump():
        try:
            for line in proc.stdout:  # type: ignore[union-attr]
                line = line.rstrip()
                collected.append(line)
                if line.strip():
                    say("  " + line)
            proc.wait()
            finished["code"] = proc.returncode
        except Exception:  # noqa: BLE001 - 读管道出错也不能把界面带崩
            finished["code"] = 1

    import threading

    worker = threading.Thread(target=_pump, daemon=True)
    worker.start()
    worker.join(timeout)
    if worker.is_alive():
        # 兜底：子进程真的卡住就杀掉，别让用户对着黑屏干等
        try:
            proc.kill()
        except OSError:
            pass
        return 1, "执行超时（{} 秒），已中止".format(timeout)

    try:
        proc.stdout.close()  # type: ignore[union-attr]
    except (OSError, AttributeError):
        pass
    return finished.get("code", 1), "\n".join(collected)


#: 账号格式候选。只有在门户的 loadConfig 查不到时才用：先试 ``,0,学号``，
#: 再退回纯学号。本校门户下发的 account_prefix 是 "1"，即必须带 ``,0,``。
ACCOUNT_PREFIXES = (",0,", "")

#: 只有本机 IP 落在这个网段里才发账号密码，防止连到酒店/伪造热点时把凭据送给
#: 陌生门户。换学校时改这里，或用配置项 allow_cidrs 覆盖（"off" 关闭该保护）。
ALLOW_CIDRS = ("172.30.0.0/16",)

#: 出现这些字样说明账号/密码/欠费有问题，重试没有意义，且可能触发账号锁定。
AUTH_ERROR_KEYWORDS = (
    "密码错误", "密码不正确", "账号或密码", "账号不存在", "用户不存在",
    "已停机", "请充值", "账户余额", "账号已锁定", "认证失败次数",
)


def fetch_portal_config(portal: str = "", timeout: int = 6) -> dict:
    """读门户页面配置（``/eportal/portal/page/loadConfig``）。

    这就是浏览器打开登录页时先拉的那份配置，里面有：
    ``account_prefix``（"1" 表示账号要写成 ``,0,学号``）、``account_suffix``、
    ``login_method``、``ep_http_port`` 等。查不到就返回 ``{}``，调用方退回试错。
    """
    base = (portal or portal_of(load_config())).rstrip("/")
    ip = local_ip()
    if not ip:
        return {}
    params = {
        "program_index": "",
        "wlan_vlan_id": "",
        "wlan_user_ip": base64.b64encode(ip.encode("utf-8")).decode("ascii"),
        "wlan_user_ipv6": "",
        "wlan_user_ssid": "",
        "wlan_user_areaid": "",
        "wlan_ac_ip": "",
        "wlan_ap_mac": "",
        "gw_id": "",
        "callback": "dr1003",
        "jsVersion": "4.X",
    }
    url = "{}/eportal/portal/page/loadConfig?{}".format(base, urlencode(params))
    try:
        resp = Session(timeout=timeout).get(url, headers={"Referer": base + "/"})
        payload = resp.jsonp_payload
        obj = json.loads(payload) if payload else None
    except Exception:  # noqa: BLE001 - 门户不提供该接口时静默降级
        return {}
    data = obj.get("data") if isinstance(obj, dict) else None
    return data if isinstance(data, dict) else {}


def portal_online(portal: str = "", timeout: int = 3) -> bool:
    """问门户自己的状态接口，判断当前 IP 是否已认证。

    这条路不走外网探测、不查 DNS，正常情况下 0.1 秒就有答案，
    比上游那套「三个劫持探测 + 三个内容探测」快得多。
    """
    base = (portal or portal_of(load_config())).rstrip("/")
    host = portal_host(base)
    url = "http://{}/drcom/chkstatus?callback=dr1003&v={}".format(host, int(time.time()))
    try:
        resp = Session(timeout=timeout).get(url, headers={"Referer": base + "/"})
        payload = resp.jsonp_payload
        obj = json.loads(payload) if payload else {}
    except Exception:  # noqa: BLE001 - 门户不通就当作未认证
        return False
    return isinstance(obj, dict) and str(obj.get("result")) == "1"


def internet_quick(timeout: int = 2) -> bool:
    """再确认一次真的能上外网（204 探测），防止门户状态停留在旧会话。"""
    try:
        resp = Session(timeout=timeout).get("http://connect.rom.miui.com/generate_204")
    except Exception:  # noqa: BLE001
        return False
    return int(resp.status) == 204


def network_allowed(ip: str, cfg: Config, portal: str = ""):
    """本机 IP 是否在允许的校园网段内，返回 (是否允许, 原因)。

    没显式配置 ``allow_cidrs`` 时，按门户地址所在网段的 /16 自动推断 ——
    这样别的学校也不用改代码。
    """
    if "allow_cidrs" in cfg.options:
        rule = cfg.options["allow_cidrs"]
    else:
        base = (portal or portal_of(cfg)).rstrip("/")
        host = portal_host(base)
        try:
            ipaddress.ip_address(host)
        except ValueError:
            return True, ""  # 门户是域名，判断不了网段，交给上游
        rule = [host + "/16"]

    if rule in ("off", "none", False) or rule in ([], ()):
        return True, ""
    if isinstance(rule, str):
        rule = [part.strip() for part in rule.split(",") if part.strip()]
    if not isinstance(rule, (list, tuple)) or not rule:
        return True, ""
    if not ip:
        return True, ""  # 取不到 IP 时不在这里拦，交给上游去失败
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return True, ""
    for cidr in rule:
        try:
            if addr in ipaddress.ip_network(str(cidr), strict=False):
                return True, ""
        except ValueError:
            continue
    return False, "本机 IP {} 不在校园网段 {} 内".format(ip, "、".join(str(r) for r in rule))


def _is_auth_error(text: str) -> bool:
    return any(word in (text or "") for word in AUTH_ERROR_KEYWORDS)


def _login_once(cfg, password, prefix, suffix, force, stream=False):
    username = cfg.username
    if suffix and "@" not in username:
        username += suffix
    args = ["login", "-c", str(CONFIG_PATH), "--portal", portal_of(cfg)]
    name = provider_of(cfg)
    if name:
        args += ["--provider", name]
    if force:
        args.append("--force")
    env = {
        "CAMPUSNET_USERNAME": username,
        "CAMPUSNET_PASSWORD": password,
        # 通过环境变量把账号前缀传给上游 provider（CAMPUSNET_OPTIONS 是它的正规入口）
        "CAMPUSNET_OPTIONS": json.dumps({"account_prefix": prefix}, ensure_ascii=False),
    }
    return call_campusnet(args, env, stream=stream)


def attempt_login(force: bool = False, prefix=None, stream=False):
    """登录一次，返回 (状态码, 输出)。

    状态码：0 已在线/登录成功；1 失败；2 未配置；3 不在校园网段（主动跳过）；
    4 账号密码被拒（不要频繁重试，免得触发锁定）。

    ``prefix`` 显式指定时跳过门户发现、只用这一种账号前缀（排查用）。
    """
    cfg = load_config()
    if not cfg.username:
        return 2, "尚未配置校园网账号"

    password = load_password()
    if not password:
        return 2, "未找到已保存的密码（可能是换了电脑或 Windows 账户）"

    ip = local_ip()
    portal = portal_of(cfg)
    allowed, why = network_allowed(ip, cfg, portal)
    if not allowed:
        return 3, "已跳过登录以保护账号：{}".format(why)

    # 快速通道：已认证且真能上外网时直接返回，省掉一整套外网探测
    if not force and portal_online(portal) and internet_quick():
        return 0, "✔ 已联网，无需认证"

    if prefix is not None:
        candidates = [(prefix, "")]
    else:
        # 优先问门户要配置，拿到确切的账号前缀/后缀，一次就对，不浪费失败尝试
        page = fetch_portal_config(portal)
        if page:
            prefix = ",0," if str(page.get("account_prefix", "0")).strip() == "1" else ""
            suffix = str(page.get("account_suffix") or "").strip()
            candidates = [(prefix, suffix)]
            if (cfg.options.get("account_prefix") != prefix
                    or cfg.options.get("account_suffix") != suffix):
                cfg.options["account_prefix"] = prefix
                cfg.options["account_suffix"] = suffix
                cfg.save(str(CONFIG_PATH), include_password=False)
        else:
            candidates = [(item, "") for item in ACCOUNT_PREFIXES]

    failures = []
    for prefix, suffix in candidates:
        code, output = _login_once(cfg, password, prefix, suffix, force, stream=stream)
        if code == 0:
            return 0, output
        failures.append((prefix, code, output))

    combined = "\n".join(
        "[账号格式 前缀={}] {}".format(
            prefix or "无", (output.splitlines() or ["失败"])[-1].strip()
        )
        for prefix, _code, output in failures
    )
    if any(_is_auth_error(output) for _p, _c, output in failures):
        return 4, combined
    return 1, combined


# --------------------------------------------------------------------------- 配置流程
def cmd_setup(args) -> int:
    cfg = load_config()

    say()
    say("=" * 60)
    say("  校园网自动登录 · 首次配置")
    say("=" * 60)
    say("  认证服务器：{}".format(portal_of(cfg)))
    say("  认证方式：{}（新版 eportal）".format(provider_of(cfg) or "auto（自动识别）"))
    say()
    say("  账号密码只保存在这台电脑上，源码和仓库里不会有它们。")
    say("  密码用 Windows DPAPI 加密，换电脑/换 Windows 账户都解不开。")
    say()

    default_user = cfg.username
    prompt = "  校园网账号（学号）"
    if default_user:
        prompt += " [{}]".format(default_user)
    try:
        entered = input(prompt + "：").strip()
    except (EOFError, KeyboardInterrupt):
        say("\n已取消。")
        return 2
    username = entered or default_user
    if not username:
        say("  没有账号，已取消。")
        return 2

    try:
        password = getpass.getpass("  校园网密码（输入时不显示）：")
    except (EOFError, KeyboardInterrupt):
        say("\n已取消。")
        return 2
    if not password:
        say("  没有输入密码，已取消。")
        return 2

    try:
        suffix = input("  运营商后缀（直接回车=不需要，本校常见 @dx / @lt）：").strip()
    except (EOFError, KeyboardInterrupt):
        suffix = ""

    # 部分学校账号必须带运营商后缀。上游 provider 只在设了 carrier 时才拼后缀，
    # 这里干脆把后缀直接并进账号，兼容性最好。
    if suffix and "@" not in username:
        username = username + suffix

    # 1) 写上游配置（不含密码）
    cfg.username = username
    # 只在"全新配置"时填默认门户/认证方式；已有配置（例如别的学校用
    # campusnet setup 生成过）保持不动，这样这个工具能被别处直接复用。
    if not cfg.portal_ip:
        cfg.portal_ip = PORTAL
        if cfg.provider in ("", "auto"):
            cfg.provider = PROVIDER
    cfg.timeout = 8
    cfg.options = {}
    saved = cfg.save(str(CONFIG_PATH), include_password=False)

    # 2) 单独存密码
    save_password(password)
    del password

    if not dpapi.available():
        say()
        say("  ! 当前系统不支持 DPAPI，密码以明文保存在 {}".format(SECRET_PATH))
    say()
    say("  ✔ 配置已保存：{}".format(saved))
    say("  ✔ 密码已加密保存：{}".format(SECRET_PATH))
    say()

    # 3) 立刻实测一次
    say("  正在测试登录（最多约 20 秒）……")
    code, output = attempt_login(force=True, stream=True)
    say()
    if code == 0:
        say("  ✔ 测试通过，网络已连接。")
    else:
        say("  ✘ 测试未通过，请核对账号密码。日志：{}".format(LOG_PATH))
        log("配置后测试登录失败（code={}）：{}".format(code, output))
        say("    可以双击 status.bat 查看详细状态，或重新运行 setup.bat。")
        return 1

    # 4) 装开机自启
    say()
    try:
        answer = input("  是否安装开机自动登录？[Y/n] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        answer = "y"
    if answer in ("", "y", "yes"):
        if install_autostart():
            say("  ✔ 已安装开机自启（当前用户登录后自动运行）。")
        else:
            say("  ! 安装开机自启失败，可稍后双击 install_startup.bat 重试。")
    else:
        say("  已跳过。需要时可双击 install_startup.bat。")

    say()
    say("  全部完成！以后开机不用再手动登录了。")
    say("  日志位置：{}".format(LOG_PATH))
    say()
    log("配置完成，账号={}".format(_mask_account(username)))
    return 0


# --------------------------------------------------------------------------- 开机自启
def _autostart_command() -> str:
    if FROZEN:
        # 优先走隐藏启动器（WMI 创建进程），避免未签名 exe 触发
        # Windows 的"打开文件 - 安全警告 / 无法验证发布者"弹窗
        launcher = Path(sys.executable).parent / "启动.vbs"
        if launcher.exists():
            return '"{}" "{}" --watch'.format(
                Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "wscript.exe",
                launcher,
            )
        return '"{}" --watch'.format(sys.executable)
    return '"{}" "{}" --watch'.format(python_executable(console=False), ROOT / "autologin.py")


def _reg(args):
    """调用系统 reg.exe（比 winreg 在某些受限环境下更可靠）。"""
    import subprocess

    try:
        return subprocess.run(
            ["reg.exe"] + list(args),
            capture_output=True,
            creationflags=0x08000000,  # CREATE_NO_WINDOW
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return None


def install_autostart() -> bool:
    if os.name != "nt":
        say("  开机自启目前只支持 Windows。")
        return False

    command = _autostart_command()
    result = _reg(["add", r"HKCU\Software\Microsoft\Windows\CurrentVersion\Run",
                   "/v", APP_NAME, "/t", "REG_SZ", "/d", command, "/f"])
    if result is not None and result.returncode == 0:
        log("已安装开机自启：{}".format(command))
        return True

    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, command)
        log("已安装开机自启：{}".format(command))
        return True
    except OSError as exc:
        log("安装开机自启失败：{}".format(exc))
        return False


def uninstall_autostart() -> bool:
    if os.name != "nt":
        return False

    result = _reg(["delete", r"HKCU\Software\Microsoft\Windows\CurrentVersion\Run",
                   "/v", APP_NAME, "/f"])
    if result is not None and result.returncode == 0:
        log("已移除开机自启")
        return True
    if result is not None and result.returncode == 1:
        # reg delete 返回值 1 表示"值不存在"，等同于已经移除
        log("开机自启本来就不存在")
        return True

    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, APP_NAME)
        log("已移除开机自启")
        return True
    except FileNotFoundError:
        return True
    except OSError as exc:
        log("移除开机自启失败：{}".format(exc))
        return False


def autostart_installed() -> bool:
    if os.name != "nt":
        return False

    result = _reg(["query", r"HKCU\Software\Microsoft\Windows\CurrentVersion\Run", "/v", APP_NAME])
    if result is not None:
        return result.returncode == 0

    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_QUERY_VALUE) as key:
            winreg.QueryValueEx(key, APP_NAME)
        return True
    except OSError:
        return False


def purge_data():
    """彻底卸载：移除自启、删除配置与加密密码。返回清理说明列表。"""
    import shutil

    notes = []
    log("卸载：开始清理自启 / 配置 / 密码")
    try:
        if uninstall_autostart():
            notes.append("已移除开机自启")
        else:
            notes.append("移除开机自启失败")
    except Exception as exc:  # noqa: BLE001
        notes.append("移除开机自启失败：{}".format(exc))

    for path in (DATA_DIR, CONFIG_PATH.parent):
        try:
            if path.is_dir():
                shutil.rmtree(path)
                notes.append("已删除 {}".format(path))
        except OSError as exc:
            notes.append("删除失败 {}：{}".format(path, exc))

    # 注意：这里不能再写日志，否则会把刚删掉的目录重新建出来
    return notes


# --------------------------------------------------------------------------- 模式
def _mask_account(username: str) -> str:
    """日志里给账号打码：保留前两位，其余用星号。"""
    username = username or ""
    if len(username) <= 2:
        return "***"
    return username[:2] + "*" * (len(username) - 2)


def _one_line(text: str, limit: int = 300) -> str:
    """把多行输出压成一行，方便写日志。"""
    parts = [line.strip() for line in (text or "").splitlines() if line.strip()]
    joined = " / ".join(parts)
    return joined[:limit]


def cmd_once(args) -> int:
    force = getattr(args, "force", False)
    say()
    say("  正在检查网络{}（通常 5~20 秒；如需重新认证会自动登录）……".format(
        "并强制重新认证" if force else "并登录"))
    code, output = attempt_login(
        force=force,
        prefix=getattr(args, "account_prefix", None),
        stream=True,  # 边跑边显示，避免看起来像卡死
    )
    if code == 2:
        say("! " + output)
        say("  请先完成账号密码配置（菜单第 1 项）。")
        return 2
    if code == 3:
        say("! " + output)
        log("跳过登录：{}".format(output))
        return 3

    if code == 0:
        say()
        say("  ✔ 完成：现在可以上网了。")
        log("检查完成：已在线")
    elif code == 4:
        say()
        say("  ! 账号或密码被拒绝。为避免账号被锁定，请不要频繁重试；")
        say("    确认密码无误后，用菜单第 1 项重新填写。")
        log("账号被拒绝（不重试）：{}".format(_one_line(output)))
    else:
        say()
        say("  ! 登录没有成功，可以选「诊断 / 排障」看详细原因。")
        log("登录失败（code={}）：{}".format(code, _one_line(output, 500)))
    return code


def _acquire_lock():
    """守护模式单实例：占住一个本地端口，占不到说明已经有一个在跑。"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", LOCK_PORT))
        sock.listen(1)
        return sock
    except OSError:
        sock.close()
        return None


def cmd_watch(args) -> int:
    lock = _acquire_lock()
    if lock is None:
        log("已有守护进程在运行，本次启动退出")
        return 0

    cfg = load_config()
    if not cfg.username or not load_password():
        log("未配置账号或密码，守护模式退出（请先运行 setup.bat）")
        return 2

    log("守护模式启动，间隔 {} 秒".format(args.interval))
    state = None
    failures = 0
    try:
        while True:
            code, output = attempt_login(force=False)

            if code == 2:
                log("账号或密码没配置好，守护模式退出：{}".format(output))
                return 2

            if code == 3:
                # 不在校园网段（家里/手机热点）：绝不在陌生网络里发账号密码
                if state != "foreign":
                    log("当前不在校园网，暂停登录（{}）".format(output))
                    state = "foreign"
                time.sleep(max(60, args.idle))
                continue

            if code == 4:
                # 账号密码被拒：继续重试只会增加锁定风险，放慢到很久一次
                log("账号或密码被拒绝，{} 秒后再试（避免锁定）：{}".format(
                    args.auth_retry, _one_line(output)))
                state = "auth"
                time.sleep(max(60, args.auth_retry))
                continue

            ok = code == 0
            if state is not ok:
                if ok:
                    log("网络已连通")
                else:
                    log("登录失败，稍后重试：{}".format(_one_line(output, 500)))
                state = ok

            if ok:
                failures = 0
                delay = args.interval
            else:
                failures += 1
                # 开机后网络可能还没就绪：前 4 分钟用短间隔快速重试
                delay = args.retry if failures <= 12 else args.interval
            time.sleep(max(5, int(delay)))
    except KeyboardInterrupt:
        log("守护模式被手动中断")
        return 0
    finally:
        try:
            lock.close()
        except OSError:
            pass


def cmd_status(args) -> int:
    cfg = load_config()
    say()
    say("配置：{}".format(CONFIG_PATH))
    say("账号：{}".format(cfg.username or "（未配置）"))
    say("密码：{}".format("已保存（DPAPI 加密）" if load_password() else "未保存"))
    say("自启：{}".format(
        "已安装（注册表 Run 项 CampusNetAutoLogin）" if autostart_installed() else "未安装"))
    say()
    # 直接复用上游的 status 输出（在线状态、本机 IP、认证方式指纹）
    code, output = call_campusnet(["status"], timeout=60)
    for line in output.splitlines():
        # 上游 status 只检查它自己注册的那一项自启（名字叫 campusnet），
        # 跟我们用的 CampusNetAutoLogin 是两回事，会把上面"已安装"打成
        # "未安装"误导人，所以这行直接略过，自启状态以上面那行为准。
        if "自启" in line:
            continue
        say(line)
    return code


def cmd_diagnose(args) -> int:
    cfg = load_config()
    say()
    say("诊断信息")
    say("-" * 50)
    say("Python      ：{}".format(sys.executable))
    say("项目目录    ：{}".format(ROOT))
    say("门户地址    ：{}".format(portal_of(cfg)))
    say("认证方式    ：{}".format(provider_of(cfg) or "auto（自动识别）"))
    say("配置文件    ：{}".format(CONFIG_PATH))
    say("密码文件    ：{}".format(SECRET_PATH))
    say("日志文件    ：{}".format(LOG_PATH))
    say("DPAPI       ：{}".format("可用" if dpapi.available() else "不可用"))
    say("账号格式    ：{}".format(
        "前缀={!r} 后缀={!r}".format(
            cfg.options.get("account_prefix"), cfg.options.get("account_suffix") or "")
        if "account_prefix" in cfg.options else "未确认（登录时会向门户查询）"
    ))
    say("开机自启    ：{}".format("已安装" if autostart_installed() else "未安装"))

    say()
    say("本机 IP     ：{}".format(local_ip() or "未知"))
    say("门户配置    ：")
    page = fetch_portal_config(portal_of(cfg))
    if page:
        prefix = ",0," if str(page.get("account_prefix", "0")).strip() == "1" else "无"
        say("  account_prefix = {}  -> 账号前缀 {}".format(
            page.get("account_prefix"), prefix))
        say("  account_suffix = {}".format(page.get("account_suffix") or "（无）"))
        say("  login_method   = {}".format(page.get("login_method")))
        say("  ep_http_port   = {}".format(page.get("ep_http_port")))
    else:
        say("  查询失败（门户不可达，或该校不提供该接口）——登录时会退回试两种账号格式")

    host = portal_host(portal_of(cfg))
    port = portal_port(portal_of(cfg))
    say()
    say("门户连通性  ：", )
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(4)
    try:
        sock.connect((host, port))
        sock_ip = sock.getsockname()[0]
        say("  ✔ {}:{} 可连接，本机出口 IP {}".format(host, port, sock_ip))
    except OSError as exc:
        say("  ✘ 连不上 {}:{} —— {}".format(host, port, exc))
    finally:
        sock.close()

    say()
    code, output = call_campusnet(["status"], timeout=60)
    for line in output.splitlines():
        say("  " + line)
    return code


# --------------------------------------------------------------------------- 菜单
def cmd_install(args) -> int:
    cfg = load_config()
    if not cfg.username or not load_password():
        say("! 请先完成账号密码配置（菜单第 1 项）。")
        return 2
    ok = install_autostart()
    say("✔ 已安装开机自启" if ok else "! 安装失败")
    return 0 if ok else 1


def cmd_uninstall(args) -> int:
    ok = uninstall_autostart()
    say("✔ 已移除开机自启" if ok else "! 移除失败")
    return 0 if ok else 1


def cmd_purge(args) -> int:
    """清除自启 / 配置 / 密码。程序文件夹请用发布包里的 卸载.bat 删除。"""
    log("卸载：命令行 --purge")
    notes = purge_data()
    lines = ["清理完成："] + ["· " + n for n in notes] + [
        "", "最后一步：双击程序文件夹里的「卸载.bat」删除文件夹。"]
    text = "\n".join(lines)
    for line in lines:
        say(line)

    if os.name == "nt":
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(0, text, "szu-netlogin 卸载", 0x40)
        except Exception:  # noqa: BLE001
            pass
    return 0


def create_desktop_shortcut() -> bool:
    """在桌面创建「校园网自动登录」快捷方式（避免用户去双击 .py）。"""
    if os.name != "nt":
        return False
    if FROZEN:
        target = sys.executable
        workdir = str(Path(sys.executable).parent)
    else:
        # 优先指向图形界面；没有 gui.py 时退回菜单
        bat = ROOT / "图形界面.bat"
        if not (bat.exists() and (ROOT / "gui.py").exists()):
            bat = ROOT / "校园网助手.bat"
        if not bat.exists():
            return False
        target = str(bat)
        workdir = str(ROOT)
    script = r'''
$ErrorActionPreference = 'Stop'
$desktop = [Environment]::GetFolderPath('Desktop')
$ws = New-Object -ComObject WScript.Shell
$lnk = $ws.CreateShortcut((Join-Path $desktop '校园网自动登录.lnk'))
$lnk.TargetPath = '{target}'
$lnk.WorkingDirectory = '{root}'
$lnk.IconLocation = '{icon},0'
$lnk.Description = '校园网自动登录'
$lnk.WindowStyle = 1
$lnk.Save()
Write-Output 'OK'
'''.format(target=target, root=workdir, icon=target)
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    try:
        # 不按文本解码：PowerShell 在管道里输出的是 GBK，直接用字节匹配就绕开编码问题
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
            capture_output=True, timeout=30, **_no_window(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0 and b"OK" in (proc.stdout or b"")


def cmd_shortcut(args) -> int:
    ok = create_desktop_shortcut()
    if ok:
        say("✔ 已在桌面创建快捷方式「校园网自动登录」")
        say("  以后双击桌面图标即可，不用再找文件夹。")
    else:
        say("! 创建失败，可以直接双击文件夹里的「校园网助手.bat」。")
    return 0 if ok else 1


def cmd_gui(args) -> int:
    """打开图形界面（独立窗口，不占住当前控制台）。"""
    gui = ROOT / "gui.py"
    if not gui.exists():
        say("! 找不到 gui.py，图形界面未安装。")
        return 1
    try:
        subprocess.Popen(
            [python_executable(console=False), str(gui)],
            cwd=str(ROOT),
            **_no_window(),
        )
    except OSError as exc:
        say("! 打开失败：{}".format(exc))
        return 1
    say("✔ 已打开图形界面窗口。")
    return 0


def _menu_actions():
    return [
        ("打开图形界面", cmd_gui),
        ("配置 / 修改账号密码", cmd_setup),
        ("查看当前网络状态", cmd_status),
        ("立即登录一次", lambda a: cmd_once(argparse.Namespace(force=False, account_prefix=None))),
        ("强制重新认证", lambda a: cmd_once(argparse.Namespace(force=True, account_prefix=None))),
        ("安装开机自启", cmd_install),
        ("卸载开机自启", cmd_uninstall),
        ("诊断 / 排障", cmd_diagnose),
        ("在桌面创建快捷方式", cmd_shortcut),
    ]


def cmd_menu(args) -> int:
    actions = _menu_actions()
    while True:
        say()
        say("=" * 52)
        say("  校园网自动登录 · 校园网助手")
        say("=" * 52)
        for index, (label, _fn) in enumerate(actions, 1):
            say("  {}. {}".format(index, label))
        say("  0. 退出")
        try:
            choice = input("\n  请输入编号后回车：").strip()
        except (EOFError, KeyboardInterrupt):
            return 0
        if choice in ("", "0"):
            return 0
        if not choice.isdigit() or not 1 <= int(choice) <= len(actions):
            say("  没有这个选项，请重新输入。")
            continue
        label, handler = actions[int(choice) - 1]
        say()
        say("— " + label + " " + "—" * (46 - len(label)))
        try:
            handler(args)
        except KeyboardInterrupt:
            say("\n  已取消。")
        except Exception as exc:  # noqa: BLE001 - 菜单里任何异常都不该把窗口弄崩
            say("! 操作出错：{}: {}".format(type(exc).__name__, exc))
        try:
            input("\n  按回车返回菜单……")
        except (EOFError, KeyboardInterrupt):
            return 0


# --------------------------------------------------------------------------- 入口
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="autologin.py",
        description="校园网自动登录（Dr.COM 新版 eportal，本校适配）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="不带参数 = 立即检查并登录一次。",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--menu", action="store_true", help="中文菜单（推荐给不想记命令的人）")
    group.add_argument("--setup", action="store_true", help="交互式录入账号密码")
    group.add_argument("--watch", action="store_true", help="常驻守护（开机自启用）")
    group.add_argument("--status", action="store_true", help="查看联网状态")
    group.add_argument("--diagnose", action="store_true", help="输出排障信息")
    group.add_argument("--install", action="store_true", help="安装开机自启")
    group.add_argument("--uninstall", action="store_true", help="移除开机自启")
    group.add_argument("--purge", action="store_true", help="彻底卸载（自启+配置+密码）")
    parser.add_argument("--force", action="store_true", help="已联网也强制重新认证")
    parser.add_argument(
        "--account-prefix",
        dest="account_prefix",
        default=None,
        help='强制账号前缀（如 ",0," 或空串），一般不需要手动指定',
    )
    parser.add_argument("--interval", type=int, default=300, help="守护模式检查间隔（秒，默认 300）")
    parser.add_argument("--retry", type=int, default=20, help="失败后的重试间隔（秒，默认 20）")
    parser.add_argument("--idle", type=int, default=300, help="不在校园网时的等待秒数（默认 300）")
    parser.add_argument("--auth-retry", dest="auth_retry", type=int, default=900,
                        help="账号被拒后的重试间隔（秒，默认 900）")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)

    if args.menu:
        return cmd_menu(args)
    if args.setup:
        return cmd_setup(args)
    if args.watch:
        return cmd_watch(args)
    if args.status:
        return cmd_status(args)
    if args.diagnose:
        return cmd_diagnose(args)
    if args.install:
        return cmd_install(args)
    if args.uninstall:
        return cmd_uninstall(args)
    if args.purge:
        return cmd_purge(args)
    return cmd_once(args)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
