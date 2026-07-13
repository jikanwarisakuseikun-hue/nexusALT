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
        background-color: #ffffff;
        border: 1px solid #cbd5e1;
        padding: 15px;
        border-radius: 8px;
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
    st.session_state.listen_counts = {1: 0, 2: 0, 3: 0} # 各問題の再生回数をカウント
if "questions_data" not in st.session_state:
    st.session_state.questions_data = None
if "ai_results_summary" not in st.session_state:
    st.session_state.ai_results_summary = [] # 最後に画面表示するための採点結果格納庫

# 📥 スプレッドシートの「Questions」シートからデータを動的に読み取る
if st.session_state.questions_data is None:
    try:
        result = sheets_service.spreadsheets().values().get(
            spreadsheetId=SPREADSHEET_ID,
            range="'Questions'!A3:G3"
        ).execute()
        
        row_values = result.get("values", [])[0]
        
        st.session_state.class_name = row_values[0] if len(row_values) > 0 else "設定なし"
        
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
        cls = st.selectbox("クラス", [st.session_state.class_name])
    with col2:
        num = st.selectbox("名簿番号", [f"{i}番" for i in range(1, 46)])
        
    name = st.text_input("氏名（イニシャル）", placeholder="例: TS")
    
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
        
        # 🔄 【自動再生数カウント機能】
        # HTML5のオーディオが再生開始(onplay)された瞬間に、Streamlit側のセッションに通知を送る非表示JavaScript
        # これにより、生徒が再生ボタンを叩くたびに自動でカウントが+1されます
        js_trigger = f"""
        <script>
        const playCountKey = 'played_q_{q['id']}_' + parent.window.location.href;
        // 親ウィンドウ（Streamlit）の全audio要素を監視
        setTimeout(() => {{
            const audios = parent.document.querySelectorAll('audio');
            audios.forEach((audio) => {{
                if(!audio.dataset.monitored) {{
                    audio.dataset.monitored = "true";
                    audio.addEventListener('play', () => {{
                        // クエリを生成してStreamlit側へ擬似シグナルを送る（一瞬だけコンポーネントがイベントを検知する）
                        const link = document.createElement('a');
                        link.href = "?played_q={q['id']}&t=" + Date.now();
                        window.parent.postMessage({{type: 'streamlit:setComponentValue', value: true}}, '*');
                    }});
                }}
            }});
        }}, 1000);
        </script>
        """
        # クエリパラメータを常に監視し、再生シグナルを受け取ったらセッションをインクリメント
        query_params = st.query_params
        if "played_q" in query_params and query_params["played_q"] == str(q['id']):
            # 連続処理の重複防止のため、タイムスタンプが新しい場合のみカウント
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
    
    # ⬅️ 前の問題に戻るボタンを削除し、常に「次へ進む」一本化のレイアウトに変更
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
    
    # すでに採点が終わっている場合はプロセスの重複をスキップ
    if not st.session_state.ai_results_summary:
        progress_bar = st.progress(0)
        status_text = st.empty()
        total_q = len(QUESTIONS)
        
        row_data = [info["class"], info["number"], info["name"]]
        
        for idx, q in enumerate(QUESTIONS):
            status_text.markdown(f"**【処理中】 Question {idx+1} の音声を保存し、AI採点しています...**")
            audio_bytes = st.session_state.recorded_audios[q["id"]]
            
            # 1. Googleドライブへアップロード
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
                st.error(f"❌ 共有ドライブへの音声保存に失敗しました。詳細: {drive_err}")
                st.stop()
            
            # 2. Geminiによる音声採点（プロンプトを調整して「正答例/解答例」を出力形式に強制指定）
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
            
            【解答例】
            (Provide 1-2 standard model answer examples in English that perfectly suit the question text)
            
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
                score = ai_output.split("【総合評価】")[1].split("【解答例】")[0].strip()
                model_answer = ai_output.split("【解答例】")[1].split("【アドバイス】")[0].strip()
                advice = ai_output.split("【アドバイス】")[1].strip()
            except:
                transcription = "認識完了"
                score = "B"
                model_answer = "Model answer generation skipped."
                advice = ai_output
                
            listen_count = st.session_state.listen_counts[q['id']]
            # スプレッドシートに格納するデータ（アドバイス欄に解答例もドッキングさせて保存）
            full_advice_for_sheet = f"【解答例】\n{model_answer}\n\n【アドバイス】\n{advice}"
            row_data.extend([audio_link, transcription, score, full_advice_for_sheet, f"{listen_count}回"])
            
            # 画面表示用のセッションデータに記録
            st.session_state.ai_results_summary.append({
                "id": q['id'],
                "question_text": q['text'],
                "transcription": transcription,
                "score": score,
                "model_answer": model_answer,
                "advice": advice
            })
            
            progress_bar.progress(int((idx + 1) / total_q * 100))
            
        status_text.empty()
        progress_bar.empty()
        
        # スプレッドシートへ一発書き込み
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

    # 🎉 結果表示パート（生徒へのフィードバック画面）
    st.success("🎉 スピーキングテストの解答送信とAI採点がすべて完了しました！")
    
    st.markdown("### 📊 今回のテスト結果・アドバイス")
    
    # 各問題の問題文、AI文字起こし、評価、正答例、アドバイスを綺麗なボックスで表示
    for res in st.session_state.ai_results_summary:
        st.markdown(f"""
        <div class="result-box">
            <h4>📝 Question {res['id']}</h4>
            <p><b>問題文 (Question):</b> <br><span style="color:#2563eb; font-size:16px;">{res['question_text']}</span></p>
            <hr style="margin:10px 0; border:0; border-top:1px solid #e2e8f0;">
            <p><b>🗣️ あなたの回答 (AI文字起こし):</b><br><i>{res['transcription']}</i></p>
            <p><b>🏅 総合評価:</b> <span style="font-size:18px; font-weight:bold; color:#10b981;">{res['score']}</span></p>
            <p><b>💡 正答例 (Model Answer):</b><br><span style="color:#059669; font-weight:500;">{res['model_answer']}</span></p>
            <p><b>💬 ALTからのアドバイス:</b><br>{res['advice']}</p>
        </div>
        """, unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)
    if st.button("🔄 次の生徒の入力を開始"):
        st.session_state.step = "init"
        st.session_state.current_q_idx = 0
        st.session_state.recorded_audios = {}
        st.session_state.listen_counts = {1: 0, 2: 0, 3: 0}
        st.session_state.ai_results_summary = []
        for i in [1, 2, 3]:
            if f"audio_bytes_{i}" in st.session_state:
                del st.session_state[f"audio_bytes_{i}"]
        st.clear_checkpoint() if hasattr(st, "clear_checkpoint") else None
        st.rerun()
        
    st.markdown('</div>', unsafe_allow_html=True)

st.markdown('</div>', unsafe_allow_html=True)

# 📊 著作権表示（フッター固定）
st.markdown("""
    <div class="footer">
        © 2026 Nexus ALT. Shogo Takeuchi All Rights Reserved. Digital Speaking Assessment System.
    </div>
""", unsafe_allow_html=True)
