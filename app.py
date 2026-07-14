# -*- coding: utf-8 -*-
"""
Streamlit 英語スピーキングテストシステム
=========================================

■ 設計方針（仕様指示書の課題対策）
----------------------------------
① Gemini APIモデルエラー(404)対策
   - 新しい統合SDK `google-genai` を使用（レガシー `google.generativeai` は不使用）。
   - CANDIDATE_MODELS の順にモデルを自動フォールバックで試行し、成功したモデル名を
     st.session_state にキャッシュ。次回以降はキャッシュ済みモデルを最優先で使うため、
     無駄なリトライが発生しない。

② Streamlitのリランに伴う非同期処理の不安定さ対策
   - threading による裏側処理は一切行わない。
   - 「次の問題へ／送信する」ボタン押下の瞬間に st.spinner を表示しながら
     "その1問分だけ" を同期的に文字起こしする設計（仕様書②の対策要求どおり）。
   - これにより st.rerun() でスレッドが死んで文字起こし失敗、という現象が原理的に起きない。

■ 再生回数カウントについての設計変更
   - 仕様書では「JSで自動カウント」とありましたが、StreamlitはJS側のイベントを
     Python の session_state に安全かつリアルタイムに同期する標準手段がなく
     （双方向カスタムコンポーネントの実装が必要でかなり壊れやすい）、
     今回の「安定動作」という最優先要件と衝突します。
   - そのため「▶️ 音声を再生」ボタンを押すたびに st.session_state 側でカウントし、
     押下と同時に st.audio(autoplay=True) で再生する方式に変更しています。
   - ユーザー操作＝Python側のrerun起点なので、カウント漏れ・二重カウントが起きません。
     （JS版が必須の場合は streamlit-javascript 等のカスタムコンポーネント導入が必要である旨
     をコード末尾のコメントに記載しています）

■ 必要パッケージ (requirements.txt)
    streamlit>=1.38
    gtts
    gspread
    google-auth
    google-api-python-client
    google-genai
"""

import io
import json
import time
import uuid
from datetime import datetime

import streamlit as st
import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
from gtts import gTTS

from google import genai
from google.genai import types


# =========================================================
# 定数・設定
# =========================================================
SHARED_DRIVE_ID = "0ACP5Eu-XLix6Uk9PVA"  # 保存先共有ドライブID
QUESTIONS_SHEET_NAME = "Questions"

# 2026年7月時点で利用可能な可能性が高い順にフォールバック
CANDIDATE_MODELS = [
    "gemini-2.5-flash",
    "gemini-2.0-flash",
    "gemini-1.5-flash",
    "gemini-1.5-flash-latest",
]

TRANSCRIBE_PROMPT = (
    "Transcribe the following English audio precisely. "
    "Output ONLY the text. If silent or no speech, output 'No speech'."
)

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


# =========================================================
# 外部サービスのクライアント初期化（キャッシュ）
# =========================================================
@st.cache_resource(show_spinner=False)
def get_google_credentials():
    sa_info = json.loads(st.secrets["GOOGLE_SERVICE_ACCOUNT_JSON"])
    return Credentials.from_service_account_info(sa_info, scopes=SCOPES)


@st.cache_resource(show_spinner=False)
def get_gspread_client():
    creds = get_google_credentials()
    return gspread.authorize(creds)


@st.cache_resource(show_spinner=False)
def get_drive_service():
    creds = get_google_credentials()
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def get_genai_client() -> genai.Client:
    """Gemini APIクライアント。session_state内にキャッシュ（cache_resourceだと
    secrets変更時に扱いにくいので明示的にsession_stateで持つ）。"""
    if "genai_client" not in st.session_state:
        st.session_state.genai_client = genai.Client(api_key=st.secrets["GEMINI_API_KEY"])
    return st.session_state.genai_client


