import datetime as dt
import json
from typing import Any, Dict

import google.generativeai as genai
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

# 後でご自身のキーに書き換えてください
GEMINI_API_KEY = "YOUR_GEMINI_API_KEY"

# Google Calendar API scope
SCOPES = ["https://www.googleapis.com/auth/calendar.events"]

# 利用するモデル（無料・高速）
MODEL_NAME = "gemini-1.5-flash"


def get_now_jst() -> dt.datetime:
    """現在時刻（JST, +09:00）を返す。"""
    jst = dt.timezone(dt.timedelta(hours=9))
    return dt.datetime.now(jst)


def ask_gemini_to_parse_schedule(user_text: str, now_jst: dt.datetime) -> Dict[str, str]:
    """自然言語を Gemini に渡し、予定情報JSONを返させる。"""
    if not GEMINI_API_KEY or GEMINI_API_KEY == "YOUR_GEMINI_API_KEY":
        raise ValueError("GEMINI_API_KEY が未設定です。main.py 内の値を書き換えてください。")

    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel(MODEL_NAME)

    prompt = f"""
あなたは予定抽出アシスタントです。次の入力からGoogleカレンダー登録用データを作ってください。

# 現在日時（JST）
{now_jst.isoformat()}

# ユーザー入力
{user_text}

# 必須ルール
- 出力は JSON オブジェクトのみ（前後に説明文をつけない）
- キーは必ず summary, start, end の3つ
- start/end は ISO8601 形式で、必ず JST オフセット +09:00 を含める
- end は start より後にする
- 時刻が省略されている場合は、開始を09:00、終了を10:00にする
- 日付が曖昧な場合は、現在日時を基準に最も自然な解釈を採用する

# 出力例
{{
  "summary": "インターン",
  "start": "2026-02-19T11:00:00+09:00",
  "end": "2026-02-19T16:00:00+09:00"
}}
""".strip()

    response = model.generate_content(
        prompt,
        generation_config={"temperature": 0.0, "response_mime_type": "application/json"},
    )

    text = (response.text or "").strip()
    if not text:
        raise ValueError("Gemini から空のレスポンスが返されました。")

    try:
        data: Dict[str, Any] = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"GeminiのレスポンスがJSONとして解釈できません: {text}") from e

    for key in ("summary", "start", "end"):
        if key not in data:
            raise ValueError(f"Geminiレスポンスに必須キー '{key}' がありません: {data}")

    summary = str(data["summary"])
    start = str(data["start"])
    end = str(data["end"])

    # ISO8601 と時間順序を軽く検証
    start_dt = dt.datetime.fromisoformat(start)
    end_dt = dt.datetime.fromisoformat(end)
    if start_dt.tzinfo is None or end_dt.tzinfo is None:
        raise ValueError("start/end にタイムゾーン情報がありません。")
    if start_dt.utcoffset() != dt.timedelta(hours=9) or end_dt.utcoffset() != dt.timedelta(hours=9):
        raise ValueError("start/end のタイムゾーンが JST (+09:00) ではありません。")
    if end_dt <= start_dt:
        raise ValueError("end は start より後である必要があります。")

    return {"summary": summary, "start": start, "end": end}


def get_calendar_service():
    """credentials.json を使って Calendar API のサービスを作成する。"""
    creds = None
    token_path = "token.json"
    credentials_path = "credentials.json"

    try:
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)
    except Exception:
        creds = None

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(credentials_path, SCOPES)
            creds = flow.run_local_server(port=0)

        with open(token_path, "w", encoding="utf-8") as token:
            token.write(creds.to_json())

    return build("calendar", "v3", credentials=creds)


def insert_event_to_calendar(event_data: Dict[str, str]) -> Dict[str, Any]:
    """Google Calendar に予定を登録する。"""
    service = get_calendar_service()

    event = {
        "summary": event_data["summary"],
        "start": {"dateTime": event_data["start"], "timeZone": "Asia/Tokyo"},
        "end": {"dateTime": event_data["end"], "timeZone": "Asia/Tokyo"},
    }

    created_event = service.events().insert(calendarId="primary", body=event).execute()
    return created_event


def main() -> None:
    user_input = input("予定を入力してください（例: 明日 11-16 インターン）: ").strip()
    if not user_input:
        print("入力が空です。終了します。")
        return

    now_jst = get_now_jst()
    parsed = ask_gemini_to_parse_schedule(user_input, now_jst)
    insert_event_to_calendar(parsed)

    print(
        f"登録しました: {parsed['summary']} {parsed['start']}〜{parsed['end']}"
    )


if __name__ == "__main__":
    main()
