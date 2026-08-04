# -*- coding: utf-8 -*-
"""
Streamlit 英語スピーキングテストシステム
=========================================
"""

import io
import json
import os
import sys
import uuid
from datetime import datetime
import streamlit as st
import streamlit.components.v1 as components
from gtts import gTTS
import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

# 🌟 新しい統合SDK `google-genai` を使用
from google import genai
from google.genai import types

# =========================================================
# 定数・設定
# =========================================================
SHARED_DRIVE_ID = "0ACP5Eu-XLix6Uk9PVA"  # 保存先共有ドライブID
QUESTIONS_SHEET_NAME = "Questions"

# Gemini モデルの自動フォールバック候補
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

# 📄 ページ設定とデザイン適用
st.set_page_config(
    page_title="Nexus ALT - デジタル英語スピーキングテスト",
    page_icon="🎙️",
    layout="centered",
    initial_sidebar_state="collapsed",
)

st.markdown("""
    <style>
    .stApp {
        background-color: #f8fafc;
        color: #1e293b;
        font-family: 'Helvetica Neue', Arial, 'Hiragino Kaku Gothic ProN', sans-serif;
    }
    .main-header {
        background: linear-gradient(135deg, #1e293b 0%, #0f172a 100%);
        padding: 24px;
        border-radius: 16px;
        color: white;
        text-align: center;
        margin-bottom: 24px;
        box-shadow: 0 4px 6px -1px rgb(0 0 0 / 0.1);
    }
    .main-header h1 {
        color: white !important;
        font-size: 24px !important;
        font-weight: 700 !important;
        margin: 0 !important;
    }
    .main-header p {
        color: #94a3b8 !important;
        font-size: 13px !important;
        margin: 4px 0 0 0 !important;
    }
    .test-card {
        background-color: white;
        padding: 30px;
        border-radius: 16px;
        border: 1px solid #e2e8f0;
        box-shadow: 0 1px 3px 0 rgb(0 0 0 / 0.1);
        margin-bottom: 20px;
    }
    .audio-box {
        background-color: #f1f5f9;
        border: 1px solid #cbd5e1;
        padding: 20px;
        border-radius: 12px;
        text-align: center;
        margin: 15px 0;
    }
    .result-box {
        background-color: #f0fdf4;
        border: 1px solid #bbf7d0;
        padding: 20px;
        border-radius: 12px;
        text-align: center;
        margin-bottom: 15px;
    }
    </style>
""", unsafe_allow_html=True)


# =========================================================
# 外部サービスのクライアント初期化（キャッシュ）
# =========================================================
@st.cache_resource(show_spinner=False)
def get_google_credentials():
    sa_info = json.loads(st.secrets["GOOGLE_SERVICE_ACCOUNT_JSON"])
    if "private_key" in sa_info:
        sa_info["private_key"] = sa_info["private_key"].replace("\\n", "\n")
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
    if "genai_client" not in st.session_state:
        st.session_state.genai_client = genai.Client(api_key=st.secrets["GEMINI_API_KEY"])
    return st.session_state.genai_client


# =========================================================
# Gemini 文字起こし（モデル自動フォールバック＋キャッシュ）
# =========================================================
def transcribe_audio(audio_bytes: bytes) -> str:
    if not audio_bytes or len(audio_bytes) < 100:
        return "No speech"

    client = get_genai_client()

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
            st.session_state["working_gemini_model"] = model_name
            text = (response.text or "").strip()
            return text if text else "No speech"
        except Exception as e:
            last_error = e
            continue

    st.session_state.pop("working_gemini_model", None)
    return f"[文字起こし失敗: 利用可能なGeminiモデルが見つかりませんでした / {last_error}]"


# =========================================================
# スプレッドシート・ドライブ関連
# =========================================================
def load_questions() -> list[str]:
    gc = get_gspread_client()
    sh = gc.open_by_key(st.secrets["SPREADSHEET_ID"])
    ws = sh.worksheet(QUESTIONS_SHEET_NAME)
    values = ws.col_values(1)
    if not values:
        return []
    if values[0].strip().lower() in ("question", "questions", "問題", "問題文"):
        values = values[1:]
    return [v for v in values if v.strip()]


