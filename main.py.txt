import os, re, json, subprocess, cv2
import numpy as np
from datetime import datetime
from google.cloud import storage
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from flask import Flask, request

app = Flask(__name__)

# --- ▼▼▼ あなたの設定エリア ▼▼▼ ---
# (これまでと同じ設定をここに貼り付け)
SERVICE_ACCOUNT_INFO = { ... }
SPREADSHEET_ID = '...' 
TEMP_FRAMES_FOLDER_ID = '...'
# ... (他も同様)
# --- ▲▲▲ あなたの設定エリア ▲▲▲ ---

# (get_google_service, get_processed_files, log_processed_file, create_alert_file 関数の定義は変更なし)

@app.route("/", methods=["POST"])
def process_video_handler():
    # Eventarcから送られてくるイベントデータを取得
    event_data = request.get_json()
    print(f"イベント受信: {event_data}")

    # CloudEventの形式からファイル情報を取り出す
    ce_subject = event_data.get('subject', '')
    if not ce_subject.startswith('objects/'):
        return "Invalid event subject", 400

    file_name_from_trigger = ce_subject.replace('objects/', '')
    bucket_name = event_data.get('bucket')

    # --- ここから下のロジックは、Cloud Functions版の process_video 関数とほぼ同じ ---
    scopes = ['https://www.googleapis.com/auth/drive', 'https://www.googleapis.com/auth/spreadsheets']
    creds = get_google_service(scopes)
    # ... (以降、Cloud Functions版の全処理ロジックをここに移植) ...

    # 処理が正常に完了したことを示す
    return "OK", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))