"""
Dashboard 启动脚本

用法：
    python -m dashboard.server --project-root /path/to/novel-project
    python -m dashboard.server                   # 自动从 .opencode/.claude 指针读取
"""

import argparse
import os
import sys
import webbrowser
from pathlib import Path


def _is_valid_project(p: Path) -> bool:
    """检查是否为有效的书项目目录（排除测试临时目录）。"""
    if not p.is_dir():
        return False
    if ".tmp" in str(p) or "pytest" in str(p) or "test_" in str(p):
        return False
    return (p / ".webnovel" / "state.json").is_file()


def _resolve_project_root(cli_root: str | None) -> Path:
    """按优先级解析 PROJECT_ROOT：CLI > 环境变量 > CWD 向上搜索 > 指针文件 > 智能搜索。"""
    if cli_root:
        return Path(cli_root).resolve()

    env = os.environ.get("WEBNOVEL_PROJECT_ROOT")
    if env:
        return Path(env).resolve()

    cwd = Path.cwd()

    # 优先：从 CWD 向上搜索（用户在书项目目录启动 dashboard 的场景）
    search = cwd
    for _ in range(10):
        if _is_valid_project(search):
            return search.resolve()
        parent = search.parent
        if parent == search:
            break
        search = parent

    # 次选：指针文件（webnovel-writer 仓库内的全局指针）
    for pointer_dir in (cwd / ".opencode", cwd / ".claude"):
        pointer = pointer_dir / ".webnovel-current-project"
        if pointer.is_file():
            target = pointer.read_text(encoding="utf-8").strip()
            if target:
                p = Path(target)
                if _is_valid_project(p):
                    return p.resolve()

    # 智能搜索：如果 CWD 是 webnovel-writer 仓库，检查同级目录下的书项目
    if (cwd / ".opencode" / "scripts" / "webnovel.py").is_file():
        for sibling in cwd.parent.iterdir():
            if sibling.name.startswith(".") or sibling.name == "webnovel-writer":
                continue
            if _is_valid_project(sibling):
                return sibling.resolve()
            # 检查嵌套结构：sibling/书名/.webnovel/state.json
            if sibling.is_dir():
                for child in sibling.iterdir():
                    if child.is_dir() and not child.name.startswith("."):
                        if _is_valid_project(child):
                            return child.resolve()

    # 最终兜底
    if _is_valid_project(cwd):
        return cwd.resolve()

    print("ERROR: 无法定位 PROJECT_ROOT（需要包含 .webnovel/state.json 的目录）", file=sys.stderr)
    print("提示: 使用 --project-root 指定路径，或设置 WEBNOVEL_PROJECT_ROOT 环境变量", file=sys.stderr)
    sys.exit(1)


def _kill_process_on_port(port: int) -> None:
    """杀掉占用指定端口的进程。"""
    import subprocess
    try:
        if sys.platform == "win32":
            # Windows: netstat -ano | findstr :port
            # 使用 encoding='gbk' 避免 Windows 中文输出 UTF-8 解码错误
            result = subprocess.run(
                ["netstat", "-ano"], capture_output=True,
                text=True, encoding="gbk", errors="replace", timeout=5
            )
            for line in result.stdout.splitlines():
                if f":{port}" in line and "LISTENING" in line:
                    parts = line.split()
                    pid = parts[-1]
                    if pid.isdigit():
                        print(f"正在终止占用端口 {port} 的进程 PID={pid}...")
                        subprocess.run(["taskkill", "/PID", pid, "/F"], capture_output=True)
                        print(f"已终止进程 PID={pid}")
                        return
        else:
            # Linux/Mac: lsof -i :port
            result = subprocess.run(
                ["lsof", "-i", f":{port}", "-t"], capture_output=True, text=True, timeout=5
            )
            pid = result.stdout.strip()
            if pid:
                print(f"正在终止占用端口 {port} 的进程 PID={pid}...")
                subprocess.run(["kill", pid], capture_output=True)
                print(f"已终止进程 PID={pid}")
                return
        print(f"未找到占用端口 {port} 的进程", file=sys.stderr)
    except Exception as e:
        print(f"终止进程失败: {e}", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description="Webnovel Dashboard Server")
    parser.add_argument("--project-root", type=str, default=None, help="小说项目根目录")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址")
    parser.add_argument("--port", type=int, default=8765, help="监听端口")
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    parser.add_argument("--kill-existing", action="store_true", help="自动杀掉占用端口的旧进程")
    args = parser.parse_args()

    project_root = _resolve_project_root(args.project_root)
    print(f"项目路径: {project_root}")

    # 非交互环境（如 OpenCode CLI）自动禁用浏览器弹出
    if not sys.stdin.isatty():
        args.no_browser = True

    # 检测端口占用
    import socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind((args.host, args.port))
        sock.close()
    except OSError:
        sock.close()
        if args.kill_existing:
            _kill_process_on_port(args.port)
        else:
            print(f"ERROR: 端口 {args.port} 已被占用", file=sys.stderr)
            print(f"", file=sys.stderr)
            print(f"可能原因：另一个 dashboard 实例正在运行", file=sys.stderr)
            print(f"", file=sys.stderr)
            print(f"解决方案：", file=sys.stderr)
            print(f"  1. 使用 --kill-existing 自动关闭旧进程", file=sys.stderr)
            print(f"  2. 手动关闭: netstat -ano | findstr :{args.port}", file=sys.stderr)
            print(f"  3. 使用其他端口: --port 8766", file=sys.stderr)
            sys.exit(1)

    # 延迟导入，以便先处理路径
    import uvicorn
    from .app import create_app

    # 必须把实际端口传进 create_app：CSRF/CORS 的允许源要跟着端口走，
    # 否则 `--port 8766`（端口占用提示推荐的做法）会让面板静默变成只读。
    app = create_app(project_root, port=args.port)

    url = f"http://{args.host}:{args.port}"
    print(f"Dashboard 启动: {url}")
    print(f"API 文档: {url}/docs")

    # 检查前端构建状态
    frontend_dist = Path(__file__).parent / "frontend" / "dist" / "index.html"
    if frontend_dist.is_file():
        print(f"前端: 已构建 ({url})")
    else:
        print(f"前端: 未构建 — 请先 cd .opencode/dashboard/frontend && npm run build")

    if not args.no_browser:
        webbrowser.open(url)

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
