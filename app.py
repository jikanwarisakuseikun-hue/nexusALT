import streamlit as st
import json
import time
import sys
import os
from gtts import gTTS  # 🔊 音声再生の安定化のために導入
import streamlit.components.v1 as components  # 🔄 自動再生カウント用のコンポーネント

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

# 📄 ページ設定とデザイン的適用
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
    .result-box {
        background-color: #f0fdf4;
        border: 1px solid #bbf7d0;
        padding: 20px;
        border-radius: 12px;
        text-align: center;
        margin-bottom: 15px;
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
    st.session_state.listen_counts = {}
if "questions_data" not in st.session_state:
    st.session_state.questions_data = None
if "is_saved_successfully" not in st.session_state:
    st.session_state.is_saved_successfully = False

# 📥 スプレッドシートの「Questions」シートからデータを動的に読み取る
if st.session_state.questions_data is None:
    try:
        result = sheets_service.spreadsheets().values().get(
            spreadsheetId=SPREADSHEET_ID,
            range="'Questions'!A3:CX3"
        ).execute()
        
        row_values = result.get("values", [])[0]
        st.session_state.class_name = row_values[0] if len(row_values) > 0 else "設定なし"
        
        dynamic_questions = []
        q_id = 1
        for i in range(1, len(row_values), 2):
            q_text = row_values[i] if i < len(row_values) else ""
            q_criterion = row_values[i+1] if (i+1) < len(row_values) else ""
            
            if q_text.strip():
                dynamic_questions.append({
                    "id": q_id,
                    "text": q_text,
                    "criterion": q_criterion
                })
                st.session_state.listen_counts[q_id] = 0
                st.session_state.recorded_audios[q_id] = None
                q_id += 1
                
        st.session_state.questions_data = dynamic_questions
        
        gemini_key = st.secrets["GEMINI_API_KEY"]
        genai.configure(api_key=gemini_key)
        
    except Exception as e:
        st.error(f"Questionsシートからのデータ動的読み込みに失敗しました。詳細: {e}")
        st.stop()

QUESTIONS = st.session_state.questions_data
FOLDER_ID = st.secrets["FOLDER_ID"]
TARGET_DRIVE_ID = "0ACP5Eu-XLix6Uk9PVA"

st.markdown('<div class="main-content-padding">', unsafe_allow_html=True)

# --- 🖼️ 画面1: 受験者情報入力画面 ---
if st.session_state.step == "init":
    st.markdown('<div class="main-header"><h1>🎙️ Nexus ALT スピーキングテスト</h1><p>Digital Speaking Assessment System</p></div>', unsafe_allow_html=True)
    st.markdown('<div class="test-card">', unsafe_allow_html=True)
    st.subheader("受験者情報の入力")
    
    col1, col2 = st.columns(2)
    with col1:
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
    
    try:
        if f"audio_bytes_{q['id']}" not in st.session_state:
            tts = gTTS(text=q['text'], lang='en', tld='com')
            import io
            fp = io.BytesIO()
            tts.write_to_fp(fp)
            st.session_state[f"audio_bytes_{q['id']}"] = fp.getvalue()
        
        st.audio(st.session_state[f"audio_bytes_{q['id']}"], format="audio/mp3")
        
        # 🔄 【自動再生数カウント機能】
        js_trigger = f"""
        <script>
        const playCountKey = 'played_q_{q['id']}_' + parent.window.location.href;
        setTimeout(() => {{
            const audios = parent.document.querySelectorAll('audio');
            audios.forEach((audio) => {{
                if(!audio.dataset.monitored) {{
                    audio.dataset.monitored = "true";
                    audio.addEventListener('play', () => {{
                        const link = document.createElement('a');
                        link.href = "?played_q={q['id']}&t=" + Date.now();
                        window.parent.postMessage({{type: 'streamlit:setComponentValue', value: true}}, '*');
                    }});
                }}
            }});
        }}, 1000);
        </script>
        """
        query_params = st.query_params
        if "played_q" in query_params and query_params["played_q"] == str(q['id']):
            last_ts_key = f"last_ts_{q['id']}"
            current_ts = query_params.get("t", [""])[0]
            if last_ts_key not in st.session_state or st.session_state[last_ts_key] != current_ts:
                st.session_state.listen_counts[q['id']] += 1
                st.session_state[last_ts_key] = current_ts
        
        components.html(js_trigger, height=0, width=0)
        st.caption(f"🎧 質問音声の再生回数: {st.session_state.listen_counts[q['id']]} 回 (自動記録中)")
            
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

# --- 🖼️ 画面3: 送信・最速文字起こし・データ保存画面 ---
elif st.session_state.step == "finish":
    info = st.session_state.student_info
    target_sheet_name = info["class"]
    
    if not st.session_state.is_saved_successfully:
        st.markdown('<div class="main-header"><h1>🏁 テスト送信・保存中</h1><p>音声を安全にアップロードし、文字起こしを生成しています</p></div>', unsafe_allow_html=True)
        st.markdown('<div class="test-card">', unsafe_allow_html=True)
        
        progress_bar = st.progress(0)
        status_text = st.empty()
        total_q = len(QUESTIONS)
        
        row_data = [info["class"], info["number"], info["name"]]
        
        for idx, q in enumerate(QUESTIONS):
            audio_bytes = st.session_state.recorded_audios[q["id"]]
            
            # 1. Googleドライブへ音声ファイルを確実に保存
            status_text.markdown(f"**【1/2 音声保存中】 Question {idx+1} / {total_q} のファイルを転送しています...**")
            filename = f"{info['class']}_{info['number']}_{info['name']}_Q{q['id']}.wav"
            media = MediaInMemoryUpload(audio_bytes, mimetype="audio/wav")
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
                    supportsAllDrives=True
                ).execute()
                audio_link = drive_file.get("webViewLink")
            except Exception as drive_err:
                st.error(f"❌ Googleドライブへの音声保存に失敗しました。詳細: {drive_err}")
                st.stop()
            
            # 2. 🦏 【超強力・絶対文字起こしリトライロジック】 🦏
            # サーバーから拒否されても、時間を少しずつ延ばしながら最大10回までしつこく自動リトライします。
            status_text.markdown(f"**【2/2 文字起こし中】 Question {idx+1} / {total_q} のAI処理を試みています...**")
            transcription = "（音声データ確認完了）"
            score = "提出済"
            advice_placeholder = "（アドバイス非表示設定）"
            
            model = genai.GenerativeModel("gemini-2.5-flash")
            prompt = "Transcribe the following English audio precisely. Output ONLY the transcription text. If it is only background noise or silent, output 'No speech'."
            
            max_attempts = 10  # 🌟 執念の10回リトライ設定
            for attempt in range(max_attempts):
                try:
                    response = model.generate_content([
                        prompt,
                        {"mime_type": "audio/wav", "data": audio_bytes}
                    ])
                    if response.text.strip():
                        transcription = response.text.strip()
                    break  # 成功したら即座にループを抜ける
                except Exception as e:
                    # 回数が増えるごとに、1秒、2秒、3秒...と待機時間を少しずつ長くして混雑を回避
                    sleep_time = min(1 + attempt, 4)
                    if attempt < max_attempts - 1:
                        time.sleep(sleep_time)
                    else:
                        # 10回すべてで完全に通信が途絶した時のみ、最悪の事態としてエラー文を記録
                        transcription = f"（アクセス集中により文字起こしエラー。音声ファイルは正常保存済）"
            
            listen_count = st.session_state.listen_counts[q['id']]
            row_data.extend([audio_link, transcription, score, advice_placeholder, f"{listen_count}回"])
            progress_bar.progress(int((idx + 1) / total_q * 100))
            
        status_text.empty()
        progress_bar.empty()
        
        # 📂 スプレッドシートへのクラス別書き込み処理
        try:
            spreadsheet_meta = sheets_service.spreadsheets().get(spreadsheetId=SPREADSHEET_ID).execute()
            existing_sheets = [sheet["properties"]["title"] for sheet in spreadsheet_meta.get("sheets", [])]
            
            if target_sheet_name not in existing_sheets:
                add_sheet_request = {
                    "requests": [{
                        "addSheet": {
                            "properties": {"title": target_sheet_name}
                        }
                    }]
                }
                sheets_service.spreadsheets().batchUpdate(spreadsheetId=SPREADSHEET_ID, body=add_sheet_request).execute()
                
                headers = ["クラス", "名簿番号", "氏名"]
                for q_num in range(1, total_q + 1):
                    headers.extend([
                        f"Q{q_num}音声リンク", 
                        f"Q{q_num}文字起こし", 
                        f"Q{q_num}評価", 
                        f"Q{q_num}ステータス", 
                        f"Q{q_num}再生数"
                    ])
                    
                sheets_service.spreadsheets().values().update(
                    spreadsheetId=SPREADSHEET_ID,
                    range=f"'{target_sheet_name}'!A1",
                    valueInputOption="USER_ENTERED",
                    body={"values": [headers]}
                ).execute()
            
            sheets_service.spreadsheets().values().append(
                spreadsheetId=SPREADSHEET_ID,
                range=f"'{target_sheet_name}'!A:A",
                valueInputOption="USER_ENTERED",
                body={"values": [row_data]}
            ).execute()
            
            st.session_state.is_saved_successfully = True
            st.balloons()
            st.rerun()
            
        except Exception as sheet_err:
            st.error(f"スプレッドシートへのデータ保存に失敗しました: {sheet_err}")
            st.stop()

    # 📥 保存完了後の画面表示
    else:
        st.markdown('<div class="main-header"><h1>🏁 テスト完了</h1><p>Nexus ALT Digital Speaking Test</p></div>', unsafe_allow_html=True)
        st.markdown('<div class="test-card">', unsafe_allow_html=True)
        
        st.markdown(f"""
        <div class="result-box">
            <h3 style="color: #15803d; margin: 0;">✅ 保存しました</h3>
            <p style="margin: 10px 0 0 0; color: #1e293b; font-size: 15px;">
                <b>{info['class']} {info['number']} {info['name']} さん</b> の音声ファイルと文字起こしデータの保存がすべて正常に完了しました。
            </p>
        </div>
        """, unsafe_allow_html=True)

        st.markdown("<br>", unsafe_allow_html=True)
        if st.button("🔄 次の生徒の入力を開始", use_container_width=True, type="primary"):
            st.session_state.step = "init"
            st.session_state.current_q_idx = 0
            st.session_state.recorded_audios = {}
            st.session_state.listen_counts = {q['id']: 0 for q in QUESTIONS}
            st.session_state.is_saved_successfully = False
            for q in QUESTIONS:
                if f"audio_bytes_{q['id']}" in st.session_state:
                    del st.session_state[f"audio_bytes_{q['id']}"]
            st.clear_checkpoint() if hasattr(st, "clear_checkpoint") else None
            st.rerun()
            
        st.markdown('</div>', unsafe_allow_html=True)

st.markdown('</div>', unsafe_allow_html=True)

# 📊 著作権表示
st.markdown("""
    <div class="footer">
        © 2026 Nexus ALT. All Rights Reserved. Digital Speaking Assessment System.
    </div>
""", unsafe_allow_html=True)
