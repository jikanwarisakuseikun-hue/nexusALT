# Let's write a python script that will generate the Streamlit app code as a python file or text, and verify its syntax and completeness.
app_code = '''import streamlit as st
import pandas as pd
import time
import os
import datetime
import pytz
from gtts import gTTS
import tempfile
import json
import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from audio_recorder_streamlit import audio_recorder
import google.generativeai as genai
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

# ページ設定
st.set_page_config(
    page_title="中学校英語スピーキングテスト",
    page_icon="🎤",
    layout="centered"
)

# -------------------------------------------------------------
# 認証・設定ヘルパー (Retries & Secrets)
# -------------------------------------------------------------
def get_secrets():
    """st.secretsから必要な認証情報を安全に取得する"""
    try:
        return {
            "gemini_api_key": st.secrets["GEMINI_API_KEY"],
            "drive_folder_id": st.secrets["GOOGLE_DRIVE_FOLDER_ID"],
            "spreadsheet_name": st.secrets["spreadsheet"],
            "service_account_info": dict(st.secrets["connections"]["gsheets"]),
            "app_users": st.secrets.get("APP_USERS", {"teacher": "password123"})
        }
    except Exception as e:
        st.error(f"Streamlit Secretsの設定が不足しています: {e}")
        st.stop()

SECRETS = get_secrets()

# Geminiの初期設定
genai.configure(api_key=SECRETS["gemini_api_key"])

# Google Sheets / Driveクライアントの初期化 (リトライ機能付き)
@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def get_gspread_client():
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]
    creds_info = SECRETS["service_account_info"]
    creds = Credentials.from_service_account_info(creds_info, scopes=scopes)
    return gspread.authorize(creds)

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def get_drive_service():
    scopes = ["https://www.googleapis.com/auth/drive"]
    creds_info = SECRETS["service_account_info"]
    creds = Credentials.from_service_account_info(creds_info, scopes=scopes)
    return build('drive', 'v3', credentials=creds)

# -------------------------------------------------------------
# ログイン・認証画面
# -------------------------------------------------------------
def check_auth():
    if "authenticated" not in st.session_state:
        st.session_state.authenticated = False

    if not st.session_state.authenticated:
        st.title("🔒 先生用認証 / 生徒ログイン")
        st.markdown("テストを実施・管理するためにログインしてください。")
        
        tab_teacher, tab_student = st.tabs(["教員ログイン", "生徒受験ログイン"])
        
        with tab_teacher:
            st.subheader("教員ポータル認証")
            t_id = st.text_input("教員ID", key="t_id")
            t_pw = st.text_input("パスワード", type="password", key="t_pw")
            # 先生ごとでAPIキーをカスタムできるようにするオプション設定
            custom_api = st.text_input("カスタム Gemini APIキー (任意で上書き)", type="password", help="空欄の場合はデフォルト設定が使われます")
            
            if st.button("教員ログイン"):
                users = SECRETS.get("app_users", {"teacher": "password123"})
                if t_id in users and users[t_id] == t_pw:
                    st.session_state.authenticated = True
                    st.session_state.role = "teacher"
                    st.session_state.user_id = t_id
                    if custom_api:
                        st.session_state.custom_gemini_api = custom_api
                    st.success("ログイン成功しました！")
                    st.rerun()
                else:
                    st.error("IDまたはパスワードが間違っています。")

        with tab_student:
            st.subheader("生徒テスト開始画面")
            st.info("生徒としてテストを受ける場合は、以下に情報を入力して開始してください。")
            s_school = st.text_input("学校名", "〇〇市立第一中学校")
            s_grade = st.selectbox("学年", ["1年", "2年", "3年"])
            s_class = st.selectbox("クラス", ["A組", "B組", "C組", "D組"])
            s_number = st.number_input("出席番号", min_value=1, max_value=50, step=1)
            s_name = st.text_input("氏名", "山田 太郎")
            
            if st.button("テストを開始する"):
                if not s_name.strip():
                    st.warning("氏名を入力してください。")
                else:
                    st.session_state.authenticated = True
                    st.session_state.role = "student"
                    st.session_state.student_info = {
                        "school": s_school,
                        "grade": s_grade,
                        "class": s_class,
                        "number": int(s_number),
                        "name": s_name
                    }
                    st.rerun()
        return False
    return True

# -------------------------------------------------------------
# スプレッドシートからの設定読込
# -------------------------------------------------------------
@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def load_config_from_sheet():
    client = get_gspread_client()
    sheet = client.open(SECRETS["spreadsheet_name"])
    try:
        config_ws = sheet.worksheet("Config")
        data = config_ws.get_all_records()
        return pd.DataFrame(data)
    except Exception as e:
        # デフォルトの設定データを返す（Configシートがない場合）
        default_data = [
            {"question_id": 1, "question_text": "Please introduce yourself in English.", "criteria": "挨拶ができているか、自分の名前や趣味を明確に話せているか。 (A/B/C)"},
            {"question_id": 2, "question_text": "What do you want to do during your summer vacation?", "criteria": "未来の表現(will / want to)を用いて、理由とともに自分の計画を説明できているか。 (A/B/C)"},
            {"question_id": 3, "question_text": "Could you tell me the way to the nearest station?", "criteria": "道案内の表現(turn left, go straightなど)を正しく使い、分かりやすく道順を説明できているか。 (A/B/C)"}
        ]
        return pd.DataFrame(default_data)

# -------------------------------------------------------------
# Google Driveへのファイルアップロード
# -------------------------------------------------------------
@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def upload_audio_to_drive(file_path, file_name):
    service = get_drive_service()
    folder_id = SECRETS["drive_folder_id"]
    
    file_metadata = {
        'name': file_name,
        'parents': [folder_id]
    }
    media = MediaFileUpload(file_path, mimetype='audio/wav', resumable=True)
    file = service.files().create(
        body=file_metadata,
        media_body=media,
        fields='id, webViewLink, webContentLink'
    ).execute()
    
    # 閲覧権限の付与（必要に応じてリンクを知っている全員が閲覧可能に設定）
    try:
        service.permissions().create(
            fileId=file.get('id'),
            body={'role': 'reader', 'type': 'anyone'}
        ).execute()
    except Exception:
        pass
        
    return file.get('webViewLink')

# -------------------------------------------------------------
# Google Sheetsへの結果保存（シート自動作成・動的分岐対応）
# -------------------------------------------------------------
@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def save_result_to_sheet(student_info, result_row):
    client = get_gspread_client()
    spreadsheet = client.open(SECRETS["spreadsheet_name"])
    
    # シート名の決定: 例 "2年B組"
    sheet_title = f"{student_info['grade']}{student_info['class']}"
    
    try:
        worksheet = spreadsheet.worksheet(sheet_title)
    except gspread.exceptions.WorksheetNotFound:
        # ワークシートが存在しない場合は新規作成し、ヘッダーを追加
        worksheet = spreadsheet.add_worksheet(title=sheet_title, rows="100", cols="15")
        header = [
            "タイムスタンプ(JST)", "学校名", "学年", "クラス", "出席番号", "氏名",
            "問題ID", "質問文", "文字起こし", "評価(A/B/C)", "アドバイス", "音声URL", "解答時間(秒)"
        ]
        worksheet.append_row(header)
        
    worksheet.append_row(result_row)

# -------------------------------------------------------------
# Gemini APIによる音声評価・文字起こし
# -------------------------------------------------------------
@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def evaluate_audio_with_gemini(audio_path, question_text, criteria, custom_api_key=None):
    if custom_api_key:
        genai.configure(api_key=custom_api_key)
    else:
        genai.configure(api_key=SECRETS["gemini_api_key"])
        
    # 音声ファイルをGemini Files APIにアップロード
    audio_file = genai.upload_file(path=audio_path)
    
    prompt = f"""
    あなたは中学校英語科の厳格かつ親切なAI英語スピーキングテスト採点官です。
    以下の質問と評価基準に基づいて、生徒の音声を文字起こしし、評価を行ってください。

    【質問】
    {question_text}

    【評価基準】
    {criteria}

    以下のJSON形式のみで正確に出力してください（マークダウンのコードブロックや余分なテキストは含めないでください）。
    {{
      "transcript": "文字起こしされた英語テキスト",
      "evaluation": "A または B または C",
      "advice": "日本語での丁寧なアドバイスと良かった点・改善点"
    }}
    """
    
    model = genai.GenerativeModel("gemini-2.5-flash") # または gemini-1.5-flash
    response = model.generate_content([audio_file, prompt])
    
    # アップロードしたファイルを削除
    try:
        genai.delete_file(audio_file.name)
    except Exception:
        pass
        
    # レスポンスのパース
    text_res = response.text.strip()
    if text_res.startswith("```json"):
        text_res = text_res[7:]
    if text_res.endswith("```"):
        text_res = text_res[:-3]
    text_res = text_res.strip()
    
    import json
    try:
        res_json = json.loads(text_res)
        return res_json.get("transcript", ""), res_json.get("evaluation", "C"), res_json.get("advice", "評価を生成できませんでした。")
    except Exception as e:
        return f"Parse Error: {response.text}", "C", f"解析エラーが発生しました: {e}"

# -------------------------------------------------------------
# メインアプリケーション
# -------------------------------------------------------------
def main():
    if not check_auth():
        return

    # サイドバー：ログアウトやセッション管理
    with st.sidebar:
        st.write(f"ログインロール: **{st.session_state.get('role', 'unknown')}**")
        if st.session_state.get('role') == 'student':
            s_info = st.session_state.student_info
            st.markdown("---")
            st.markdown(f"**受験者情報**")
            st.write(f"学校: {s_info['school']}")
            st.write(f"学年/クラス: {s_info['grade']} {s_info['class']}")
            st.write(f"番号・氏名: {s_info['number']}番 {s_info['name']}")
        
        if st.button("ログアウト / 最初に戻る"):
            st.session_state.clear()
            st.rerun()

    st.title("🎤 中学校英語スピーキングテストシステム")

    # 設定のロード
    config_df = load_config_from_sheet()

    # 教員モードの場合のダッシュボード表示
    if st.session_state.get('role') == 'teacher':
        st.subheader("👨‍🏫 教員管理ダッシュボード")
        st.markdown("現在設定されているテスト問題と評価基準一覧です。")
        st.dataframe(config_df, use_container_width=True)
        
        st.markdown("---")
        st.markdown("### スプレッドシート確認リンク")
        st.write(f"対象スプレッドシート名: `{SECRETS['spreadsheet_name']}`")
        st.info("生徒ごとの結果は各クラス別シート（例: 2年B組）に自動で蓄積されます。")
        return

    # 生徒受験モード
    s_info = st.session_state.student_info
    
    if "test_step" not in st.session_state:
        st.session_state.test_step = 0
    if "test_results" not in st.session_state:
        st.session_state.test_results = []

    total_questions = len(config_df)
    current_step = st.session_state.test_step

    if current_step < total_questions:
        q_row = config_df.iloc[current_step]
        q_id = q_row.get("question_id", current_step + 1)
        q_text = q_row.get("question_text", "Please speak in English.")
        q_criteria = q_row.get("criteria", "英語で適切に返答できているか。")

        st.progress((current_step) / total_questions, text=f"進捗: 質問 {current_step + 1} / {total_questions}")
        
        st.markdown(f"### 質問 {current_step + 1}")
        st.info(f"🔊 以下の英語の質問をよく聞いて、英語で答えてください。")
        
        # 1. 音声生成 & 再生 (gTTS)
        tts = gTTS(text=q_text, lang='en')
        with tempfile.NamedTemporaryFile(delete=False, suffix='.mp3') as tmp_audio:
            tts.save(tmp_audio.name)
            tmp_audio_path = tmp_audio.name
            
        st.audio(tmp_audio_path, format="audio/mp3", autoplay=False)
        st.markdown(f"**質問文（テキスト）:** `{q_text}`")

        # 2. 5秒間のシンキングタイム
        if f"thinking_done_{current_step}" not in st.session_state:
            if st.button("▶️ 音声を再生してシンキングタイム(5秒)を開始する", key=f"start_btn_{current_step}"):
                with st.spinner("シンキングタイム中... 準備をしてください"):
                    bar = st.progress(0)
                    for i in range(5):
                        time.sleep(1)
                        bar.progress((i + 1) / 5)
                st.session_state[f"thinking_done_{current_step}"] = True
                st.rerun()
        else:
            st.success("✨ シンキングタイム終了！下の録音ボタンを押して発話してください。")

            # 3. 発話・録音エリア
            st.markdown("#### 🎙️ 録音エリア")
            st.write("マイクに向かって英語で答えてください。話し終わったら停止ボタンを押してください。")
            
            audio_bytes = audio_recorder(text="クリックして録音開始/停止", recording_color="#e8b62c", neutral_color="#6aa36f", icon_size="2x")

            if audio_bytes:
                st.audio(audio_bytes, format="audio/wav")
                
                if st.button("📤 この音声を送信して次へ進む", key=f"submit_btn_{current_step}"):
                    with st.spinner("音声をアップロードし、AI採点・文字起こしを実行中..."):
                        # 一時ファイルに保存
                        with tempfile.NamedTemporaryFile(delete=False, suffix='.wav') as f_wav:
                            f_wav.write(audio_bytes)
                            wav_path = f_wav.name

                        # Google Driveへアップロード
                        file_name = f"{s_info['school']}_{s_info['grade']}{s_info['class']}_{s_info['number']}_{s_info['name']}_Q{q_id}.wav"
                        audio_url = upload_audio_to_drive(wav_path, file_name)

                        # Gemini APIで評価・文字起こし
                        custom_api = st.session_state.get("custom_gemini_api", None)
                        transcript, evaluation, advice = evaluate_audio_with_gemini(wav_path, q_text, q_criteria, custom_api)

                        # タイムスタンプ（日本時間）
                        jst = pytz.timezone('Asia/Tokyo')
                        timestamp = datetime.datetime.now(jst).strftime('%Y-%m-%d %H:%M:%S')

                        # スプレッドシートに保存
                        result_row = [
                            timestamp, s_info['school'], s_info['grade'], s_info['class'],
                            s_info['number'], s_info['name'], q_id, q_text,
                            transcript, evaluation, advice, audio_url, 10 # 解答時間目安
                        ]
                        save_result_to_sheet(s_info, result_row)

                        # 結果をセッションに一時保存
                        st.session_state.test_results.append({
                            "question": q_text,
                            "transcript": transcript,
                            "evaluation": evaluation,
                            "advice": advice
                        })

                        # 次のステップへ
                        st.session_state.test_step += 1
                        st.rerun()

    else:
        # テスト完了画面
        st.balloons()
        st.success("🎉 すべての質問が終了しました！お疲れ様でした。")
        st.markdown("### 📊 今回のテスト結果サマリー")
        
        for idx, res in enumerate(st.session_state.test_results):
            with st.expander(f"質問 {idx + 1} の結果"):
                st.write(f"**質問:** {res['question']}")
                st.write(f"**文字起こし:** {res['transcript']}")
                st.write(f"**評価:** {res['evaluation']}")
                st.write(f"**アドバイス:** {res['advice']}")
                
        if st.button("最初に戻る"):
            st.session_state.clear()
            st.rerun()

    # フッター著作権表示
    st.markdown("---")
    st.markdown(
        "<div style='text-align: center; color: gray; font-size: 0.9em;'>"
        "© 2026 Shogo Takeuchi. All Rights Reserved."
        "</div>",
        unsafe_allow_html=True
    )

if __name__ == "__main__":
    main()
'''

print("Python code generated successfully.")
