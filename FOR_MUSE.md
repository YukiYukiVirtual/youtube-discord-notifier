# 導入担当 Muse への指示書（youtube-discord-notifier）

このファイルは、利用者から導入を頼まれた Muse が読むための指示書です。
人間の利用者は読む必要がありません。利用者はこのパッケージを zip または [GitHub リポジトリ](https://github.com/YukiYukiVirtual/youtube-discord-notifier) の形であなたに渡して「導入して」と伝えただけです。ファイル一式を用意できたら、まずこのファイルを通読してください。

まず README.md と main.py、config.py、secret-converter.html を読んで、仕組みと前提を理解してから進めてください。

## 前提となる構成

- 本体は systemd ユーザーサービスとして動かし、`Restart=always` にする
- 死活確認（ハートビート）は、Muse 標準のハートビート（30分間隔で自動的に動く仕組み）に担わせる。あなたの HEARTBEAT.md に watchdog チェックを1行追加し、ハートビートのたびに watchdog スクリプトが実行されて、止まっていれば再起動する。これにより、作業環境が基盤更新で入れ替わって本体が止まっても、次回のハートビートで自動復旧する構成にする
- 生存信号は `state.json` の更新時刻。本体はチェックのたびにこのファイルを書き直すので、ファイルが古いままならループ停止と判断できる（本体への改造は不要）

## 必要な入力（3つ）

1. 通知先 Discord チャンネルの Webhook URL（秘密情報）
2. YouTube Data API v3 の APIキー（秘密情報）
3. 監視したい YouTube チャンネル（チャンネルID（UC...）／ @ハンドル ／ チャンネルURL のいずれか。プログラム側で自動的にIDへ解決するので、利用者から聞いた値をそのまま config.py の CHANNEL_ID に設定すること）

準備がまだの入力があれば、README の「事前準備」の手順を利用者に案内すること。

## 秘密情報の受け渡し（公開鍵暗号方式・厳密にこの手順で行うこと）

Webhook URL と APIキーは秘密情報です。**利用者にチャットで値を貼らせないこと。** 受け渡しは必ず次の一連の手順で行うこと。

### 厳守事項

- 秘密鍵ファイルは **絶対に開かない**。内容を会話・ログ・他のファイルに **出力しない**
- 復号した平文も同様に出力しない。復号プログラムの出力は成否だけにすること
- 検証は「設定済みか」「API が応答するか」だけに留め、値そのものを確認しないこと
- 秘密鍵や平文が漏れた、または漏れた可能性がある場合は、その時点で作業を止めて利用者に知らせること。鍵ペアと変換スペースは作り直しになる

### 手順

1. 配置フォルダ内に一時鍵ペア（RSA 4096bit）を生成する。

   ```bash
   mkdir -p .tmpkey && chmod 700 .tmpkey
   openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:4096 -out .tmpkey/private.pem
   chmod 600 .tmpkey/private.pem
   openssl pkey -in .tmpkey/private.pem -pubout -outform DER | base64 -w0
   ```

   最後のコマンドの出力が公開鍵（SPKI 形式、Base64、1行）。公開鍵は秘密ではないので、そのまま使ってよい。

2. 同梱の **secret-converter.html を正本として**、フルスタックの「シークレット変換スペース」を作成する。
   - ファイル内の `__PUBLIC_KEY_SPKI_B64__` を、手順1の公開鍵に置換する
   - 保存アクションは1つだけ用意する：`webhook_ct` と `apikey_ct`（Base64 文字列）を受け取り、レコードに保存するだけのものにし、ページの `window.__saveCiphertext(webhook_ct, apikey_ct)` から呼べるよう接続する。アクション側で復号・加工はしない
   - secret-converter.html の「暗号化コア」部分のロジックは一切変更しない。外部リソースを読み込む形にも変更しない
   - 詳細なルールは secret-converter.html 先頭のコメントにも書かれているので、そちらも守ること

3. 利用者にスペースで Webhook URL と APIキーを入力・送信してもらう。暗号化はブラウザ内（WebCrypto、RSA-OAEP / SHA-256）で完結し、スペースに保存されるのは暗号文だけである。
   - 送信に失敗した場合のフォールバックとして、ページには暗号文の表示欄が出る。その場合は暗号文を利用者から受け取ってよい（暗号文は秘密ではない）

4. スペースの保存先から暗号文（`webhook_ct`、`apikey_ct`）を取得し、復号して `secret.py` を組み立てる。
   - 復号は openssl を使う（パラメータは検証済みの次の形を使うこと）：

     ```bash
     # 暗号文（Base64）をファイルに書き、デコードしてから復号する
     openssl pkeyutl -decrypt -inkey .tmpkey/private.pem -in ct.bin \
       -pkeyopt rsa_padding_mode:oaep \
       -pkeyopt rsa_oaep_md:sha256 \
       -pkeyopt rsa_mgf1_md:sha256 -out pt.txt
     ```

   - `secret.py` の形式は secret.py.example と同じ（`WEBHOOK_URL = "..."`、`YOUTUBE_API_KEY = "..."`）。復号した平文ファイル（pt.txt 等）は、secret.py を書き終えたら即座に `shred -u` で消すこと
   - この一連の処理を行うスクリプトは、標準出力・標準エラーに平文や秘密鍵の内容を含めないこと（成否だけを出す）

5. `secret.py` の検証を、値を見ずに行う。
   - secret.py を読み込むスクリプトで YouTube Data API を1回叩き、成否だけを得る
   - Webhook は GET で疎通だけ確認する（**テスト投稿はしない**）
   - 検証に失敗したら、手順1の鍵生成からやり直すこと

6. 完了処理：
   - `.tmpkey/private.pem` を `shred -u` で破棄し、`.tmpkey` フォルダも消す
   - 変換スペースを削除する（保存されていた暗号文も一緒に消える）
   - 鍵が消えた時点で、途中に残った暗号文は二度と復号できない状態になる

## 導入手順

1. このフォルダをワークスペース内の永続化される場所に配置する（例：`~/workspace/youtube-discord-notifier`）
2. Python の仮想環境（venv）を作り、`requirements.txt` をインストールする
3. 上記の公開鍵方式で `secret.py` を用意する
   - `config.py` の `CHANNEL_ID` は利用者から聞いた値を設定する
   - `CHANNEL_NAME` は、secret.py の APIキーを使うスクリプト経由で YouTube Data API（channels.list の snippet）からチャンネル名を取得して自動設定する（キー自体は表示しないこと。チャンネルが @ハンドルで指定された場合は、channels.list の forHandle で取得できる）
4. systemd ユーザーサービスを作成し、有効化して起動する。初回は既存の動画・配信が「見済み」として取り込まれて通知が出ないこと（seeded のログ）を確認する
5. ハートビートを設定する：
   - 配置場所に watchdog.sh を作成する（内容は下記。DIR は配置場所に合わせること）
   - あなたの HEARTBEAT.md に、次の趣旨のチェックを1行追加する：「youtube-discord-notifier の watchdog.sh（配置場所のパス）を実行し、出力が OK なら何もしない。RECOVERED なら再起動した旨を短く利用者に知らせる。FAILED、または短時間に再起動を繰り返す場合は強めに知らせる」
   - ハートビートは Muse 標準の仕組みで30分間隔に動く。専用の cron は作らないこと（より速い復旧を利用者が希望した場合のみ、cron の追加を提案する）
6. 復旧テストをする：サービスを手動で止めてから watchdog.sh を実行し、再起動して active に戻ることを確認する。**これが通るまでは完了としないこと**
7. 完了報告には次を含める：インストール場所、サービスの状態確認・再起動の方法、ハートビートの間隔、YouTube API の消費目安（20秒間隔で約8,640ユニット/日、無料枠10,000/日）、「作業環境の入れ替わり直後は最大でハートビート間隔ぶん起動が遅れることがある」旨の注意

## 設定変更について

- 導入時は config.py のデフォルト値のまま進めること。設定項目を一つずつ利用者に確認しないこと（チェック間隔や通知文面などの設定項目があることだけ、完了報告で伝える）
- 導入後に利用者から設定変更を頼まれたら、config.py の該当項目を編集し、サービスを再起動して反映すること
- チェック間隔を変更する場合は、YouTube API クォータへの影響を利用者に伝えること（目安：20秒間隔 ≒ 8,640ユニット/日、60秒間隔 ≒ 2,880ユニット/日、無料枠は10,000/日）

## systemd ユニット

`~/.config/systemd/user/youtube-discord-notifier.service`:

```ini
[Unit]
Description=YouTube stream notifier
After=network-online.target

[Service]
WorkingDirectory=/path/to/youtube-discord-notifier
ExecStart=/path/to/youtube-discord-notifier/venv/bin/python -u main.py
Restart=always
RestartSec=10

[Install]
WantedBy=default.target
```

## watchdog.sh

```bash
#!/usr/bin/env bash
# youtube-discord-notifier の死活確認と自動復旧（ハートビート用）
set -u
DIR="$HOME/workspace/youtube-discord-notifier"   # 配置場所に合わせて変更
SERVICE="youtube-discord-notifier.service"
STATE="$DIR/state.json"
STALE_SEC=300   # state.json がこの秒数より古ければ停止とみなす

# watchdog.log 自体の肥大化防止（1MBを超えたら末尾200行だけ残す）
if [ -f "$DIR/watchdog.log" ] && [ "$(stat -c %s "$DIR/watchdog.log" 2>/dev/null || echo 0)" -gt 1000000 ]; then
  tail -n 200 "$DIR/watchdog.log" > "$DIR/watchdog.log.tmp" && mv "$DIR/watchdog.log.tmp" "$DIR/watchdog.log"
fi

reason=""
if ! systemctl --user is-active --quiet "$SERVICE"; then
  reason="service is not active"
else
  now=$(date +%s)
  mtime=$(stat -c %Y "$STATE" 2>/dev/null || echo 0)
  if [ $((now - mtime)) -gt $STALE_SEC ]; then
    reason="state.json is stale"
  fi
fi

if [ -n "$reason" ]; then
  echo "$(date -Is) watchdog: $reason -> restart" >> "$DIR/watchdog.log"
  systemctl --user restart "$SERVICE"
  sleep 5
  if systemctl --user is-active --quiet "$SERVICE"; then
    echo "RECOVERED: $reason"
  else
    echo "FAILED: could not restart ($reason)"
  fi
else
  echo "OK"
fi
```
