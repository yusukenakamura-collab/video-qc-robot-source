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
# Step 1-3でDLしたJSONキーファイルの中身を、一字一句そのままここに貼り付け
SERVICE_ACCOUNT_INFO = { 
  "type": "service_account",
  "project_id": "your-project-id",
  "private_key_id": "your-private-key-id",
  "private_key": "-----BEGIN PRIVATE KEY-----\\nYOUR_PRIVATE_KEY\\n-----END PRIVATE KEY-----\\n",
  "client_email": "video-qc-robot@your-project-id.iam.gserviceaccount.com",
  "client_id": "your-client-id",
  "auth_uri": "https://accounts.google.com/o/oauth2/auth",
  "token_uri": "https://oauth2.googleapis.com/token",
  "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
  "client_x509_cert_url": "https://www.googleapis.com/robot/v1/metadata/x509/video-qc-robot%40your-project-id.iam.gserviceaccount.com"
}
# スプレッドシートのURLからIDをコピーして貼り付け (例: .../d/ココがID/edit)
SPREADSHEET_ID = 'ここに「動画チェックレポート」のスプレッドシートID' 
# 各フォルダのURLからIDをコピーして貼り付け (例: .../folders/ココがID)
TEMP_FRAMES_FOLDER_ID = 'ここに「frames」のID'
TEMP_AUDIO_FOLDER_ID = 'ここに「audio」のID'
TEMP_LOGS_FOLDER_ID = 'ここに「analysis_logs」のID'
ALERTS_FOLDER_ID = 'ここに「5_Alerts」のID'
# --- ▲▲▲ あなたの設定エリア ▲▲▲ ---

def get_google_service(scopes):
    """指定された権限でGoogle APIサービスを準備する"""
    return service_account.Credentials.from_service_account_info(SERVICE_ACCOUNT_INFO, scopes=scopes)

def get_processed_files(sheets_service):
    """台帳シートから処理済みのファイル名リストを取得する"""
    try:
        result = sheets_service.spreadsheets().values().get(spreadsheetId=SPREADSHEET_ID, range='ProcessingLog!A2:A').execute()
        values = result.get('values', [])
        return {row[0] for row in values if row}
    except Exception as e:
        print(f"処理済みリスト取得エラー: {e}")
        return set()

def log_processed_file(sheets_service, file_name):
    """台帳シートに処理済みファイル名を記録する"""
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    body = {'values': [[file_name, timestamp]]}
    sheets_service.spreadsheets().values().append(spreadsheetId=SPREADSHEET_ID, range='ProcessingLog!A1', valueInputOption='USER_ENTERED', body=body).execute()

def create_alert_file(drive_service, video_name):
    """重複アラート用のファイルを作成する"""
    alert_message = f"重複ファイルがアップロードされました。\n\nファイル名: {video_name}\n日時: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    alert_file_name = f"ALERT_DUPLICATE_{video_name}.txt"
    local_alert_path = f"/tmp/{alert_file_name}"
    with open(local_alert_path, 'w') as f: f.write(alert_message)
    media = MediaFileUpload(local_alert_path, mimetype='text/plain')
    drive_service.files().create(body={'name': alert_file_name, 'parents': [ALERTS_FOLDER_ID]}, media_body=media).execute()

