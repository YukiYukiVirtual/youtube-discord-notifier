import json
import re
import time
from datetime import datetime, time as dtime, timedelta, timezone
from html import unescape
from pathlib import Path
from xml.etree import ElementTree as ET

import holidays as jp_holidays_lib
import requests

import config as cfg
import secret as _secret

WEBHOOK_URL = _secret.WEBHOOK_URL
API_KEY = getattr(_secret, "YOUTUBE_API_KEY", "")
API_BASE = "https://www.googleapis.com/youtube/v3"

JST = timezone(timedelta(hours=9))
BASE = Path(__file__).resolve().parent
STATE_FILE = BASE / "state.json"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"}
NS = {"atom": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015"}


def log(msg):
    print(f"[{datetime.now(JST).strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


_JP_HOLIDAYS = jp_holidays_lib.Japan()


def current_interval(now=None):
    """配信が多い時間帯は短い間隔、それ以外は長い間隔を返す（秒）"""
    now = now or datetime.now(JST)
    t = now.time()
    is_rest_day = now.weekday() >= 5 or now.date() in _JP_HOLIDAYS
    if is_rest_day:
        high = dtime(cfg.RESTDAY_HIGH_START_HOUR, 0) <= t < dtime(cfg.RESTDAY_HIGH_END_HOUR, 0)
    else:
        high = t >= dtime(cfg.WEEKDAY_HIGH_START_HOUR, 0)
    return cfg.POLL_INTERVAL_HIGH_SEC if high else cfg.POLL_INTERVAL_LOW_SEC


# ---------- 状態管理 ----------
def load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"seeded": False, "videos": {}, "posts": []}


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")


# ---------- 通知 ----------
def notify(content):
    try:
        r = requests.post(WEBHOOK_URL, json={"content": content}, timeout=20)
        if r.status_code in (200, 204):
            return True
        log(f"webhook failed: HTTP {r.status_code}")
    except Exception as e:
        log(f"webhook error: {e}")
    return False


# ---------- YouTube Data API ----------
class ApiError(Exception):
    pass


def api_get(path, params):
    r = requests.get(f"{API_BASE}/{path}", params={**params, "key": API_KEY}, timeout=30)
    if r.status_code != 200:
        raise ApiError(f"{path}: HTTP {r.status_code}")
    d = r.json()
    if "error" in d:
        raise ApiError(f"{path}: {d['error'].get('message')}")
    return d


def _extract_handle(raw):
    """CHANNEL_ID の値から @ハンドルを取り出す（取り出せなければ None）"""
    s = raw.strip()
    m = re.search(r"youtube\.com/@([\w.-]+)", s)
    if m:
        return "@" + m.group(1)
    if s.startswith("@"):
        return s.split("/")[0]
    if re.fullmatch(r"[\w.-]+", s):
        return "@" + s
    return None


def resolve_channel_id(state):
    """CHANNEL_ID（UC... / @ハンドル / チャンネルURL）を UC... 形式のIDに解決する。
    解決結果は state にキャッシュする。解決できなければ None を返す"""
    if state.get("channel_id"):
        return state["channel_id"]
    raw = (cfg.CHANNEL_ID or "").strip()
    cid = None
    m = re.search(r"youtube\.com/channel/(UC[\w-]{22})", raw)
    if m:
        cid = m.group(1)
    elif re.fullmatch(r"UC[\w-]{22}", raw):
        cid = raw
    else:
        handle = _extract_handle(raw)
        if handle and API_KEY:
            try:
                d = api_get("channels", {"part": "id", "forHandle": handle})
                items = d.get("items", [])
                if items:
                    cid = items[0]["id"]
            except Exception as e:
                log(f"channel resolve failed ({handle}): {e}")
    if cid:
        state["channel_id"] = cid
    return cid


def get_uploads_playlist(state, cid):
    if state.get("uploads_playlist"):
        return state["uploads_playlist"]
    d = api_get("channels", {"part": "contentDetails", "id": cid})
    up = d["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]
    state["uploads_playlist"] = up
    return up


def api_discover(state, cid):
    """アップロード一覧（フィード相当）: [{id, title, url}]"""
    up = get_uploads_playlist(state, cid)
    d = api_get("playlistItems", {"part": "snippet", "playlistId": up, "maxResults": 25})
    out = []
    for it in d.get("items", []):
        sn = it.get("snippet", {})
        rid = sn.get("resourceId", {}).get("videoId")
        if rid:
            out.append({"id": rid, "title": sn.get("title", ""),
                        "url": f"https://www.youtube.com/watch?v={rid}"})
    return out


def api_statuses(ids):
    """{id: (status, start_iso, title)} status は 'live' / 'upcoming' / 'none'"""
    result = {}
    ids = list(ids)
    for i in range(0, len(ids), 50):
        d = api_get("videos", {"part": "snippet,liveStreamingDetails",
                               "id": ",".join(ids[i:i + 50])})
        for it in d.get("items", []):
            sn = it.get("snippet", {})
            lsd = it.get("liveStreamingDetails", {})
            lbc = sn.get("liveBroadcastContent", "none")
            status = lbc if lbc in ("live", "upcoming") else "none"
            result[it["id"]] = (status, lsd.get("scheduledStartTime"), sn.get("title", ""))
    return result


# ---------- スクレイピング（APIが使えない時の予備） ----------
def fetch_rss(cid):
    url = f"https://www.youtube.com/feeds/videos.xml?channel_id={cid}"
    r = requests.get(url, headers=UA, timeout=30)
    r.raise_for_status()
    root = ET.fromstring(r.text)
    entries = []
    for e in root.findall("atom:entry", NS):
        vid = e.findtext("yt:videoId", namespaces=NS)
        if not vid:
            continue
        entries.append({
            "id": vid,
            "title": e.findtext("atom:title", namespaces=NS) or "",
            "url": f"https://www.youtube.com/watch?v={vid}",
        })
    return entries


def fetch_stream_page_ids(cid):
    """配信一覧ページに出ている動画ID（枠の発見用）"""
    r = requests.get(f"https://www.youtube.com/channel/{cid}/streams",
                     headers=UA, timeout=30)
    ids = []
    for vid in re.findall(r'"videoId":"([\w-]{11})"', r.text):
        if vid not in ids:
            ids.append(vid)
    return ids


def fetch_status(video_id):
    """watchページから配信状態を判定する。
    return: (status, start_iso, title)  status は 'live' / 'upcoming' / 'none'"""
    r = requests.get(f"https://www.youtube.com/watch?v={video_id}", headers=UA, timeout=30)
    html = r.text
    if '"liveBroadcastContent":"liveNow"' in html or '"isLiveNow":true' in html:
        status = "live"
    elif '"liveBroadcastContent":"upcoming"' in html:
        status = "upcoming"
    else:
        status = "none"
    start_iso = None
    m = re.search(r'"startTimestamp":"([^"]+)"', html)
    if m:
        start_iso = m.group(1)
    title = None
    mt = re.search(r'<meta property="og:title" content="([^"]+)"', html)
    if mt:
        title = unescape(mt.group(1))
    return status, start_iso, title


def fetch_current_live(cid):
    """現在配信中なら (video_id, title) を返す"""
    r = requests.get(f"https://www.youtube.com/channel/{cid}/live",
                     headers=UA, timeout=30)
    m = re.search(r"watch\?v=([\w-]{11})", r.url)
    if not m:
        return None
    if '"liveBroadcastContent":"liveNow"' not in r.text and '"isLiveNow":true' not in r.text:
        return None
    title = ""
    mt = re.search(r'<meta property="og:title" content="([^"]+)"', r.text)
    if mt:
        title = unescape(mt.group(1))
    return m.group(1), title


# ---------- コミュニティ投稿（APIに取得手段が無いので解析のみ） ----------
def fetch_posts(cid):
    r = requests.get(f"https://www.youtube.com/channel/{cid}/posts",
                     headers=UA, timeout=30)
    ids = []
    for pid in re.findall(r'"postId":"([^"]+)"', r.text):
        if pid not in ids:
            ids.append(pid)
    return ids, r.text


def extract_post_text(html, post_id):
    idx = html.find(f'"postId":"{post_id}"')
    if idx < 0:
        return ""
    seg = html[idx: idx + 6000]
    m = re.search(r'"contentText":\{"runs":\[(.*?)\]\}', seg, re.S)
    if not m:
        return ""
    parts = []
    for t in re.findall(r'"text":"((?:[^"\\]|\\.)*)"', m.group(1)):
        try:
            parts.append(json.loads(f'"{t}"'))
        except Exception:
            pass
    return "".join(parts)[:500]


# ---------- 文面 ----------
def format_scheduled(title, url, start_iso):
    start_line = ""
    if start_iso:
        try:
            dt = datetime.fromisoformat(start_iso.replace("Z", "+00:00")).astimezone(JST)
            start_line = f"開始予定: {dt.strftime('%Y-%m-%d %H:%M')}\n"
        except Exception:
            pass
    return cfg.MSG_STREAM_SCHEDULED.format(
        channel=cfg.CHANNEL_NAME, title=title, start_line=start_line, url=url)


# ---------- メインのポーリング ----------
def poll(state):
    # チャンネルIDの解決（@ハンドルやURLで指定された場合は UC... 形式に直す）
    cid = resolve_channel_id(state)
    if not cid:
        log("チャンネルを特定できませんでした。config.py の CHANNEL_ID（ID / @ハンドル / URL）と APIキーを確認してください")
        return

    # フィード取得：API が主、失敗したらスクレイピングに落とす
    use_api = bool(API_KEY)
    feed = None
    if use_api:
        try:
            feed = api_discover(state, cid)
        except Exception as e:
            log(f"api discover failed, fallback to scrape: {e}")
            use_api = False
    if feed is None:
        feed = fetch_rss(cid)
    feed_ids = {e["id"] for e in feed}

    if not state["seeded"]:
        for e in feed:
            state["videos"][e["id"]] = {"status": "seen", "title": e["title"]}
        pids, _ = fetch_posts(cid)
        state["posts"] = pids
        state["seeded"] = True
        save_state(state)
        log(f"seeded: {len(feed)} videos, {len(pids)} posts (existing items are not notified)")
        return

    videos = state["videos"]

    # 追跡対象を集める（フィード＋配信一覧＋追跡中のもの）
    targets = {}
    for e in feed:
        targets[e["id"]] = e
    for vid in fetch_stream_page_ids(cid):
        if vid not in targets:
            targets[vid] = {"id": vid, "title": "", "url": f"https://www.youtube.com/watch?v={vid}"}
    for vid, v in videos.items():
        if v["status"] in ("upcoming", "live") and vid not in targets:
            targets[vid] = {"id": vid, "title": v["title"], "url": f"https://www.youtube.com/watch?v={vid}"}
    if not use_api:
        live_now = fetch_current_live(cid)
        if live_now and live_now[0] not in targets:
            targets[live_now[0]] = {"id": live_now[0], "title": live_now[1],
                                    "url": f"https://www.youtube.com/watch?v={live_now[0]}"}

    # 状態確認が必要なものだけ調べる
    need = [vid for vid in targets
            if videos.get(vid, {"status": "new"})["status"] in ("new", "seen", "done", "upcoming", "live")]
    statuses = {}
    if use_api:
        try:
            statuses = api_statuses(need)
        except Exception as e:
            log(f"api status failed, fallback to scrape: {e}")
            use_api = False
    if not use_api:
        for vid in need:
            statuses[vid] = fetch_status(vid)

    for vid in need:
        if vid not in statuses:
            continue  # API に項目が無い（削除・非公開など）は様子見
        status, start_iso, page_title = statuses[vid]
        e = targets[vid]
        prev = videos.get(vid, {"status": "new", "title": ""})["status"]
        title = e["title"] or page_title or videos.get(vid, {}).get("title", "")
        url = e["url"]

        if prev in ("new", "seen", "done"):
            if status == "upcoming":
                if start_iso:
                    # 開始予定が付いている枠だけ告知する
                    if notify(format_scheduled(title, url, start_iso)):
                        videos[vid] = {"status": "upcoming", "title": title, "sched_announced": True}
                        log(f"scheduled notified: {title}")
                else:
                    # 開始予定なしはエンコーダ配信の待機オブジェクトとみなし、告知せず追跡だけする
                    videos[vid] = {"status": "upcoming", "title": title, "sched_announced": False}
                    log(f"upcoming without schedule (silent track): {title}")
            elif status == "live":
                if notify(cfg.MSG_STREAM_STARTED.format(channel=cfg.CHANNEL_NAME, title=title, url=url)):
                    videos[vid] = {"status": "live", "title": title}
                    log(f"started notified: {title}")
            elif prev in ("seen", "done"):
                pass  # 既存動画・終了済み配信はそのまま（状態が変わった時だけ上の分岐で通知する）
            elif vid not in feed_ids:
                # フィード以外（配信一覧）で見つけただけの過去動画は通知せずに見済みにする
                videos[vid] = {"status": "seen", "title": title}
            else:
                if notify(cfg.MSG_VIDEO_POSTED.format(channel=cfg.CHANNEL_NAME, title=title, url=url)):
                    videos[vid] = {"status": "done", "title": title}
                    log(f"video notified: {title}")
        elif prev == "upcoming":
            if status == "upcoming" and start_iso and videos.get(vid, {}).get("sched_announced") is False:
                # 追跡開始後に開始予定が付いたら、その時点で枠を告知する
                if notify(format_scheduled(title, url, start_iso)):
                    videos[vid] = {"status": "upcoming", "title": title, "sched_announced": True}
                    log(f"scheduled notified (late schedule): {title}")
            elif status == "live":
                if notify(cfg.MSG_STREAM_STARTED.format(channel=cfg.CHANNEL_NAME, title=title, url=url)):
                    videos[vid] = {"status": "live", "title": title}
                    log(f"started notified: {title}")
            elif status == "none":
                videos[vid] = {"status": "cancelled", "title": title}
                log(f"schedule cancelled (silent): {title}")
        elif prev == "live":
            if status != "live":
                if notify(cfg.MSG_STREAM_ENDED.format(channel=cfg.CHANNEL_NAME, title=title, url=url)):
                    videos[vid] = {"status": "done", "title": title}
                    log(f"ended notified: {title}")

    # コミュニティ投稿
    pids, html = fetch_posts(cid)
    new_posts = [p for p in pids if p not in state["posts"]]
    for pid in reversed(new_posts):
        text = extract_post_text(html, pid) or "（本文を取得できませんでした）"
        url = f"https://www.youtube.com/post/{pid}"
        if notify(cfg.MSG_COMMUNITY_POST.format(channel=cfg.CHANNEL_NAME, text=text, url=url)):
            log(f"post notified: {pid}")
    state["posts"] = (state["posts"] + new_posts)[-200:]

    # 終わった動画の記録を掃除（フィードにも追跡対象にも残っていないもの）
    for vid in list(videos):
        if videos[vid]["status"] in ("done", "seen", "cancelled") and vid not in feed_ids and vid not in targets:
            del videos[vid]

    save_state(state)


def main():
    if not cfg.CHANNEL_ID:
        log("config.py の CHANNEL_ID が未設定です。監視したいチャンネルのID（UC...）または @ハンドルを設定してください")
        return
    if not WEBHOOK_URL:
        log("secret.py の WEBHOOK_URL が未設定です。通知先の Discord Webhook URL を設定してください")
        return
    if not API_KEY:
        log("警告: secret.py の YOUTUBE_API_KEY が未設定です。スクレイピング方式で動作しますが、"
            "検知の精度と速さが落ちます")
    if not cfg.CHANNEL_NAME:
        log("警告: config.py の CHANNEL_NAME が未設定です。通知文面のチャンネル名が空欄になります")
    log(f"youtube_notifier started (api={'on' if API_KEY else 'off'}, "
        f"interval: high={cfg.POLL_INTERVAL_HIGH_SEC}s / low={cfg.POLL_INTERVAL_LOW_SEC}s)")
    state = load_state()
    last_interval = None
    while True:
        try:
            poll(state)
        except Exception as e:
            log(f"poll error: {e}")
        interval = current_interval()
        if interval != last_interval:
            log(f"poll interval: {interval}s")
            last_interval = interval
        time.sleep(interval)


if __name__ == "__main__":
    main()
