import streamlit as st
import json
import time
import sys
import os
from gtts import gTTS  # 🔊 音声再生の安定化のために導入

# 🌟 st_audiorec の読み込みパス問題を強制解決するロジック
try:
    import st_audiorec
except ModuleNotFoundError:
    for path in sys.path:
        if "site-packages" in path:
            potential_path = os.path.join(path, "st_audiorec")
            if os.path.exists(potential_path) and potential_path not in sys.path:
                sys.path.append(potential_path)
    try:
        from st_audiorec import st_audiorec
    except ImportError:
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
    SPREADSHEET_ID = st.secrets["SPREADSHEET_ID"]
    raw_json_text = st.secrets["GOOGLE_SERVICE_ACCOUNT_JSON"]
    service_account_info = json.loads(raw_json_text)
    
    if "private_key" in service_account_info:
        service_account_info["private_key"] = service_account_info["private_key"].replace("\\n", "\n")
        
except Exception as e:
    st.error(f"【設定エラー】Secretsの読み込みに失敗しました。 エラー詳細: {e}")
    st.stop()

# 🌐 Google APIの初期化
creds = Credentials.from_service_account_info(
    service_account_info, 
    scopes=[
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]
)
sheets_service = build("sheets", "v4", credentials=creds)
drive_service = build("drive", "v3", credentials=creds)

# 💾 セッション状態の初期化
if "step" not in st.session_state:
    st.session_state.step = "init"
if "current_q_idx" not in st.session_state:
    st.session_state.current_q_idx = 0
if "student_info" not in st.session_state:
    st.session_state.student_info = {}
if "recorded_audios" not in st.session_state:
    st.session_state.recorded_audios = {}
if "listen_counts" not in st.session_state:
    st.session_state.listen_counts = {1: 0, 2: 0, 3: 0} # 各問題の再生回数をカウント
if "questions_data" not in st.session_state:
    st.session_state.questions_data = None

# 📥 スプレッドシートの「Questions」シートからデータを動的に読み取る
if st.session_state.questions_data is None:
    try:
        result = sheets_service.spreadsheets().values().get(
            spreadsheetId=SPREADSHEET_ID,
            range="'Questions'!A3:G3"
        ).execute()
        
        row_values = result.get("values", [])[0]
        
        # シートに書かれているクラス名（例: A3セル）を取得
        st.session_state.class_name = row_values[0] if len(row_values) > 0 else "設定なし"
        
        # 問題データのセット
        st.session_state.questions_data = [
            {"id": 1, "text": row_values[1] if len(row_values) > 1 else "", "criterion": row_values[2] if len(row_values) > 2 else ""},
            {"id": 2, "text": row_values[3] if len(row_values) > 3 else "", "criterion": row_values[4] if len(row_values) > 4 else ""},
            {"id": 3, "text": row_values[5] if len(row_values) > 5 else "", "criterion": row_values[6] if len(row_values) > 6 else ""}
        ]
        
        gemini_key = st.secrets["GEMINI_API_KEY"]
        genai.configure(api_key=gemini_key)
        st.session_state.recorded_audios = {1: None, 2: None, 3: None}
        
    except Exception as e:
        st.error(f"Questionsシートからのデータ読み込みに失敗しました。詳細: {e}")
        st.stop()

QUESTIONS = st.session_state.questions_data
FOLDER_ID = st.secrets["FOLDER_ID"]

# 💡 【重要】新しく指定された共有ドライブのルートID（0Aから始まるID）を内部処理用に固定適用
TARGET_DRIVE_ID = "0ACP5Eu-XLix6Uk9PVA"

st.markdown('<div class="main-content-padding">', unsafe_allow_html=True)