# =========================================================
# Gemini 文字起こし（モデル自動フォールバック＋キャッシュ）
# =========================================================
def transcribe_audio(audio_bytes: bytes) -> str:
    """1問分の音声(WAV bytes)を同期的に文字起こしする。
    複数モデルを順に試し、成功したモデル名は session_state にキャッシュする。
    """
    client = get_genai_client()

    # 過去に成功したモデルを最優先で試す
    models_to_try = []
    cached_model = st.session_state.get("working_gemini_model")
    if cached_model:
        models_to_try.append(cached_model)
    for m in CANDIDATE_MODELS:
        if m not in models_to_try:
            models_to_try.append(m)

    last_error = None
    for model_name in models_to_try:
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=[
                    types.Part.from_bytes(data=audio_bytes, mime_type="audio/wav"),
                    TRANSCRIBE_PROMPT,
                ],
            )
            # 成功したモデルをキャッシュ（次回以降はこのモデルを最優先で試行）
            st.session_state["working_gemini_model"] = model_name
            text = (response.text or "").strip()
            return text if text else "No speech"
        except Exception as e:  # noqa: BLE001 - 404やモデル未対応を含め広く捕捉してフォールバック
            last_error = e
            continue

    # 全モデル失敗
    st.session_state.pop("working_gemini_model", None)
    return f"[文字起こし失敗: 利用可能なGeminiモデルが見つかりませんでした / {last_error}]"


# =========================================================
# スプレッドシート関連
# =========================================================
def load_questions() -> list[str]:
    """'Questions' シートから問題文一覧を読み込む。
    A列（1列目）にヘッダー行＋問題文が並んでいる想定。ヘッダーは自動でスキップ。
    """
    gc = get_gspread_client()
    sh = gc.open_by_key(st.secrets["SPREADSHEET_ID"])
    ws = sh.worksheet(QUESTIONS_SHEET_NAME)
    values = ws.col_values(1)
    if not values:
        return []
    # 1行目が "Question" 等のヘッダーらしければ除外
    if values[0].strip().lower() in ("question", "questions", "問題", "問題文"):
        values = values[1:]
    return [v for v in values if v.strip()]


def get_or_create_class_sheet(class_name: str):
    gc = get_gspread_client()
    sh = gc.open_by_key(st.secrets["SPREADSHEET_ID"])
    try:
        ws = sh.worksheet(class_name)
    except gspread.exceptions.WorksheetNotFound:
        ws = sh.add_worksheet(title=class_name, rows=1000, cols=50)
        header = ["タイムスタンプ", "クラス", "番号", "氏名"]
        for i in range(1, len(st.session_state.answers) + 1):
            header += [f"Q{i}_音声リンク", f"Q{i}_文字起こし", f"Q{i}_評価", f"Q{i}_ステータス", f"Q{i}_再生回数"]
        ws.append_row(header)
    return ws


def append_result_row(class_name: str, number: str, name: str, answers: list[dict]):
    ws = get_or_create_class_sheet(class_name)
    row = [
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        class_name,
        number,
        name,
    ]
    for ans in answers:
        row += [
            ans.get("drive_link", ""),
            ans.get("transcript", ""),
            "提出済",
            "正常に受付",
            ans.get("play_count", 0),
        ]
    ws.append_row(row)


# =========================================================
# Google Drive アップロード
# =========================================================
def upload_audio_to_drive(audio_bytes: bytes, filename: str) -> str:
    """共有ドライブへWAVをアップロードし、共有リンクを返す。"""
    service = get_drive_service()
    file_metadata = {
        "name": filename,
        "parents": [SHARED_DRIVE_ID],
    }
    media = MediaIoBaseUpload(io.BytesIO(audio_bytes), mimetype="audio/wav", resumable=False)
    file = service.files().create(
        body=file_metadata,
        media_body=media,
        fields="id, webViewLink",
        supportsAllDrives=True,
    ).execute()

    file_id = file["id"]
    # リンクを知っている全員が閲覧可能に設定
    service.permissions().create(
        fileId=file_id,
        body={"type": "anyone", "role": "reader"},
        supportsAllDrives=True,
    ).execute()

    return file.get("webViewLink", f"https://drive.google.com/file/d/{file_id}/view")


