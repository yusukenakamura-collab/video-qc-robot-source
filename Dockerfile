# === ステップ1: ベースとなる環境の準備 ===
# 公式のPython 3.9の軽量版イメージを土台として使います。
# これにより、PythonがすでにインストールされたクリーンなLinux環境からスタートできます。
FROM python:3.9-slim

# === ステップ2: アプリケーションを置く作業フォルダの作成 ===
# コンテナ内に "/app" という名前のフォルダを作成し、
# 以降の作業は全てこのフォルダ内で行うように設定します。
ENV APP_HOME /app
WORKDIR $APP_HOME

# === ステップ3: 必要な道具（ライブラリ）のインストール ===
# まず、道具リストである `requirements.txt` のみを先にコピーします。
COPY requirements.txt .
# 次に、そのリストに従って、pipコマンドで全てのライブラリをインストールします。
# `--no-cache-dir` は、不要なキャッシュファイルを残さないための効率化オプションです。
RUN pip install --no-cache-dir -r requirements.txt

# === ステップ4: ロボットの頭脳（アプリケーションコード）の配置 ===
# カレントディレクトリにある全てのファイル（main.pyなど）を、
# コンテナ内の作業フォルダ（/app）にコピーします。
COPY . .

# === ステップ5: サーバーの起動命令 ===
# このコンテナが起動したときに、最初に実行するコマンドを指定します。
# gunicornというWebサーバーを起動し、Cloud Runが指定するポート（$PORT）で、
# `main.py` ファイルの中にある `app` という名前のFlaskアプリケーションを動かします。
# `--timeout 0` は、リクエストの処理時間が長くてもタイムアウトしないようにする設定です。
CMD exec gunicorn --bind :$PORT --workers 1 --threads 8 --timeout 0 main:app