def get_or_create_class_sheet(class_name: str, total_q: int):
    gc = get_gspread_client()
    sh = gc.open_by_key(st.secrets["SPREADSHEET_ID"])
    try:
        ws = sh.worksheet(class_name)
    except gspread.exceptions.WorksheetNotFound:
        ws = sh.add_worksheet(title=class_name, rows=1000, cols=50)
        header = ["タイムスタンプ", "クラス", "番号", "氏名"]
        for i in range(1, total_q + 1):
            header += [f"Q{i}_音声リンク", f"Q{i}_文字起こし", f"Q{i}_評価", f"Q{i}_ステータス", f"Q{i}_再生回数"]
        ws.append_row(header)
    return ws


def upload_audio_to_drive(audio_bytes: bytes, filename: str) -> str:
    service = get_drive_service()
    file_metadata = {
        "name": filename,
        "parents": [SHARED_DRIVE_ID],
        "driveId": SHARED_DRIVE_ID
    }
    media = MediaIoBaseUpload(io.BytesIO(audio_bytes), mimetype="audio/wav", resumable=False)
    file = service.files().create(
        body=file_metadata,
        media_body=media,
        fields="id, webViewLink",
        supportsAllDrives=True,
    ).execute()

    file_id = file["id"]
    try:
        service.permissions().create(
            fileId=file_id,
            body={"type": "anyone", "role": "reader"},
            supportsAllDrives=True,
        ).execute()
    except Exception:
        pass

    return file.get("webViewLink", f"https://drive.google.com/file/d/{file_id}/view")


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
        "class_name": "1年A組",
        "number": "1",
        "name_katakana": "",
        "questions": [],
        "current_q_index": 0,
        "answers": [],
        "upload_done": False,
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def reset_for_next_student():
    for key in ["number", "name_katakana", "questions", "current_q_index", "answers", "upload_done"]:
        st.session_state.pop(key, None)
    st.session_state["step"] = "init"
    init_session_state()


# =========================================================
# 画面1：受験者情報入力
# =========================================================
def render_init_screen():
    st.markdown('<div class="main-header"><h1>🎙️ Nexus ALT スピーキングテスト</h1><p>Digital Speaking Assessment System</p></div>', unsafe_allow_html=True)
    st.markdown('<div class="test-card">', unsafe_allow_html=True)
    st.subheader("受験者情報の入力")

    with st.form("init_form"):
        class_name = st.text_input("クラス", value=st.session_state.class_name)
        number = st.selectbox("名簿番号", options=[str(i) for i in range(1, 46)], index=0)
        name_katakana = st.text_input("氏名（カタカナ）", placeholder="例: トウキョウ タロウ")
        submitted = st.form_submit_button("テストを開始する ➔", type="primary", use_container_width=True)

    if submitted:
        if not class_name.strip() or not name_katakana.strip():
            st.error("⚠️ クラスと氏名を入力してください。")
            st.stop()

        with st.spinner("問題を読み込んでいます..."):
            try:
                questions = load_questions()
            except Exception as e:
                st.error(f"問題の読み込みに失敗しました: {e}")
                st.stop()

        if not questions:
            st.error("⚠️ 'Questions' シートに問題文が見つかりませんでした。")
            st.stop()

        st.session_state.class_name = class_name.strip()
        st.session_state.number = number
        st.session_state.name_katakana = name_katakana.strip()
        st.session_state.questions = questions
        st.session_state.answers = [
            {"question": q, "audio_bytes": None, "transcript": "", "play_count": 0, "drive_link": ""}
            for q in questions
        ]
        st.session_state.current_q_index = 0
        st.session_state.step = "test"
        st.rerun()
    st.markdown('</div>', unsafe_allow_html=True)


