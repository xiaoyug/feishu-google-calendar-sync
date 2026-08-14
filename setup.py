#!/usr/bin/env python3
"""交互式安装向导：把「飞书 ⇄ Google 日历双向同步」在本机跑起来。

每一步都会先检测、已完成的自动跳过，所以随时可以重复运行。
用法：python3 setup.py
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CFG_DIR = Path.home() / ".config" / "calendar-sync"
CLIENT_SECRET = CFG_DIR / "google_client_secret.json"
TOKENS = CFG_DIR / "google_tokens.json"

OK, WARN, BAD = "✅", "⚠️ ", "❌"


def say(msg=""):
    print(msg, flush=True)


def step(n, title):
    say()
    say(f"───── 第 {n} 步 / 共 6 步 · {title} ─────")


def ask(prompt, default="y"):
    hint = "Y/n" if default == "y" else "y/N"
    try:
        a = input(f"{prompt} [{hint}] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        say()
        sys.exit(1)
    if not a:
        a = default
    return a.startswith("y")


def wait(prompt="做完后按回车继续…"):
    try:
        input(prompt)
    except (EOFError, KeyboardInterrupt):
        say()
        sys.exit(1)


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=120, **kw)


def die(msg):
    say(f"\n{BAD} {msg}")
    sys.exit(1)


# ───────────────────────── 第 1 步：环境 ─────────────────────────

def check_env():
    step(1, "检查运行环境")

    if sys.version_info < (3, 9):
        die(f"需要 Python 3.9 及以上，当前是 {sys.version.split()[0]}。\n"
            "   macOS 装新版：brew install python3")
    say(f"{OK} Python {sys.version.split()[0]}")

    if not shutil.which("node"):
        say(f"{BAD} 没装 node（lark-cli 依赖它）")
        say("   macOS：brew install node")
        say("   其他：https://nodejs.org 下载 LTS 版")
        die("装完 node 后重新运行本向导")
    say(f"{OK} node {run(['node', '--version']).stdout.strip()}")

    if not shutil.which("lark-cli"):
        say(f"{WARN}没装 lark-cli（飞书官方命令行工具）")
        if ask("   现在自动安装吗（npm i -g @larksuite/cli）？"):
            say("   安装中，约需 1 分钟…")
            r = subprocess.run(["npm", "i", "-g", "@larksuite/cli"], timeout=600)
            if r.returncode != 0 or not shutil.which("lark-cli"):
                die("安装失败。手动跑：npm i -g @larksuite/cli")
        else:
            die("请先手动安装：npm i -g @larksuite/cli")
    say(f"{OK} lark-cli 已就位")


# ───────────────────────── 第 2 步：飞书授权 ─────────────────────────

def feishu_user_ready():
    r = run(["lark-cli", "auth", "status"])
    try:
        return json.loads(r.stdout).get("identities", {}).get("user", {}).get("status") == "ready"
    except (json.JSONDecodeError, AttributeError):
        return False


def check_feishu_auth():
    step(2, "飞书授权")

    if feishu_user_ready():
        say(f"{OK} 飞书已授权（用户身份就绪）")
        return

    say("需要用你的飞书账号登录一次，授权这台电脑读写你的日历。")
    say("下面会打印一个链接和验证码，用手机飞书扫码或在浏览器打开确认即可。")
    say()
    wait("准备好了按回车开始登录…")
    subprocess.run(["lark-cli", "auth", "login"], timeout=600)

    if not feishu_user_ready():
        die("飞书授权没完成。可以单独重试：lark-cli auth login")
    say(f"{OK} 飞书授权成功")


# ───────────────────────── 第 3 步：飞书日历连通 ─────────────────────────

def check_feishu_calendar():
    step(3, "测试飞书日历读写权限")

    r = run(["lark-cli", "api", "POST", "/open-apis/calendar/v4/calendars/primary",
             "--as", "user", "--format", "json", "--data", "{}"])
    try:
        resp = json.loads(r.stdout)
    except json.JSONDecodeError:
        die(f"lark-cli 返回看不懂：{r.stdout[:200]}{r.stderr[:200]}")

    if not resp.get("ok"):
        die(f"读飞书主日历失败：{json.dumps(resp.get('error'), ensure_ascii=False)[:300]}\n"
            "   多半是授权过期，重跑：lark-cli auth login")

    cals = (resp.get("data") or {}).get("calendars") or []
    if not cals:
        die("没找到你的飞书主日历")
    say(f"{OK} 飞书主日历可读写")


# ───────────────────────── 第 4 步：Google 凭证 ─────────────────────────

GCP_GUIDE = """
Google 这边需要你在自己的 Google Cloud 建一个「桌面应用」OAuth 客户端。
不花钱、不用审核，全程约 5 分钟。详细图文见 docs/google-setup.md，简版如下：

  1. 打开 https://console.cloud.google.com/projectcreate
     项目名随便填（比如 calendar-sync）→ Create → 等几秒创建完，切换到该项目

  2. 启用日历接口：https://console.cloud.google.com/apis/library/calendar-json.googleapis.com
     点 Enable

  3. 配置授权页：左边 APIs & Services → OAuth consent screen（或 Google Auth Platform）
     - App name 填 calendar-sync，support email 选你自己
     - Audience 选 External，联系邮箱填你自己，勾同意条款 → Create

  4. 把自己加进测试用户（最容易漏的一步，漏了会报 403 access_denied）：
     Audience 页面 → Test users → Add users → 填你自己的 Gmail → Save

  5. 建客户端：左边 Clients → Create client
     - Application type 选 【Desktop app】
     - 名字默认即可 → Create

  6. 弹窗里点 【Download JSON】，把下载到的文件放到下面这个位置（改成这个文件名）：