@app.route("/", methods=["POST"])
def process_video_handler():
    """EventarcからのHTTP POSTリクエストを受け取り、全処理を実行するメイン関数"""
    event_data = request.get_json()
    print(f"イベント受信: {event_data}")

    ce_subject = event_data.get('subject', '')
    if not ce_subject.startswith('objects/'):
        return "Invalid event subject", 400

    file_name_from_trigger = ce_subject.replace('objects/', '')
    bucket_name = event_data.get('bucket')

    # --- サービス準備 ---
    scopes = ['https://www.googleapis.com/auth/drive', 'https://www.googleapis.com/auth/spreadsheets']
    creds = get_google_service(scopes)
    drive_service = build('drive', 'v3', credentials=creds)
    sheets_service = build('sheets', 'v4', credentials=creds)
    storage_client = storage.Client()
    blob = storage_client.bucket(bucket_name).blob(file_name_from_trigger)

    # --- 入力ソースの判定とダウンロード ---
    if is_url_source := file_name_from_trigger.lower().endswith('.txt'):
        url = blob.download_as_text().strip()
        if not ("youtube.com" in url or "youtu.be" in url or "vimeo.com" in url): return "Unsupported URL", 400
        
        local_video_path = "/tmp/downloaded_video.mp4"
        subprocess.run(['yt-dlp', '-f', 'best[ext=mp4]', '-o', local_video_path, url], check=True)
        video_name = subprocess.check_output(['yt-dlp', '--get-title', url], universal_newlines=True).strip().replace('/', '_')
    else:
        video_name = file_name_from_trigger
        local_video_path = f"/tmp/{video_name}"
        blob.download_to_filename(local_video_path)

    # --- 重複チェック ---
    if video_name in get_processed_files(sheets_service):
        print(f"重複ファイルのためアラート生成: {video_name}")
        create_alert_file(drive_service, video_name)
        blob.delete()
        return "Duplicate file processed", 200

    # --- 機能選択 ---
    run_v = any(tag in video_name.lower() for tag in ['[all]', '[v]'])
    run_a = any(tag in video_name.lower() for tag in ['[all]', '[a]'])
    if not run_v and not run_a: 
        blob.delete()
        return "No valid tag found", 200

    # --- 解析実行 ---
    local_log_path = f"/tmp/{video_name}_log.txt"
    with open(local_log_path, 'w') as f:
        # 1. ffprobeメタデータ解析
        try:
            ffprobe_cmd = ['ffprobe', '-v', 'quiet', '-print_format', 'json', '-show_streams', '-show_format', local_video_path]
            metadata = json.loads(subprocess.check_output(ffprobe_cmd))
            f.write("[METADATA]\n")
            video_stream = next((s for s in metadata.get('streams', []) if s.get('codec_type') == 'video'), {})
            f.write(f"resolution: {video_stream.get('width')}x{video_stream.get('height')}\n")
            f.write(f"codec: {video_stream.get('codec_name')}\n")
            num, den = map(int, video_stream.get('avg_frame_rate', '0/1').split('/'))
            f.write(f"frame_rate: {round(num/den, 2) if den else 0}\n")
            f.write(f"bitrate_kbps: {round(int(metadata.get('format', {}).get('bit_rate', 0)) / 1000)}\n")
        except Exception as e:
            f.write(f"[ERROR] ffprobe failed: {e}\n")

    # 2. ffmpeg技術チェック (ログに追記)
    tech_check_cmd = f"ffmpeg -i {local_video_path} -t 15 -vf \"blackdetect=d=0.1,cropdetect,signalstats,metadata=print:key=lavfi.signalstats.YMAX\" -af \"silencedetect=n=-30dB:d=2\" -f null - 2>> {local_log_path}"
    subprocess.run(tech_check_cmd, shell=True)
    
    # 3. 音声解析 (run_aがTrueの場合)
    if run_a:
        local_audio_path = f"/tmp/audio.flac"
        subprocess.run(f"ffmpeg -i {local_video_path} -vn -ar 16000 -ac 1 {local_audio_path}", shell=True, check=True)
        subprocess.run(f"ffmpeg -i {local_audio_path} -af ebur128=peak=true -f null - 2>> {local_log_path}", shell=True)
        # (ここにノイズ・ハム音チェックのコマンドを追加)
        media_audio = MediaFileUpload(local_audio_path, mimetype='audio/flac')
        drive_service.files().create(body={'name': f"{video_name}.flac", 'parents': [TEMP_AUDIO_FOLDER_ID]}, media_body=media_audio).execute()

    # 4. 映像解析 (run_vがTrueの場合)
    if run_v:
        frames_dir = "/tmp/frames"
        os.makedirs(frames_dir, exist_ok=True)
        subprocess.run(f"ffmpeg -i {local_video_path} -vf fps=1 {frames_dir}/frame_%04d.jpg", shell=True)
        with open(local_log_path, 'a') as main_log:
            for frame_file in sorted(os.listdir(frames_dir)):
                image = cv2.imread(os.path.join(frames_dir, frame_file))
                if image is None: continue
                gray = cv2.cvtColor(image, cv2.COLOR_BGR_GRAY)
                variance = cv2.Laplacian(gray, cv2.CV_64F).var()
                if variance < 100:
                    timestamp = int(re.sub(r'[^0-9]', '', frame_file))
                    main_log.write(f"Blur detected at {timestamp}s, variance: {variance:.2f}\n")
        
        # (フレームのアップロード処理は省略、必要に応じて追加)

    # --- 最終処理 ---
    media_log = MediaFileUpload(local_log_path, mimetype='text/plain')
    drive_service.files().create(body={'name': f"{video_name}_log.txt", 'parents': [TEMP_LOGS_FOLDER_ID]}, media_body=media_log).execute()
    
    log_processed_file(sheets_service, video_name)
    blob.delete()
    print("処理完了。")
    return "OK", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