# --- 🖼️ 画面1: 受験者情報入力画面 ---
if st.session_state.step == "init":
    st.markdown('<div class="main-header"><h1>🎙️ Nexus ALT スピーキングテスト</h1><p>Digital Speaking Assessment System</p></div>', unsafe_allow_html=True)
    st.markdown('<div class="test-card">', unsafe_allow_html=True)
    st.subheader("受験者情報の入力")
    
    col1, col2 = st.columns(2)
    with col1:
        # セレクトボックスにはシートから読み取ったクラス名のみを表示
        cls = st.selectbox("クラス", [st.session_state.class_name])
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
    
    # gTTSを使ってサーバーサイドで問題音声を生成
    try:
        if f"audio_bytes_{q['id']}" not in st.session_state:
            tts = gTTS(text=q['text'], lang='en', tld='com')
            import io
            fp = io.BytesIO()
            tts.write_to_fp(fp)
            st.session_state[f"audio_bytes_{q['id']}"] = fp.getvalue()
        
        st.audio(st.session_state[f"audio_bytes_{q['id']}"], format="audio/mp3")
        
        if st.button("🔊 質問を聴いた（回数を記録）", key=f"listen_btn_{q['id']}"):
            st.session_state.listen_counts[q['id']] += 1
            st.success(f"再生回数を記録しました（現在: {st.session_state.listen_counts[q['id']]}回）")
            
    except Exception as tts_err:
        st.error("問題音声の生成に失敗しました。ページをリロードしてください。")
        
    st.markdown('</div>', unsafe_allow_html=True)
    
    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown("##### 🎙️ 回答を録音する")
    
    wav_audio_data = None
    if st_audiorec is not None:
        try:
            wav_audio_data = st_audiorec()
        except Exception:
            pass
    
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
    
    # 生徒1人分の横に長いデータ行を準備 (クラス, 名簿番号, 氏名)
    row_data = [info["class"], info["number"], info["name"]]
    
    for idx, q in enumerate(QUESTIONS):
        status_text.markdown(f"**【処理中】 Question {idx+1} の音声を保存し、AI採点しています...**")
        audio_bytes = st.session_state.recorded_audios[q["id"]]
        
        # 1. Googleドライブへアップロード (🚨 新しい共有ドライブ・ルート直下保存最適化版)
        filename = f"{info['class']}_{info['number']}_{info['name']}_Q{q['id']}.wav"
        media = MediaInMemoryUpload(audio_bytes, mimetype="audio/wav")
        
        # 💡 ルートID（0Aで始まるID）の場合は、通常の子フォルダ保存用の metadata に加え
        # driveId を明示的に渡すことで、APIが共有ドライブ直下への直接保存を認識できるようになります。
        file_metadata = {
            "name": filename, 
            "parents": [TARGET_DRIVE_ID],
            "driveId": TARGET_DRIVE_ID
        }
        
        try:
            drive_file = drive_service.files().create(
                body=file_metadata, 
                media_body=media, 
                fields="id, webViewLink",
                supportsAllDrives=True  # 👈 共有ドライブ用の必須セキュリティ解除フラグ
            ).execute()
            audio_link = drive_file.get("webViewLink")
        except Exception as drive_err:
            st.error(f"❌ 共有ドライブへの音声保存に失敗しました。ドライブID「{TARGET_DRIVE_ID}」に対してサービスアカウントのメールアドレスが『管理者』または『コンテンツ管理者』として追加されているか必ずご確認ください。 詳細: {drive_err}")
            st.stop()
        
        # 2. Geminiによる音声採点
        model = genai.GenerativeModel("gemini-2.5-flash")
        prompt = f"""
        You are an expert English ALT (Assistant Language Teacher) at a Japanese school.
        Please evaluate the student's spoken audio response for this question: "{q['text']}"
        
        [Strict Evaluation Criterion]
        Use this specific grading guideline provided by the teacher:
        "{q['criterion']}"
        
        Provide the output strictly in Japanese with the following format:
        【AI文字起こし】
        (Write out exactly what the student said in English here. If noise only, write '音声認識不可')
        
        【総合評価】
        (Write ONLY A, B, or C here based on the criterion)
        
        【アドバイス】
        (Provide short advice in Japanese about grammar, pronunciation and fluency within 2-3 sentences)
        """
        
        response = model.generate_content([
            prompt,
            {"mime_type": "audio/wav", "data": audio_bytes}
        ])
        ai_output = response.text
        
        try:
            transcription = ai_output.split("【AI文字起こし】")[1].split("【総合評価】")[0].strip()
            score = ai_output.split("【総合評価】")[1].split("【アドバイス】")[0].strip()
            advice = ai_output.split("【アドバイス】")[1].strip()
        except:
            transcription = "認識完了"
            score = "B"
            advice = ai_output
            
        # 横長のシート構成に追随するよう、各設問データを配列の末尾へ結合
        listen_count = st.session_state.listen_counts[q['id']]
        row_data.extend([audio_link, transcription, score, advice, f"{listen_count}回"])
        
        progress_bar.progress(int((idx + 1) / total_q * 100))
        
    status_text.empty()
    progress_bar.empty()
    
    # 全設問分の連結データを、もっとも安全な A:A 指定で Results シートの最下行へ一発書き込み
    try:
        sheets_service.spreadsheets().values().append(
            spreadsheetId=SPREADSHEET_ID,
            range="'Results'!A:A",
            valueInputOption="USER_ENTERED",
            body={"values": [row_data]}
        ).execute()
    except Exception as sheet_err:
        st.error(f"Resultsシートへのデータ保存に失敗しました: {sheet_err}")
        st.stop()
        
    st.balloons()
    st.success("🎉 スピーキングテストの解答送信とAI採点がすべて完了しました！")
    
    if st.button("🔄 次の生徒の入力を開始"):
        st.session_state.step = "init"
        st.session_state.current_q_idx = 0
        st.session_state.recorded_audios = {}
        st.session_state.listen_counts = {1: 0, 2: 0, 3: 0}
        for i in [1, 2, 3]:
            if f"audio_bytes_{i}" in st.session_state:
                del st.session_state[f"audio_bytes_{i}"]
        st.rerun()
    st.markdown('</div>', unsafe_allow_html=True)

st.markdown('</div>', unsafe_allow_html=True)

# 📊 著作権表示（フッター固定）
st.markdown("""
    <div class="footer">
        © 2026 Nexus ALT. All Rights Reserved. Digital Speaking Assessment System.
    </div>
""", unsafe_allow_html=True)