"""


def check_google_client():
    step(4, "Google OAuth 客户端凭证")

    CFG_DIR.mkdir(parents=True, exist_ok=True)
    if CLIENT_SECRET.exists():
        try:
            data = json.loads(CLIENT_SECRET.read_text())
            c = data.get("installed") or data.get("web") or data
            if c.get("client_id") and c.get("client_secret"):
                say(f"{OK} 已有凭证：{CLIENT_SECRET}")
                return
        except json.JSONDecodeError:
            pass
        say(f"{WARN}{CLIENT_SECRET} 内容不对，需要重新下载")

    say(GCP_GUIDE)
    say(f"     {CLIENT_SECRET}")
    say()
    say("  提示：下载后在「访达」按 Cmd+Shift+G，粘贴上面路径的文件夹部分即可定位。")
    say(f"  命令行一句话搬过去（假设下载在 Downloads，文件名以 client_secret 开头）：")
    say(f"    mv ~/Downloads/client_secret*.json {CLIENT_SECRET}")
    say()

    while True:
        wait("放好文件后按回车检查…")
        if CLIENT_SECRET.exists():
            try:
                data = json.loads(CLIENT_SECRET.read_text())
                c = data.get("installed") or data.get("web") or data
                if c.get("client_id"):
                    os.chmod(CLIENT_SECRET, 0o600)
                    say(f"{OK} 凭证已就位")
                    return
                say(f"{WARN}这个 JSON 里没有 client_id，是不是下错文件了？")
            except json.JSONDecodeError:
                say(f"{WARN}这个文件不是合法 JSON，请确认下载的是 OAuth 客户端 JSON")
        else:
            say(f"{WARN}还没看到 {CLIENT_SECRET}")
        if not ask("   再试一次吗？"):
            die("Google 凭证没配好，向导先退出。随时可以重新运行 python3 setup.py")


# ───────────────────────── 第 5 步：Google 授权 ─────────────────────────

def check_google_auth():
    step(5, "Google 授权")

    if TOKENS.exists():
        say(f"{OK} 已授权（{TOKENS}）")
        if not ask("   要重新授权吗（一般不需要）？", default="n"):
            return

    say("接下来会打开浏览器，用你的 Google 账号授权。")
    say(f"{WARN}中途会看到「Google hasn't verified this app」——这是你自己建的测试应用，")
    say("   点 Advanced（高级）→ Continue（继续前往）即可，不是风险提示。")
    say()
    wait("准备好了按回车开始授权…")

    r = subprocess.run([sys.executable, str(HERE / "google_auth.py")], timeout=600)
    if r.returncode != 0 or not TOKENS.exists():
        die("Google 授权没完成。可单独重试：python3 google_auth.py\n"
            "   若报 403 access_denied，是第 4 步的「Test users」没加自己")
    say(f"{OK} Google 授权成功")


# ───────────────────────── 第 6 步：试运行 + 装定时器 ─────────────────────────

def dry_run_and_install():
    step(6, "试运行并安装定时任务")

    say("先空跑一次，只看会做什么、不改任何数据…")
    say()
    r = subprocess.run([sys.executable, str(HERE / "sync.py"), "--dry-run"],
                       capture_output=True, text=True, timeout=600)
    out = (r.stdout or "") + (r.stderr or "")
    lines = out.strip().split("\n")
    for ln in lines[:40]:
        say("  " + ln)
    if len(lines) > 40:
        say(f"  …（省略 {len(lines) - 40} 行，完整内容见 ~/.config/calendar-sync/sync.log）")

    if r.returncode != 0:
        die("试运行失败，先别装定时器。把上面的报错发给我或提 issue。")

    say()
    say(f"{OK} 试运行通过。上面「+G 创建镜像」是要写进 Google 的，「+F 创建镜像」是要写进飞书的。")
    say(f"{WARN}第一次跑通常会有几十条——那是把你已有的日程补齐镜像，属于正常。")
    say()

    if not ask("确认无误，现在正式同步并装上定时任务（每 10 分钟一次）吗？"):
        say()
        say("好，先不装。想手动跑一次：python3 sync.py")
        say("想以后再装定时：./install.sh")
        return

    say()
    say("正式同步中（第一次可能要一两分钟）…")
    r = subprocess.run(["bash", str(HERE / "install.sh")], timeout=900)
    if r.returncode != 0:
        die("安装定时任务失败，见上面的报错")


def main():
    say()
    say("═══════════════════════════════════════════════")
    say("  飞书 ⇄ Google 日历 双向同步 · 安装向导")
    say("═══════════════════════════════════════════════")
    say()
    say("这个向导会带你走完 6 步，全程约 10 分钟。")
    say("任何一步中断都不要紧——重新运行 python3 setup.py 会自动跳过已完成的步骤。")

    check_env()
    check_feishu_auth()
    check_feishu_calendar()
    check_google_client()
    check_google_auth()
    dry_run_and_install()

    say()
    say("═══════════════════════════════════════════════")
    say(f"  {OK} 全部完成")
    say("═══════════════════════════════════════════════")
    say()
    say("现在两边日历会每 10 分钟自动对齐一次。")
    say("  看日志：tail -f ~/.config/calendar-sync/sync.log")
    say("  手动跑：python3 sync.py")
    say("  卸载：  ./uninstall.sh")
    say()
    say("提醒：改会议永远改源头，镜像日程（标题带 [G] / [飞书] 前缀）上的手动改动会被覆盖。")
    say()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        say("\n\n已中断。重新运行 python3 setup.py 可继续。")
        sys.exit(1)
