#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""打包入口。

* 不带参数（双击 exe）：打开图形界面
* 带命令行参数（如 ``--watch`` / ``--status``）：走命令行版，供开机自启使用
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def dbg(message: str) -> None:
    """无控制台时也能追踪启动流程（仅在设置了 CAMPUS_DEBUG_FILE 时写）。"""
    target = os.environ.get("CAMPUS_DEBUG_FILE")
    if not target:
        return
    try:
        with open(target, "a", encoding="utf-8") as handle:
            handle.write(message + "\n")
    except OSError:
        pass

CLI_FLAGS = (
    "--menu", "--setup", "--watch", "--status", "--diagnose",
    "--install", "--uninstall", "--purge", "--force", "--account-prefix",
    "--interval", "--retry", "--idle", "--auth-retry", "--help",
)


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    # 清掉自身“来自 Internet”标记，避免每次双击都弹「无法验证发布者」
    if getattr(sys, "frozen", False):
        try:
            import autologin

            autologin.clear_motw(Path(sys.executable))
        except Exception:
            pass

    if "--selftest" in argv:
        import faulthandler

        faulthandler.dump_traceback_later(8, repeat=False)
        print("step 1: start", flush=True)
        import tkinter

        print("step 2: tkinter imported", tkinter.TkVersion, flush=True)
        root = tkinter.Tk()
        print("step 3: Tk created", flush=True)
        root.update()
        print("step 4: update ok", flush=True)
        root.destroy()
        print("step 5: done", flush=True)
        return 0

    if "--guitest" in argv:
        import traceback

        try:
            dbg("guitest: begin")
            import gui

            dbg("guitest: gui imported")
            app = gui.App()
            dbg("guitest: App constructed")
            app.update()
            dbg("guitest: update ok")
            app.destroy()
            dbg("guitest: done")
            print("gui ok", flush=True)
        except Exception:
            dbg("guitest: EXCEPTION\n" + traceback.format_exc())
            traceback.print_exc()
            return 1
        return 0

    if any(item in CLI_FLAGS for item in argv):
        import autologin

        return autologin.main(argv)
    import gui

    return gui.main()


if __name__ == "__main__":
    raise SystemExit(main())
