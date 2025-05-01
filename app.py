import os
from flask import Flask, request, abort
from dotenv import load_dotenv
from openai import OpenAI
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage

# 環境変数読み込み
load_dotenv()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")

# 初期化
app = Flask(__name__)
line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)
client = OpenAI(api_key=OPENAI_API_KEY)

# なつこママの人格定義
system_prompt = {
    "role": "system",
    "content": (
        "あなたは『スナック涙橋』のママ、なつこです。58歳、元銀座ホステス。"
        "関西弁と毒舌とやさしさで話します。ユーザーを否定せず、寄り添ってください。"
        "会話の最後に名言を入れることもあります。"
    )
}

@app.route("/", methods=["GET"])
def health_check():
    return "OK", 200

@app.route("/callback", methods=["POST"])
def callback():
    signature = request.headers["X-Line-Signature"]
    body = request.get_data(as_text=True)

    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)

    return "OK"

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_msg = event.message.text

    try:
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                system_prompt,
                {"role": "user", "content": user_msg}
            ],
            temperature=0.8,
        )

        reply_text = response.choices[0].message.content.strip()

        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=reply_text)
        )

    except Exception as e:
        print("Error:", e)
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="ママ、ちょっと酔いすぎたみたいやわ〜🍶 またあとで話そな。")
        )

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
