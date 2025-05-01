import os
import datetime
import traceback
from flask import Flask, request, abort, redirect
from dotenv import load_dotenv
from openai import OpenAI
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage, TemplateSendMessage, ButtonsTemplate, URITemplateAction
import firebase_admin
from firebase_admin import credentials, firestore
import stripe

# --- 環境変数の読み込み ---
load_dotenv()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "whsec_J8sueygWFxeaDwJiqR9kTmT94ZClM9Rc")
STRIPE_PRICE_ID = os.getenv("STRIPE_PRICE_ID")
RENDER_URL = os.getenv("RENDER_URL", "https://snack-namida-bashi.onrender.com")

# --- LINE BotとFlaskの初期化 ---
app = Flask(__name__)
line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)
client = OpenAI()

# --- Firestore初期化 ---
cred = credentials.Certificate("firebase_key.json")
firebase_admin.initialize_app(cred)
db = firestore.client()

# --- Stripe初期化 ---
stripe.api_key = os.getenv("STRIPE_SECRET_KEY")

# --- みつきママのキャラクター設定 ---
system_prompt = {
    "role": "system",
    "content": (
        "あなたは『スナック涙橋』のママ、みつきです。35歳。元銀座ホステス。"
        "落ち着きとやさしさがありながらも、若めで今っぽい言葉づかいで話します。"
        "たまに砕けた口調も混ざりますが、ユーザーに自然に寄り添うことが大事です。"
    )
}

@app.route("/", methods=["GET"])
def health_check():
    return "OK", 200

@app.route("/callback", methods=["POST"])
def callback():
    signature = request.headers.get("X-Line-Signature")
    body = request.get_data(as_text=True)

    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        print("署名エラー")
        abort(400)

    return "OK"

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_msg = event.message.text
    user_id = event.source.user_id
    today = datetime.datetime.now().strftime("%Y%m%d")
    usage_ref = db.collection("users").document(user_id).collection("usage").document(today)
    profile_ref = db.collection("users").document(user_id)

    profile_doc = profile_ref.get()
    status = profile_doc.to_dict().get("status", "free") if profile_doc.exists else "free"

    usage_doc = usage_ref.get()
    usage_data = usage_doc.to_dict() if usage_doc.exists else {}
    count = usage_data.get("count", 0)
    blocked = usage_data.get("blocked", False)

    if not usage_doc.exists:
        usage_ref.set({"count": 0, "blocked": False})
        count = 0
        blocked = False

    limit = 6 if status == "free" else 60

    if user_msg.strip() in ["残り通数", "あと何通？", "のこり"]:
        remaining = max(0, limit - count)
        message = f"今日はあと {remaining} 通話せるよ！" if remaining > 0 else "今日はもうおしゃべり終わりかな。また明日ね〜！"
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=message))
        return

    if blocked:
        return

    if count == limit - 1:
        if status == "free":
            pay_url = f"{RENDER_URL}/pay?user_id={user_id}"
            line_bot_api.reply_message(
                event.reply_token,
                TemplateSendMessage(
                    alt_text="有料プランのご案内",
                    template=ButtonsTemplate(
                        title="本日の会話上限に達しました",
                        text="もっと話したい方は、有料プランをご検討ください",
                        actions=[
                            URITemplateAction(label="有料プランはこちら", uri=pay_url)
                        ]
                    )
                )
            )
        else:
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="ごめん、ちょっと今立て込んでてまた明日返信するね！ほんとにごめん！")
            )
        usage_ref.set({"count": count + 1, "blocked": True})
        return

    if count >= limit:
        return

    try:
        history_ref = db.collection("users").document(user_id).collection("history")
        history_query = history_ref.order_by("timestamp", direction=firestore.Query.DESCENDING).limit(5).stream()
        chat_history = [system_prompt]
        for doc in reversed(list(history_query)):
            entry = doc.to_dict()
            chat_history.append({"role": entry["role"], "content": entry["content"]})

        chat_history.append({"role": "user", "content": user_msg})

        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=chat_history,
            temperature=0.8,
        )

        reply_text = response.choices[0].message.content.strip()
        print(f"🗣 ママの返答: {reply_text}")

        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=reply_text)
        )

        history_ref.add({"role": "user", "content": user_msg, "timestamp": datetime.datetime.now()})
        history_ref.add({"role": "assistant", "content": reply_text, "timestamp": datetime.datetime.now()})

        usage_ref.set({"count": count + 1, "blocked": False})

    except Exception as e:
        print("❌ エラー発生！詳細↓")
        traceback.print_exc()
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="ママ、ちょっと酔いすぎたみたい。またあとで話そね。")
        )

@app.route("/webhook/stripe", methods=["POST"])
def stripe_webhook():
    payload = request.data
    sig_header = request.headers.get("Stripe-Signature")
    try:
        event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
    except stripe.error.SignatureVerificationError:
        print("⚠️ Stripe署名検証に失敗しました")
        abort(400)

    session = event.get("data", {}).get("object", {})
    user_id = session.get("metadata", {}).get("user_id")

    if not user_id:
        print("⚠️ user_id が metadata に含まれていません")
        return "NG"

    if event["type"] == "checkout.session.completed":
        print(f"✅ ユーザー {user_id} の支払い完了を検知")
        db.collection("users").document(user_id).set({"status": "paid"}, merge=True)
    elif event["type"] in ["customer.subscription.deleted", "invoice.payment_failed"]:
        print(f"❌ ユーザー {user_id} のサブスクリプションがキャンセル／失敗されました")
        db.collection("users").document(user_id).set({"status": "free"}, merge=True)

    return "OK"

@app.route("/pay", methods=["GET"])
def create_checkout():
    user_id = request.args.get("user_id")
    if not user_id:
        return "user_id が指定されていません", 400

    try:
        checkout_session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            line_items=[{
                "price": STRIPE_PRICE_ID,
                "quantity": 1,
            }],
            mode="subscription",
            metadata={"user_id": user_id},
            success_url="https://lin.ee/xxxxx",
            cancel_url="https://lin.ee/xxxxx"
        )
        return redirect(checkout_session.url, code=303)
    except Exception as e:
        print("❌ Checkout セッション作成エラー")
        traceback.print_exc()
        return "エラー", 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
