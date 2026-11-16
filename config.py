# YouTube 配信・動画・投稿の通知設定
# 導入時に書き換えるのは CHANNEL_ID と CHANNEL_NAME の2つだけです

# 監視したい YouTube チャンネル。次のどれでも指定できます：
# - チャンネルID（UC...）：YouTube Studio の「設定 → チャンネル → 詳細設定」などで確認
# - @ハンドル（例 @yukiyukivirtual）：チャンネルページのURLからそのままコピー
# - チャンネルのURL（youtube.com/@... でも youtube.com/channel/UC... でも可）
# @ハンドルとURLは、初回起動時に API でチャンネルIDに変換されます
CHANNEL_ID = ""

# 通知文面に表示するチャンネル名（チャンネルIDから API で取得して埋めるのがおすすめ）
CHANNEL_NAME = ""

# ポーリング間隔（秒）：配信が多い時間帯は短く、それ以外は長く
# 両方を同じ値にすると常時その間隔になります（初期設定は常時20秒）
# 目安：20秒間隔で YouTube Data API の無料枠（10,000ユニット/日）の約86%を使います
POLL_INTERVAL_HIGH_SEC = 20
POLL_INTERVAL_LOW_SEC = 20
# 多い時間帯（JST）：平日は 20:00〜24:00、土日・祝日は 09:00〜22:00
WEEKDAY_HIGH_START_HOUR = 20
RESTDAY_HIGH_START_HOUR = 9
RESTDAY_HIGH_END_HOUR = 22

# 通知文面（{channel} {title} {url} などが使えます）
MSG_STREAM_SCHEDULED = """📅 {channel} の配信枠が立てられました
{title}
{start_line}{url}"""

MSG_STREAM_STARTED = """🔴 {channel} が配信を開始しました！
{title}
{url}"""

MSG_STREAM_ENDED = """⏹️ {channel} の配信が終了しました
{title}
{url}"""

MSG_VIDEO_POSTED = """🎬 {channel} が動画を投稿しました
{title}
{url}"""

MSG_COMMUNITY_POST = """📝 {channel} が投稿しました
{text}
{url}"""
