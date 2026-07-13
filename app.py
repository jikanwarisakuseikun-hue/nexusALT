import streamlit as st
import json
import time
import sys
import os

# 🌟 st_audiorec の読み込みパス問題を強制解決するロジック
try:
    import st_audiorec
except ModuleNotFoundError:
    # サーバー内のパッケージ配置先を自動検索してパスへ追加
    for path in sys.path:
        if "site-packages" in path:
            potential_path = os.path.join(path, "st_audiorec")
            if os.path.exists(potential_path) and potential_path not in sys.path:
                sys.path.append(potential_path)
    try:
        from st_audiorec import st_audiorec
    except ImportError:
        # 万が一読み込めない場合、標準の録音コンポーネントを代替として配置
        st_audiorec = None

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaInMemoryUpload
import google.generativeai as genai

# 📄 ページ設定とデザインの適用
st.set_page_config(
    page_title="Nexus ALT - デジタル英語スピーキングテスト",
    page_icon="🎙️",
    layout="centered",
    initial_sidebar_state="collapsed"
)

# 🎨 スタイリッシュなモダンデザインCSS
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
    .footer {
        position: fixed;
        left: 0;
        bottom: 0;
        width: 100%;
        background-color: #f1f5f9;
        color: #64748b;
        text-align: center;
        padding: 8px 0;
        font-size: 11px;
        border-top: 1px solid #e2e8f0;
        z-index: 100;
    }
    .main-content-padding {
        margin-bottom: 60px;
    }
    </style>