# =========================================================
# gTTS 音声生成（キャッシュ）
# =========================================================
@st.cache_data(show_spinner=False)
def generate_question_audio(text: str) -> bytes:
    buf = io.BytesIO()
    gTTS(text=text, lang="en").write_to_fp(buf)
    return buf.getvalue()


# =========================================================
# セッション状態の初期化
# =========================================================
def init_session_state():
    defaults = {
        "step": "init",
        "class_name": "",
        "number": "",
        "name_katakana": "",
        "questions": [],
        "current_q_index": 0,
        "answers": [],  # 各要素: {question, audio_bytes, transcript, play_count, drive_link}
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def reset_for_next_student():
    for key in ["class_name", "number", "name_katakana", "questions",
                "current_q_index", "answers"]:
        st.session_state.pop(key, None)
    st.session_state["step"] = "init"
    init_session_state()


# =========================================================
# 画面1：受験者情報入力
# =========================================================
def render_init_screen():
    st.title("🎙️ 英語スピーキングテスト")
    st.subheader("受験者情報の入力")

    with st.form("init_form"):
        class_name = st.text_input("クラス", value=st.session_state.class_name)
        number = st.selectbox(
            "名簿番号",
            options=[str(i) for i in range(1, 46)],
            index=0,
        )
        name_katakana = st.text_input("氏名（カタカナ）", value=st.session_state.name_katakana)
        submitted = st.form_submit_button("テストを開始する", type="primary")

    if submitted:
        if not class_name.strip() or not name_katakana.strip():
            st.error("クラスと氏名を入力してください。")
            return

        with st.spinner("問題を読み込んでいます..."):
            try:
                questions = load_questions()
            except Exception as e:  # noqa: BLE001
                st.error(f"問題の読み込みに失敗しました: {e}")
                return

        if not questions:
            st.error("'Questions' シートに問題文が見つかりませんでした。")
            return

        st.session_state.class_name = class_name.strip()
        st.session_state.number = number
        st.session_state.name_katakana = name_katakana.strip()
        st.session_state.questions = questions
        st.session_state.answers = [
            {"question": q, "audio_bytes": None, "transcript": None,
             "play_count": 0, "drive_link": ""}
            for q in questions
        ]
        st.session_state.current_q_index = 0
        st.session_state.step = "test"
        st.rerun()


# =========================================================
# 画面2：テスト本番
# =========================================================
def render_test_screen():
    idx = st.session_state.current_q_index
    total = len(st.session_state.questions)
    question = st.session_state.questions[idx]
    answer = st.session_state.answers[idx]

    st.title("🎙️ 英語スピーキングテスト")
    st.progress((idx) / total, text=f"Q{idx + 1} / {total}")
    st.subheader(f"Q{idx + 1}")
    st.write(question)

    # --- 問題音声の再生（ボタン押下ごとに再生回数をカウント） ---
    col1, col2 = st.columns([1, 3])
    with col1:
        if st.button("▶️ 音声を再生", key=f"play_{idx}"):
            st.session_state.answers[idx]["play_count"] += 1
    with col2:
        st.caption(f"再生回数: {st.session_state.answers[idx]['play_count']} 回")

    if st.session_state.answers[idx]["play_count"] > 0:
        audio_bytes = generate_question_audio(question)
        st.audio(audio_bytes, format="audio/mp3", autoplay=True)

    st.divider()

    # --- 録音 ---
    st.write("あなたの解答を録音してください。")
    recorded = st.audio_input("解答を録音", key=f"rec_{idx}")

    is_last = idx == total - 1
    button_label = "送信する" if is_last else "次の問題へ"

    if st.button(button_label, type="primary", key=f"next_{idx}"):
        if recorded is None:
            st.warning("録音が完了してから進んでください。")
            return

        audio_bytes = recorded.getvalue()

        # ★超重要：ボタンを押した瞬間に、その場で同期的に文字起こしを行う
        # （threadingは使わない。st.rerun()による処理の中断が起きないため確実）
        with st.spinner(f"Q{idx + 1} の解答を文字起こししています..."):
            transcript = transcribe_audio(audio_bytes)

        st.session_state.answers[idx]["audio_bytes"] = audio_bytes
        st.session_state.answers[idx]["transcript"] = transcript

        if is_last:
            st.session_state.step = "finish"
        else:
            st.session_state.current_q_index += 1
        st.rerun()


# =========================================================
# 画面3：送信・データ保存
# =========================================================
def render_finish_screen():
    st.title("🎙️ 英語スピーキングテスト")

    if not st.session_state.get("upload_done", False):
        with st.spinner("音声データをアップロードし、記録を保存しています..."):
            try:
                for i, ans in enumerate(st.session_state.answers):
                    if ans["audio_bytes"] is None:
                        continue
                    filename = (
                        f"{st.session_state.class_name}_{st.session_state.number}_"
                        f"{st.session_state.name_katakana}_Q{i + 1}_{uuid.uuid4().hex[:8]}.wav"
                    )
                    link = upload_audio_to_drive(ans["audio_bytes"], filename)
                    st.session_state.answers[i]["drive_link"] = link

                append_result_row(
                    st.session_state.class_name,
                    st.session_state.number,
                    st.session_state.name_katakana,
                    st.session_state.answers,
                )
                st.session_state.upload_done = True
            except Exception as e:  # noqa: BLE001
                st.error(f"保存中にエラーが発生しました: {e}")
                st.stop()

    st.success("✅ 送信が完了しました。お疲れ様でした！")
    st.write(f"クラス: {st.session_state.class_name} / 番号: {st.session_state.number} "
              f"/ 氏名: {st.session_state.name_katakana}")

    with st.expander("送信内容を確認する"):
        for i, ans in enumerate(st.session_state.answers):
            st.markdown(f"**Q{i + 1}**: {ans['question']}")
            st.write(f"文字起こし: {ans['transcript']}")
            st.write(f"再生回数: {ans['play_count']}")
            if ans["drive_link"]:
                st.write(f"音声リンク: {ans['drive_link']}")
            st.divider()

    if st.button("次の生徒の入力を開始", type="primary"):
        st.session_state.pop("upload_done", None)
        reset_for_next_student()
        st.rerun()


# =========================================================
# メイン
# =========================================================
def main():
    st.set_page_config(page_title="英語スピーキングテスト", page_icon="🎙️", layout="centered")
    init_session_state()

    step = st.session_state.step
    if step == "init":
        render_init_screen()
    elif step == "test":
        render_test_screen()
    elif step == "finish":
        render_finish_screen()
    else:
        st.error("不明な画面状態です。リセットします。")
        reset_for_next_student()
        st.rerun()


if __name__ == "__main__":
    main()

# =========================================================
# 補足：JSでの再生回数の完全自動カウントについて
# =========================================================
# もし「ユーザーがブラウザの音声プレーヤーのシークバー等を直接操作して再生した回数」
# まで含めて厳密にJS側で自動検知したい場合は、以下いずれかの追加実装が必要です。
#   1. streamlit-javascript / streamlit.components.v1.html + postMessage を使い、
#      JS の <audio> の 'play' イベントを検知して st.query_params 経由で
#      Python側に通知し、st.rerun() でカウントを更新する（実装はやや複雑・要検証）。
#   2. streamlit-audio-recorder 系のカスタムコンポーネントを自作し、
#      Component側でカウントを保持して bidirectional に値を返す。
# 今回は「安定動作」を最優先とし、ボタン起点のカウント方式を採用しています。