# =========================================================
# 画面2：テスト本番
# =========================================================
def render_test_screen():
    idx = st.session_state.current_q_index
    total = len(st.session_state.questions)
    question = st.session_state.questions[idx]

    st.markdown(f'<div class="main-header"><h1>Question {idx + 1} / {total}</h1><p>{st.session_state.class_name} {st.session_state.number} {st.session_state.name_katakana} 受験中</p></div>', unsafe_allow_html=True)
    st.markdown('<div class="test-card">', unsafe_allow_html=True)

    st.progress((idx) / total, text=f"進捗: Q{idx + 1} / {total}")
    st.subheader(f"Q{idx + 1}")
    st.write(question)

    st.markdown('<div class="audio-box">', unsafe_allow_html=True)
    col1, col2 = st.columns([1, 2])
    with col1:
        if st.button("▶️ 音声を再生", key=f"play_{idx}", use_container_width=True):
            st.session_state.answers[idx]["play_count"] += 1
    with col2:
        st.caption(f"🎧 再生回数: {st.session_state.answers[idx]['play_count']} 回")

    if st.session_state.answers[idx]["play_count"] > 0:
        audio_bytes = generate_question_audio(question)
        st.audio(audio_bytes, format="audio/mp3", autoplay=True)
    st.markdown('</div>', unsafe_allow_html=True)

    st.divider()
    st.write("##### 🎙️ あなたの解答を録音")
    recorded = st.audio_input("マイクに向かって話してください", key=f"rec_{idx}")

    is_last = (idx == total - 1)
    button_label = "🏁 すべての回答を送信する" if is_last else "次の問題へ ➡️"

    if st.button(button_label, type="primary", use_container_width=True):
        if recorded is None:
            st.warning("⚠️ 録音が完了してから進んでください。")
        else:
            audio_bytes = recorded.read()
            with st.spinner(f"Q{idx + 1} の音声を解析中..."):
                transcript = transcribe_audio(audio_bytes)

            st.session_state.answers[idx]["audio_bytes"] = audio_bytes
            st.session_state.answers[idx]["transcript"] = transcript

            if is_last:
                st.session_state.step = "finish"
            else:
                st.session_state.current_q_index += 1
            st.rerun()

    st.markdown('</div>', unsafe_allow_html=True)


# =========================================================
# 画面3：送信・データ保存
# =========================================================
def render_finish_screen():
    st.markdown('<div class="main-header"><h1>🏁 テスト送信・保存中</h1><p>サーバーへデータを安全に記録しています</p></div>', unsafe_allow_html=True)
    st.markdown('<div class="test-card">', unsafe_allow_html=True)

    if not st.session_state.upload_done:
        progress_bar = st.progress(0)
        status_text = st.empty()
        total_q = len(st.session_state.questions)

        try:
            for i, ans in enumerate(st.session_state.answers):
                status_text.markdown(f"**Question {i+1} / {total_q} の音声ファイルをアップロード中...**")
                if ans["audio_bytes"]:
                    filename = f"{st.session_state.class_name}_{st.session_state.number}_{st.session_state.name_katakana}_Q{i+1}_{uuid.uuid4().hex[:8]}.wav"
                    link = upload_audio_to_drive(ans["audio_bytes"], filename)
                    st.session_state.answers[i]["drive_link"] = link
                progress_bar.progress(int((i + 1) / total_q * 50))

            status_text.markdown("**スプレッドシートにデータを書き込んでいます...**")
            ws = get_or_create_class_sheet(st.session_state.class_name, total_q)
            
            row = [
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                st.session_state.class_name,
                st.session_state.number,
                st.session_state.name_katakana,
            ]
            for ans in st.session_state.answers:
                row += [
                    ans.get("drive_link", ""),
                    ans.get("transcript", ""),
                    "提出済",
                    "正常に受付",
                    ans.get("play_count", 0),
                ]
            ws.append_row(row)
            progress_bar.progress(100)
            
            st.session_state.upload_done = True
            st.balloons()
            st.rerun()

        except Exception as e:
            st.error(f"❌ 保存中にエラーが発生しました: {e}")
            st.stop()

    else:
        st.markdown("""
        <div class="result-box">
            <h3 style="color: #15803d; margin: 0;">✅ 送信が完了しました</h3>
            <p style="margin: 10px 0 0 0; color: #1e293b; font-size: 15px;">
                音声ファイルと解答データの保存がすべて正常に完了しました。お疲れ様でした！
            </p>
        </div>
        """, unsafe_allow_html=True)

        with st.expander("📋 送信内容の確認"):
            for i, ans in enumerate(st.session_state.answers):
                st.markdown(f"**Q{i + 1}**: {ans['question']}")
                st.write(f"文字起こし: {ans['transcript']}")
                st.write(f"再生回数: {ans['play_count']} 回")
                if ans["drive_link"]:
                    st.write(f"音声リンク: {ans['drive_link']}")
                st.divider()

        if st.button("🔄 次の生徒の入力を開始", use_container_width=True, type="primary"):
            reset_for_next_student()
            st.rerun()

    st.markdown('</div>', unsafe_allow_html=True)


# =========================================================
# メイン
# =========================================================
def main():
    init_session_state()
    step = st.session_state.step
    if step == "init":
        render_init_screen()
    elif step == "test":
        render_test_screen()
    elif step == "finish":
        render_finish_screen()


if __name__ == "__main__":
    main()
