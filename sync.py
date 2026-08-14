#!/usr/bin/env python3
"""Google Calendar ⇄ 飞书日历 双向同步引擎。

原则：宁可多、不能漏。
- 飞书真实日程 → 在 Google 创建镜像（标题前缀「[飞书] 」，extendedProperties 存来源 id）
- Google 真实日程 → 在飞书主日历创建镜像（标题前缀「[G] 」，description 存来源 id，忙闲=忙）
- 镜像随源头更新/删除；带前缀的镜像永远不会被反向同步（防循环）
- 无本地状态文件：每次全量比对同步窗口内的两边日程，天然幂等、可自愈

用法：
  python3 sync.py            # 执行一次同步
  python3 sync.py --dry-run  # 只打印将要做什么，不动任何数据
  python3 sync.py --doctor   # 生成脱敏诊断报告（求助时贴这个，不要贴原始日志）

首次使用请先运行 python3 setup.py（交互式安装向导）。
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone, date
from pathlib import Path

CFG_DIR = Path.home() / ".config" / "calendar-sync"
CONFIG_PATH = CFG_DIR / "config.json"
GOOGLE_TOKENS_PATH = CFG_DIR / "google_tokens.json"
GOOGLE_CLIENT_SECRET_PATH = CFG_DIR / "google_client_secret.json"
LOG_PATH = CFG_DIR / "sync.log"

G_BASE = "https://www.googleapis.com/calendar/v3"

# 镜像标记
G_MIRROR_PREFIX = "[飞书] "   # 飞书 → Google 的镜像标题前缀
F_MIRROR_PREFIX = "[G] "      # Google → 飞书 的镜像标题前缀
F_MARKER_RE = re.compile(r"\[gcal-sync\] id=(\S+) fp=(\w+)")

DEFAULT_CONFIG = {
    "window_past_days": 1,
    "window_future_days": 60,
    "google_calendar_id": "primary",
    "feishu_calendar_id": None,   # 首次运行自动解析主日历并回填
    "google_ics_url": None,       # 无 OAuth 时的只读通道（Google 日历私密 ICS 地址）
    "google_self_email": None,    # 用于识别「我已拒绝」的邀请，跳过不同步
}

DRY_RUN = False


def log(msg):
    line = f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    print(line)
    try:
        CFG_DIR.mkdir(parents=True, exist_ok=True)
        existed = LOG_PATH.exists()
        with open(LOG_PATH, "a") as f:
            f.write(line + "\n")
        if not existed:
            os.chmod(LOG_PATH, 0o600)  # 日志含会议标题，不给同机其他用户读
    except OSError:
        pass


def load_config():
    CFG_DIR.mkdir(parents=True, exist_ok=True)
    cfg = dict(DEFAULT_CONFIG)
    if CONFIG_PATH.exists():
        cfg.update(json.loads(CONFIG_PATH.read_text()))
    return cfg


def save_config(cfg):
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2, ensure_ascii=False))


# ===================== 飞书侧（走 lark-cli 的用户身份，日历权限已齐全） =====================

def _find_lark_cli():
    """定时任务的 PATH 很精简，找不到时再去常见的 node 安装位置捞一遍。"""
    found = shutil.which("lark-cli")
    if found:
        return found
    candidates = []
    nvm = Path.home() / ".nvm" / "versions" / "node"
    if nvm.is_dir():
        candidates += sorted(nvm.glob("*/bin/lark-cli"), reverse=True)
    candidates += [Path(p) / "lark-cli" for p in
                   ("/opt/homebrew/bin", "/usr/local/bin", "/usr/bin",
                    str(Path.home() / ".volta" / "bin"))]
    for c in candidates:
        if Path(c).exists():
            return str(c)
    return "lark-cli"


LARK_BIN = _find_lark_cli()


def fs_call(method, path, params=None, body=None, retried=False):
    cmd = [LARK_BIN, "api", method, path, "--as", "user", "--format", "json"]
    if params:
        cmd += ["--params", json.dumps(params)]
    if body is not None:
        cmd += ["--data", json.dumps(body, ensure_ascii=False)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except FileNotFoundError:
        raise RuntimeError(f"找不到 lark-cli（{LARK_BIN}）。安装：npm i -g @larksuite/cli，并完成 user 身份授权")
    out = proc.stdout.strip()
    try:
        resp = json.loads(out)
    except json.JSONDecodeError:
        raise RuntimeError(f"lark-cli 输出不是 JSON {method} {path}: {out[:200]} {proc.stderr[:200]}")
    if not resp.get("ok"):
        err = resp.get("error") or {}
        # 网络抖动 / 限流：等 3 秒重试一次
        if not retried and err.get("type") in ("network", "rate_limit"):
            time.sleep(3)
            return fs_call(method, path, params=params, body=body, retried=True)
        raise RuntimeError(f"飞书 API 失败 {method} {path}: {json.dumps(err, ensure_ascii=False)[:300]}")
    return resp.get("data") or {}


def fs_delete_event(cal_id, event_id):
    """幂等删除：日程已不存在（193003）视为成功"""
    try:
        fs_call("DELETE", f"/open-apis/calendar/v4/calendars/{cal_id}/events/{event_id}")
    except RuntimeError as e:
        if "193003" in str(e) or "is deleted" in str(e):
            log(f"    （{event_id} 已不存在，跳过）")
        else:
            raise


def fs_primary_calendar_id():
    data = fs_call("POST", "/open-apis/calendar/v4/calendars/primary", body={})
    cals = data.get("calendars") or []
    if not cals:
        raise RuntimeError(f"没拿到飞书主日历：{data}")
    c = cals[0]
    cal = c.get("calendar") if isinstance(c.get("calendar"), dict) else c
    cal_id = cal.get("calendar_id")
    if not cal_id:
        raise RuntimeError(f"解析不出 calendar_id：{c}")
    return cal_id


def fs_read(cal_id, t_start, t_end):
    """返回 (real_events: {key: ev}, mirrors: {gcal_id: {event_id, fp}})

    real_events 用「日程视图」接口读（自动展开重复日程的每个实例）；
    mirrors 用「日程列表」接口读（能拿到 description 里的来源标记）。
    """
    real = {}
    # instance_view 单次时间跨度有限制，按 30 天分片
    chunk = 30 * 86400
    a = t_start
    while a < t_end:
        b = min(a + chunk, t_end)
        data = fs_call(
            "GET",
            f"/open-apis/calendar/v4/calendars/{cal_id}/events/instance_view",
            params={"start_time": str(int(a)), "end_time": str(int(b))},
        )
        for it in data.get("items") or []:
            ev = _fs_normalize(it)
            if ev:
                real[ev["key"]] = ev
        a = b

    mirrors, dup_mirrors = {}, []
    page_token = ""
    while True:
        params = {"start_time": str(int(t_start)), "end_time": str(int(t_end)), "page_size": 500}
        if page_token:
            params["page_token"] = page_token
        data = fs_call("GET", f"/open-apis/calendar/v4/calendars/{cal_id}/events", params=params)
        for it in data.get("items") or []:
            if it.get("status") == "cancelled":
                continue
            m = F_MARKER_RE.search(it.get("description") or "")
            if m:
                entry = {"event_id": it.get("event_id"), "fp": m.group(2)}
                if m.group(1) in mirrors and mirrors[m.group(1)]["event_id"] != entry["event_id"]:
                    dup_mirrors.append(entry)  # 同一来源的重复镜像，当作待清理
                else:
                    mirrors[m.group(1)] = entry
                # 镜像不算真实日程
                real.pop(it.get("event_id"), None)
        page_token = data.get("page_token") or ""
        if not data.get("has_more") or not page_token:
            break

    return real, mirrors, dup_mirrors


def _fs_normalize(it):
    """飞书 instance_view 的一条日程 → 标准结构；镜像/取消/已拒绝返回 None"""
    if it.get("status") == "cancelled":
        return None
    summary = (it.get("summary") or "").strip() or "(无标题)"
    if summary.startswith(F_MIRROR_PREFIX):
        return None  # 这是 Google 同步过来的镜像，防循环
    if it.get("self_attendee_status") in ("decline", "declined"):
        return None
    key = it.get("event_id")
    st, et = it.get("start_time") or {}, it.get("end_time") or {}
    if not key or not st or not et:
        return None
    if st.get("date"):
        return {"key": key, "summary": summary, "all_day": True,
                "sdate": st["date"], "edate": (et.get("date") or st["date"])}
    try:
        start, end = int(st.get("timestamp")), int(et.get("timestamp"))
    except (TypeError, ValueError):
        return None
    return {"key": key, "summary": summary, "all_day": False, "start": start, "end": end}


def fs_event_body(ev, marker):
    """Google 真实日程 → 飞书镜像日程 body"""
    body = {
        "summary": (F_MIRROR_PREFIX + ev["summary"])[:200],
        "description": marker + "\n（自动同步自 Google Calendar，勿手动修改；改源头即可）",
        "free_busy_status": "busy",
    }
    if ev["all_day"]:
        body["start_time"] = {"date": ev["sdate"]}
        # Google 的 end date 是「不含」的；飞书按「含」理解也只会多挡一天，符合宁多勿漏
        body["end_time"] = {"date": ev["edate"]}
    else:
        body["start_time"] = {"timestamp": str(ev["start"])}
        body["end_time"] = {"timestamp": str(ev["end"])}
    return body


# ===================== Google 侧 =====================

def g_load_client():
    if not GOOGLE_CLIENT_SECRET_PATH.exists():
        raise RuntimeError(
            f"缺少 Google OAuth 客户端凭证：{GOOGLE_CLIENT_SECRET_PATH}\n"
            "  跑 python3 setup.py 会一步步带你做完（或看 docs/google-setup.md）"
        )
    data = json.loads(GOOGLE_CLIENT_SECRET_PATH.read_text())
    return data.get("installed") or data.get("web") or data


def g_access_token(force_refresh=False):
    if not GOOGLE_TOKENS_PATH.exists():
        raise RuntimeError(
            f"还没有 Google 授权：{GOOGLE_TOKENS_PATH} 不存在。\n"
            "  运行 python3 setup.py（或 python3 google_auth.py）完成一次性授权"
        )
    tokens = json.loads(GOOGLE_TOKENS_PATH.read_text())
    issued = tokens.get("obtained_at", 0)
    expires = tokens.get("expires_in", 0) or 0
    if not force_refresh and time.time() < issued + expires - 120:
        return tokens["access_token"]
    client = g_load_client()
    payload = urllib.parse.urlencode({
        "grant_type": "refresh_token",
        "refresh_token": tokens["refresh_token"],
        "client_id": client["client_id"],
        "client_secret": client["client_secret"],
    }).encode()
    req = urllib.request.Request("https://oauth2.googleapis.com/token", data=payload, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Google token 刷新失败：{e.read().decode()[:300]}（可能需要重跑 google_auth.py）")
    tokens.update({
        "access_token": body["access_token"],
        "expires_in": body.get("expires_in"),
        "obtained_at": int(time.time()),
    })
    GOOGLE_TOKENS_PATH.write_text(json.dumps(tokens, indent=2))
    os.chmod(GOOGLE_TOKENS_PATH, 0o600)
    return tokens["access_token"]


def g_call(method, path, body=None, params=None, retried=False):
    url = f"{G_BASE}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {g_access_token()}")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode()
            return json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as e:
        if e.code == 401 and not retried:
            g_access_token(force_refresh=True)
            return g_call(method, path, body=body, params=params, retried=True)
        if e.code in (403, 429) and not retried:
            time.sleep(2)
            return g_call(method, path, body=body, params=params, retried=True)
        raise RuntimeError(f"Google API 失败 {method} {path}: HTTP {e.code} {e.read().decode()[:300]}")


def _rfc3339(epoch):
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_rfc3339(s):
    return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())


def g_read(cal_id, t_start, t_end):
    """返回 (real_events: {key: ev}, mirrors: {feishu_key: {id, fp}})"""
    real, mirrors = {}, {}
    page_token = None
    while True:
        params = {
            "timeMin": _rfc3339(t_start),
            "timeMax": _rfc3339(t_end),
            "singleEvents": "true",
            "maxResults": "250",
        }
        if page_token:
            params["pageToken"] = page_token
        data = g_call("GET", f"/calendars/{urllib.parse.quote(cal_id)}/events", params=params)
        for it in data.get("items") or []:
            if it.get("status") == "cancelled":
                continue
            priv = (it.get("extendedProperties") or {}).get("private") or {}
            summary = (it.get("summary") or "").strip() or "(无标题)"
            if priv.get("fsync_src"):
                mirrors[priv["fsync_src"]] = {"id": it["id"], "fp": priv.get("fsync_fp", "")}
                continue
            if summary.startswith(G_MIRROR_PREFIX):
                continue  # 有前缀但丢了属性的孤儿镜像：既不当真实日程也不反向同步
            if any(a.get("self") and a.get("responseStatus") == "declined"
                   for a in it.get("attendees") or []):
                continue
            st, et = it.get("start") or {}, it.get("end") or {}
            if st.get("date"):
                ev = {"key": it["id"], "summary": summary, "all_day": True,
                      "sdate": st["date"], "edate": et.get("date") or st["date"]}
            elif st.get("dateTime") and et.get("dateTime"):
                ev = {"key": it["id"], "summary": summary, "all_day": False,
                      "start": _parse_rfc3339(st["dateTime"]), "end": _parse_rfc3339(et["dateTime"])}
            else:
                continue
            real[ev["key"]] = ev
        page_token = data.get("nextPageToken")
        if not page_token:
            break
    return real, mirrors


def g_event_body(ev, fp):
    """飞书真实日程 → Google 镜像日程 body"""
    body = {
        "summary": G_MIRROR_PREFIX + ev["summary"],
        "description": "自动同步自飞书日历，勿手动修改；改源头即可。",
        "extendedProperties": {"private": {"fsync_src": ev["key"], "fsync_fp": fp}},
        "transparency": "opaque",
        "reminders": {"useDefault": False},
    }
    if ev["all_day"]:
        sdate = ev["sdate"]
        # 飞书 end date 按「含」理解 → Google 的不含 end 要 +1 天（宁多勿漏）
        try:
            ed = date.fromisoformat(ev["edate"])
            sd = date.fromisoformat(sdate)
            end_excl = (max(ed, sd) + timedelta(days=1)).isoformat()
        except ValueError:
            end_excl = sdate
        body["start"] = {"date": sdate}
        body["end"] = {"date": end_excl}
    else:
        body["start"] = {"dateTime": _rfc3339(ev["start"])}
        body["end"] = {"dateTime": _rfc3339(ev["end"])}
    return body


# ===================== Google ICS 模式（无 OAuth 时的只读通道） =====================

def _unfold_ics(text):
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out = []
    for ln in lines:
        if ln[:1] in (" ", "\t") and out:
            out[-1] += ln[1:]
        else:
            out.append(ln)
    return out


def _ics_prop(line):
    """'DTSTART;TZID=Asia/Shanghai:20260807T100000' → ('DTSTART', {'TZID':'Asia/Shanghai'}, '2026...')"""
    head, _, value = line.partition(":")
    parts = head.split(";")
    name = parts[0].upper()
    params = {}
    for p in parts[1:]:
        k, _, v = p.partition("=")
        params[k.upper()] = v
    return name, params, value


def _tzinfo(name, default_tz):
    from zoneinfo import ZoneInfo
    try:
        return ZoneInfo(name) if name else default_tz
    except Exception:
        return default_tz


def _parse_ics_dt(params, value, default_tz):
    """返回 ('date', 'YYYY-MM-DD') 或 ('ts', epoch_int)"""
    value = value.strip()
    if params.get("VALUE") == "DATE" or (len(value) == 8 and value.isdigit()):
        return ("date", f"{value[:4]}-{value[4:6]}-{value[6:8]}")
    utc = value.endswith("Z")
    v = value.rstrip("Z")
    dt = datetime.strptime(v, "%Y%m%dT%H%M%S")
    if utc:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.replace(tzinfo=_tzinfo(params.get("TZID"), default_tz))
    return ("ts", int(dt.timestamp()))


_ICS_WEEKDAYS = {"MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6}


def _expand_rrule(rrule_str, start_kind, start_val, duration, default_tz, t_start, t_end, uid=""):
    """展开 RRULE，返回窗口内每个实例的 (kind, start_val)。不认识的规则记日志并尽力展开。"""
    rr = {}
    for part in rrule_str.split(";"):
        k, _, v = part.partition("=")
        if k:
            rr[k.upper()] = v
    freq = rr.get("FREQ", "").upper()
    interval = max(1, int(rr.get("INTERVAL", "1") or 1))
    count = int(rr["COUNT"]) if rr.get("COUNT") else None
    until_ts = None
    if rr.get("UNTIL"):
        kind, val = _parse_ics_dt({}, rr["UNTIL"], default_tz)
        until_ts = val if kind == "ts" else int(
            datetime.strptime(val, "%Y-%m-%d").replace(tzinfo=default_tz).timestamp()) + 86400
    unsupported = set(rr) - {"FREQ", "INTERVAL", "COUNT", "UNTIL", "BYDAY", "BYMONTHDAY", "WKST", "BYMONTH"}
    if unsupported:
        log(f"  ⚠️ RRULE 含未支持字段 {unsupported}（uid={uid[:40]}），按基础规则尽力展开，请人工核对该日程")

    # 统一转为「本地墙上时间」做日期步进（避免 DST 漂移）
    if start_kind == "date":
        base_local = datetime.strptime(start_val, "%Y-%m-%d").replace(tzinfo=default_tz)
    else:
        base_local = datetime.fromtimestamp(start_val, tz=default_tz)

    def emit(dt_local):
        if start_kind == "date":
            return ("date", dt_local.strftime("%Y-%m-%d"))
        return ("ts", int(dt_local.timestamp()))

    occs = []
    n_emitted = 0
    max_iter = 5000
    horizon = t_end + 86400

    if freq == "WEEKLY":
        bydays = sorted(_ICS_WEEKDAYS[d.strip()[-2:]] for d in rr.get("BYDAY", "").split(",") if d.strip()) \
            or [base_local.weekday()]
        week0 = base_local - timedelta(days=base_local.weekday())
        for wk in range(max_iter):
            week_start = week0 + timedelta(weeks=wk * interval)
            done = False
            for wd in bydays:
                occ = week_start + timedelta(days=wd)
                if occ < base_local:
                    continue
                ts = int(occ.timestamp())
                if until_ts and ts > until_ts:
                    done = True
                    break
                n_emitted += 1
                if count and n_emitted > count:
                    done = True
                    break
                occs.append(emit(occ))
                if ts > horizon:
                    done = True
                    break
            if done or int(week_start.timestamp()) > horizon:
                break
    elif freq == "DAILY":
        for i in range(max_iter):
            occ = base_local + timedelta(days=i * interval)
            ts = int(occ.timestamp())
            if (until_ts and ts > until_ts) or (count and i + 1 > count) or ts > horizon:
                break
            occs.append(emit(occ))
    elif freq in ("MONTHLY", "YEARLY"):
        byday = rr.get("BYDAY", "").strip()
        step_months = interval * (12 if freq == "YEARLY" else 1)
        n = 0
        for i in range(max_iter):
            total = base_local.month - 1 + i * step_months
            y, m = base_local.year + total // 12, total % 12 + 1
            if byday and freq == "MONTHLY":
                # 形如 2TU / -1FR：当月第 N 个 / 倒数第 N 个星期 X
                pos = int(byday[:-2]) if byday[:-2] not in ("", "+") else 1
                wd = _ICS_WEEKDAYS.get(byday[-2:], base_local.weekday())
                days = [d for d in range(1, 32)
                        if _safe_date(y, m, d) and _safe_date(y, m, d).weekday() == wd]
                if not days:
                    continue
                dom = days[pos - 1] if 0 < pos <= len(days) else (days[pos] if -len(days) <= pos < 0 else None)
                if dom is None:
                    continue
            else:
                dom = base_local.day
                if not _safe_date(y, m, dom):
                    continue
            occ = base_local.replace(year=y, month=m, day=dom)
            if occ < base_local:
                continue
            ts = int(occ.timestamp())
            if (until_ts and ts > until_ts) or ts > horizon:
                break
            n += 1
            if count and n > count:
                break
            occs.append(emit(occ))
    else:
        log(f"  ⚠️ 不支持的 RRULE FREQ={freq}（uid={uid[:40]}），该重复日程未展开，请人工核对")
        occs.append(emit(base_local))

    return occs


def _safe_date(y, m, d):
    try:
        return date(y, m, d)
    except ValueError:
        return None


def _occ_key_ts(kind, val, default_tz):
    if kind == "ts":
        return val
    return int(datetime.strptime(val, "%Y-%m-%d").replace(tzinfo=default_tz).timestamp())


def g_read_ics(url, t_start, t_end, self_email=None):
    """从 Google 私密 ICS 地址读取真实日程（只读模式）。返回 {key: ev}"""
    req = urllib.request.Request(url, headers={"User-Agent": "calendar-sync/1.0"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        text = resp.read().decode("utf-8", errors="replace")
    lines = _unfold_ics(text)

    default_tz = timezone.utc
    for ln in lines:
        if ln.startswith("X-WR-TIMEZONE:"):
            default_tz = _tzinfo(ln.split(":", 1)[1].strip(), timezone.utc)
            break

    vevents = []
    cur = None
    for ln in lines:
        if ln == "BEGIN:VEVENT":
            cur = {"EXDATE": [], "ATTENDEE": []}
        elif ln == "END:VEVENT":
            if cur is not None:
                vevents.append(cur)
            cur = None
        elif cur is not None and ":" in ln:
            name, params, value = _ics_prop(ln)
            if name in ("EXDATE", "ATTENDEE"):
                cur[name].append((params, value))
            else:
                cur[name] = (params, value)

    def declined(v):
        if not self_email:
            return False
        for params, value in v["ATTENDEE"]:
            if self_email.lower() in value.lower() and params.get("PARTSTAT", "").upper() == "DECLINED":
                return True
        return False

    def dtstart(v):
        params, value = v["DTSTART"]
        return _parse_ics_dt(params, value, default_tz)

    def duration_of(v, skind, sval):
        if "DTEND" in v:
            ekind, eval_ = _parse_ics_dt(v["DTEND"][0], v["DTEND"][1], default_tz)
            return ekind, eval_
        return skind, sval

    masters, singles, overrides = {}, [], {}
    for v in vevents:
        if "DTSTART" not in v:
            continue
        uid = v.get("UID", ({}, ""))[1] or "no-uid"
        if "RECURRENCE-ID" in v:
            okind, oval = _parse_ics_dt(v["RECURRENCE-ID"][0], v["RECURRENCE-ID"][1], default_tz)
            overrides[(uid, _occ_key_ts(okind, oval, default_tz))] = v
        elif "RRULE" in v:
            masters[uid] = v
        else:
            singles.append(v)

    out = {}

    def add(key, v, skind, sval, ekind, eval_):
        if v.get("STATUS", ({}, ""))[1].upper() == "CANCELLED" or declined(v):
            return
        summary = (v.get("SUMMARY", ({}, ""))[1] or "(无标题)").strip() or "(无标题)"
        if summary.startswith(G_MIRROR_PREFIX):
            return
        if skind == "date":
            out[key] = {"key": key, "summary": summary, "all_day": True,
                        "sdate": sval, "edate": eval_ if ekind == "date" else sval}
        else:
            end_ts = eval_ if ekind == "ts" else sval
            if not (sval < t_end and end_ts > t_start):
                return
            out[key] = {"key": key, "summary": summary, "all_day": False, "start": sval, "end": end_ts}

    for v in singles:
        uid = v.get("UID", ({}, ""))[1] or "no-uid"
        skind, sval = dtstart(v)
        ekind, eval_ = duration_of(v, skind, sval)
        if skind == "date":
            sts = _occ_key_ts(skind, sval, default_tz)
            if not (t_start - 86400 <= sts <= t_end + 86400):
                continue
        add(f"ics:{uid}", v, skind, sval, ekind, eval_)

    for uid, v in masters.items():
        skind, sval = dtstart(v)
        ekind, eval_ = duration_of(v, skind, sval)
        if skind == "ts" and ekind == "ts":
            dur = max(0, eval_ - sval)
        else:
            dur = 0
        exdates = set()
        for params, value in v["EXDATE"]:
            for one in value.split(","):
                xk, xv = _parse_ics_dt(params, one, default_tz)
                exdates.add(_occ_key_ts(xk, xv, default_tz))
        for okind, oval in _expand_rrule(v["RRULE"][1], skind, sval, dur, default_tz, t_start, t_end, uid=uid):
            occ_ts = _occ_key_ts(okind, oval, default_tz)
            if occ_ts in exdates or (uid, occ_ts) in overrides:
                continue
            if not (t_start - 86400 <= occ_ts <= t_end + 86400):
                continue
            if okind == "date":
                add(f"ics:{uid}:{occ_ts}", v, "date", oval, "date", oval)
            else:
                add(f"ics:{uid}:{occ_ts}", v, "ts", occ_ts, "ts", occ_ts + dur)

    for (uid, occ_ts), v in overrides.items():
        skind, sval = dtstart(v)
        ekind, eval_ = duration_of(v, skind, sval)
        if skind == "ts" and not (t_start - 86400 <= sval <= t_end + 86400):
            continue
        add(f"ics:{uid}:{occ_ts}", v, skind, sval, ekind, eval_)

    return out


# ===================== 同步核心 =====================

def fp_of(ev):
    if ev["all_day"]:
        raw = f"D|{ev['sdate']}|{ev['edate']}|{ev['summary']}"
    else:
        raw = f"T|{ev['start']}|{ev['end']}|{ev['summary']}"
    return hashlib.sha1(raw.encode()).hexdigest()[:10]


def sync_feishu_to_google(fs_real, g_mirrors, gcal_id):
    created = updated = deleted = 0
    for key, ev in fs_real.items():
        fp = fp_of(ev)
        mirror = g_mirrors.get(key)
        if mirror is None:
            log(f"  +G 创建镜像：{ev['summary']}")
            if not DRY_RUN:
                g_call("POST", f"/calendars/{urllib.parse.quote(gcal_id)}/events", body=g_event_body(ev, fp))
            created += 1
        elif mirror["fp"] != fp:
            log(f"  ~G 更新镜像：{ev['summary']}")
            if not DRY_RUN:
                g_call("PATCH", f"/calendars/{urllib.parse.quote(gcal_id)}/events/{mirror['id']}",
                       body=g_event_body(ev, fp))
            updated += 1
    for key, mirror in g_mirrors.items():
        if key not in fs_real:
            log(f"  -G 删除失效镜像（源头已删/移出窗口）：{mirror['id']}")
            if not DRY_RUN:
                try:
                    g_call("DELETE", f"/calendars/{urllib.parse.quote(gcal_id)}/events/{mirror['id']}")
                except RuntimeError as e:
                    if "HTTP 404" in str(e) or "HTTP 410" in str(e):
                        log(f"    （{mirror['id']} 已不存在，跳过）")
                    else:
                        raise
            deleted += 1
    return created, updated, deleted


def sync_google_to_feishu(g_real, fs_mirrors, fcal_id, dup_mirrors=None):
    created = updated = deleted = 0
    for entry in dup_mirrors or []:
        log(f"  -F 清理重复镜像：{entry['event_id']}")
        if not DRY_RUN:
            fs_delete_event(fcal_id, entry["event_id"])
        deleted += 1
    for key, ev in g_real.items():
        fp = fp_of(ev)
        marker = f"[gcal-sync] id={key} fp={fp}"
        mirror = fs_mirrors.get(key)
        if mirror is None:
            log(f"  +F 创建镜像：{ev['summary']}")
            if not DRY_RUN:
                fs_call("POST", f"/open-apis/calendar/v4/calendars/{fcal_id}/events",
                        body=fs_event_body(ev, marker))
            created += 1
        elif mirror["fp"] != fp:
            log(f"  ~F 更新镜像：{ev['summary']}")
            if not DRY_RUN:
                fs_call("PATCH", f"/open-apis/calendar/v4/calendars/{fcal_id}/events/{mirror['event_id']}",
                        body=fs_event_body(ev, marker))
            updated += 1
    for key, mirror in fs_mirrors.items():
        if key not in g_real:
            log(f"  -F 删除失效镜像（源头已删/移出窗口）：{mirror['event_id']}")
            if not DRY_RUN:
                fs_delete_event(fcal_id, mirror["event_id"])
            deleted += 1
    return created, updated, deleted


# ===================== --doctor：可安全分享的诊断报告 =====================

TITLE_LINE_RE = re.compile(r"^(.*?(?:创建镜像|更新镜像)：).*$")


def _redact(line):
    """把日志里的会议标题、ID、私密地址抹掉，只留结构信息"""
    line = TITLE_LINE_RE.sub(r"\1<标题已隐去>", line)
    line = re.sub(r"(id=)\S+", r"\1<已隐去>", line)
    line = re.sub(r"https?://\S+", "<地址已隐去>", line)
    line = re.sub(r"[0-9a-fA-F-]{20,}", "<ID已隐去>", line)
    return line


def doctor():
    """打印排障信息。刻意不含任何会议标题、日程 ID、私密地址、令牌。
    这份输出可以安全地贴给同事、AI 助手或 GitHub issue。"""
    import platform

    print("===== calendar-sync 诊断报告（已脱敏，可安全外发）=====")
    print(f"系统       : {platform.system()} {platform.release()}")
    print(f"Python     : {sys.version.split()[0]}")
    print(f"lark-cli   : {LARK_BIN if Path(LARK_BIN).exists() or shutil.which('lark-cli') else '未找到'}")

    print("\n--- 凭证文件（只报告存在与否，绝不打印内容）---")
    for label, path in (("Google 客户端凭证", GOOGLE_CLIENT_SECRET_PATH),
                        ("Google 令牌", GOOGLE_TOKENS_PATH),
                        ("配置", CONFIG_PATH)):
        if path.exists():
            print(f"  {label}: 存在，权限 {oct(path.stat().st_mode)[-3:]}")
        else:
            print(f"  {label}: 不存在")

    cfg = load_config()
    print("\n--- 配置（敏感项已隐去）---")
    print(f"  同步窗口   : {cfg['window_past_days']} 天前 ～ {cfg['window_future_days']} 天后")
    print(f"  飞书日历ID : {'已配置' if cfg.get('feishu_calendar_id') else '未配置'}")
    print(f"  Google通道 : {'API（双向）' if GOOGLE_TOKENS_PATH.exists() else ('ICS（只读）' if cfg.get('google_ics_url') else '未配置')}")

    print("\n--- 飞书授权 ---")
    try:
        r = subprocess.run([LARK_BIN, "auth", "status"], capture_output=True, text=True, timeout=60)
        st = json.loads(r.stdout).get("identities", {}).get("user", {}).get("status", "unknown")
        print(f"  用户身份: {st}")
    except Exception as e:
        print(f"  查询失败: {type(e).__name__}")

    print("\n--- 最近 25 行日志（标题/ID/地址已隐去）---")
    if LOG_PATH.exists():
        for ln in LOG_PATH.read_text(errors="replace").strip().split("\n")[-25:]:
            print("  " + _redact(ln))
    else:
        print("  （还没有日志）")
    print("\n===== 报告结束 =====")


def main():
    global DRY_RUN
    if "--doctor" in sys.argv:
        doctor()
        return
    DRY_RUN = "--dry-run" in sys.argv
    cfg = load_config()

    now = int(time.time())
    t_start = now - cfg["window_past_days"] * 86400
    t_end = now + cfg["window_future_days"] * 86400

    fcal_id = cfg.get("feishu_calendar_id")
    if not fcal_id:
        fcal_id = fs_primary_calendar_id()
        cfg["feishu_calendar_id"] = fcal_id
        save_config(cfg)
        log(f"已解析飞书主日历：{fcal_id}")
    gcal_id = cfg["google_calendar_id"]

    # Google 通道：有 OAuth token 用 API（双向）；否则退回 ICS 只读（仅 Google→飞书）
    if GOOGLE_TOKENS_PATH.exists():
        g_mode = "api"
    elif cfg.get("google_ics_url"):
        g_mode = "ics"
    else:
        raise RuntimeError("Google 侧未配置：跑 python3 setup.py 完成授权，或在 config.json 填 google_ics_url 走只读模式")

    log(f"开始同步{'（dry-run）' if DRY_RUN else ''}"
        f"：窗口 {cfg['window_past_days']} 天前 ～ {cfg['window_future_days']} 天后，Google 通道={g_mode}")

    # 两边都读成功后才进入写入/删除阶段——任何一侧读失败都直接中止，绝不误删
    fs_real, fs_mirrors, fs_dup_mirrors = fs_read(fcal_id, t_start, t_end)
    if g_mode == "api":
        g_real, g_mirrors = g_read(gcal_id, t_start, t_end)
    else:
        g_real = g_read_ics(cfg["google_ics_url"], t_start, t_end, self_email=cfg.get("google_self_email"))
        g_mirrors = {}
    log(f"飞书：真实日程 {len(fs_real)}，Google 镜像 {len(fs_mirrors)}；"
        f"Google：真实日程 {len(g_real)}，飞书镜像 {len(g_mirrors)}")

    if g_mode == "api":
        c1, u1, d1 = sync_feishu_to_google(fs_real, g_mirrors, gcal_id)
    else:
        c1 = u1 = d1 = 0
        log("  ⚠️ ICS 只读模式：飞书→Google 方向暂缓（完成 Google OAuth 后自动启用）")
    c2, u2, d2 = sync_google_to_feishu(g_real, fs_mirrors, fcal_id, dup_mirrors=fs_dup_mirrors)

    log(f"完成：飞书→Google 建{c1}/改{u1}/删{d1}；Google→飞书 建{c2}/改{u2}/删{d2}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"❌ 同步失败：{e}")
        sys.exit(1)