""", unsafe_allow_html=True)

# 🔒 Secretsのパース
try:
    gemini_key = st.secrets["GEMINI_API_KEY"]
    SPREADSHEET_ID = st.secrets["SPREADSHEET_ID"]
    FOLDER_ID = st.secrets["FOLDER_ID"]
    
    raw_json_text = st.secrets["GOOGLE_SERVICE_ACCOUNT_JSON"]
    service_account_info = json.loads(raw_json_text)
    
    if "private_key" in service_account_info:
        service_account_info["private_key"] = service_account_info["private_key"].replace("\\n", "\n")
        
except Exception as e:
    st.error(f"【設定エラー】Secretsの読み込みまたはJSONの解析に失敗しました。設定内容を確認してください。 エラー詳細: {e}")
    st.stop()

# 🌐 APIの初期化
creds = Credentials.from_service_account_info(
    service_account_info, 
    scopes=[
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]
)
sheets_service = build("sheets", "v4", credentials=creds)
drive_service = build("drive", "v3", credentials=creds)
genai.configure(api_key=gemini_key)

# 📝 テストの問題マスタ
QUESTIONS = [
    {"id": 1, "text": "What did you do last weekend? Please tell me about it in detail."},
    {"id": 2, "text": "Why do you think learning English is important for your future?"},
    {"id": 3, "text": "Look at the imaginary situation. If you could travel anywhere in the world right now, where would you go and why?"}
]

# 💾 セッション状態の初期化
if "step" not in st.session_state:
    st.session_state.step = "init"
if "current_q_idx" not in st.session_state:
    st.session_state.current_q_idx = 0
if "student_info" not in st.session_state:
    st.session_state.student_info = {}
if "recorded_audios" not in st.session_state:
    st.session_state.recorded_audios = {q["id"]: None for q in QUESTIONS}

st.markdown('<div class="main-content-padding">', unsafe_allow_html=True)

# --- 🖼️ 画面1: 受験者情報入力画面 ---
if st.session_state.step == "init":
    st.markdown('<div class="main-header"><h1>🎙️ Nexus ALT スピーキングテスト</h1><p>Digital Speaking Assessment System</p></div>', unsafe_allow_html=True)
    st.markdown('<div class="test-card">', unsafe_allow_html=True)
    st.subheader("受験者情報の入力")
    
    col1, col2 = st.columns(2)
    with col1:
        cls = st.selectbox("クラス", ["1年1組", "1年2組", "2年1組", "2年2組", "3年1組", "3年2組"])
    with col2:
        num = st.selectbox("名簿番号", [f"{i}番" for i in range(1, 46)])
        
    name = st.text_input("氏名（カタカナ）", placeholder="例: トウキョウ タロウ")
    
    st.markdown("<br>", unsafe_allow_html=True)
    if st.button("テストを開始する ➔", use_container_width=True, type="primary"):
        if not name.strip():
            st.error("⚠️ 氏名を入力してください。")
        else:
            st.session_state.student_info = {"class": cls, "number": num, "name": name.strip()}
            st.session_state.step = "test"
            st.rerun()
    st.markdown('</div>', unsafe_allow_html=True)

# --- 🖼️ 画面2: テスト本番画面 ---
elif st.session_state.step == "test":
    info = st.session_state.student_info
    st.markdown(f'<div class="main-header"><h1>Question {st.session_state.current_q_idx + 1} / {len(QUESTIONS)}</h1><p>{info["class"]} {info["number"]} {info["name"]} 受験中</p></div>', unsafe_allow_html=True)
    
    q = QUESTIONS[st.session_state.current_q_idx]
    st.markdown('<div class="test-card">', unsafe_allow_html=True)
    
    st.markdown('<div class="audio-box">', unsafe_allow_html=True)
    tts_url = f"https://translate.google.com/translate_tts?ie=UTF-8&tl=en&client=tw-ob&q={q['text'].replace(' ', '+')}"
    st.audio(tts_url, format="audio/mp3")
    st.markdown('<p style="color:#64748b; font-size:12px; margin-top:5px;">※上の再生ボタンを押して質問をよく聴いてください。</p>', unsafe_allow_html=True)
    st.markdown('</div>', unsafe_allow_html=True)
    
    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown("##### 🎙️ 回答を録音する")
    
    # 🛠️ 録音コンポーネントのエラーを2重でガード
    wav_audio_data = None
    if st_audiorec is not None:
        try:
            wav_audio_data = st_audiorec()
        except Exception as e:
            st.warning("カスタム録音モジュールの起動に失敗しました。標準のマイクを使用します。")
    
    # 万が一カスタム録音部品が死んでいる場合は、Streamlit標準の録音機能に自動切り替え
    if wav_audio_data is None:
        standard_audio = st.audio_input("マイク入力を許可して録音ボタンを押してください", key=f"audio_input_{q['id']}")
        if standard_audio is not None:
            wav_audio_data = standard_audio.read()
    
    if wav_audio_data is not None:
        st.session_state.recorded_audios[q["id"]] = wav_audio_data
        st.success("✅ この問題の録音が完了しました！")
        
    st.markdown("<br><br>", unsafe_allow_html=True)
    
    c1, c2 = st.columns(2)
    with c1:
        if st.button("⬅️ 前の問題に戻る", use_container_width=True, disabled=(st.session_state.current_q_idx == 0)):
            st.session_state.current_q_idx -= 1
            st.rerun()
            
    with c2:
        is_last = (st.session_state.current_q_idx == len(QUESTIONS) - 1)
        btn_label = "🏁 すべての回答を送信する" if is_last else "次の問題へ ➡️"
        
        if st.button(btn_label, use_container_width=True, type="primary" if is_last else "secondary"):
            if st.session_state.recorded_audios[q["id"]] is None:
                st.warning("⚠️ 録音を行ってから次へ進んでください。")
            else:
                if is_last:
                    st.session_state.step = "finish"
                else:
                    st.session_state.current_q_idx += 1
                st.rerun()
    st.markdown('</div>', unsafe_allow_html=True)

# --- 🖼️ 画面3: 送信・AI採点・完了画面 ---
elif st.session_state.step == "finish":
    st.markdown('<div class="main-header"><h1>🏁 テスト送信・AI採点中</h1><p>データを安全に送信し、AI採点を行っています</p></div>', unsafe_allow_html=True)
    st.markdown('<div class="test-card">', unsafe_allow_html=True)
    info = st.session_state.student_info
    
    progress_bar = st.progress(0)
    status_text = st.empty()
    total_q = len(QUESTIONS)
    
    for idx, q in enumerate(QUESTIONS):
        status_text.markdown(f"**【処理中】 Question {idx+1} の音声を保存し、AI採点しています...**")
        audio_bytes = st.session_state.recorded_audios[q["id"]]
        
        # 1. Googleドライブへアップロード
        filename = f"{info['class']}_{info['number']}_{info['name']}_Q{q['id']}.wav"
        media = MediaInMemoryUpload(audio_bytes, mimetype="audio/wav")
        file_metadata = {"name": filename, "parents": [FOLDER_ID]}
        
        drive_file = drive_service.files().create(
            body=file_metadata, media_body=media, fields="id, webViewLink"
        ).execute()
        audio_link = drive_file.get("webViewLink")
        
        # 2. Geminiによる音声採点
        model = genai.GenerativeModel("gemini-2.5-flash")
        prompt = f"""
        You are an expert English ALT (Assistant Language Teacher) at a Japanese school.
        Please evaluate the student's spoken audio response for this question: "{q['text']}"
        
        Provide the output strictly in Japanese with the following format:
        【AI文字起こし】
        (Write out exactly what the student said in English here. If noise only, write '音声認識不可')
        
        【採点フィードバック】
        ・総合評価: (A / B / C)
        ・文法・表現: (Good points or corrections)
        ・発音・流暢さ: (Advice for improvement)
        """
        
        response = model.generate_content([
            prompt,
            {"mime_type": "audio/wav", "data": audio_bytes}
        ])
        ai_output = response.text
        
        try:
            parts = ai_output.split("【採点フィードバック】")
            transcription = parts[0].replace("【AI文字起こし】", "").strip()
            feedback = parts[1].strip()
        except:
            transcription = "認識完了"
            feedback = ai_output
            
        # 3. Googleスプレッドシートへ書き込み（Resultsシート指定）
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        row_data = [timestamp, info["class"], info["number"], info["name"], f"Q{q['id']}", transcription, feedback, audio_link]
        
        try:
            sheets_service.spreadsheets().values().append(
                spreadsheetId=SPREADSHEET_ID,
                range="'Results'!A:H",
                valueInputOption="USER_ENTERED",
                body={"values": [row_data]}
            ).execute()
        except Exception as sheet_err:
            st.error(f"Resultsシートへのデータ追加時にエラーが発生しました: {sheet_err}")
        
        progress_bar.progress(int((idx + 1) / total_q * 100))
        
    status_text.empty()
    progress_bar.empty()
    
    st.balloons()
    st.success("🎉 スピーキングテストの解答送信とAI採点がすべて完了しました！")
    
    if st.button("🔄 次の生徒の入力を開始"):
        st.session_state.step = "init"
        st.session_state.current_q_idx = 0
        st.session_state.recorded_audios = {q["id"]: None for q in QUESTIONS}
        st.rerun()
    st.markdown('</div>', unsafe_allow_html=True)

st.markdown('</div>', unsafe_allow_html=True)

# 📊 著作権表示
st.markdown("""
    <div class="footer">
        © 2026 Nexus ALT. All Rights Reserved. Digital Speaking Assessment System.
    </div>
""", unsafe_allow_html=True)
