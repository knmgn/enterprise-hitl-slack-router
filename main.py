from fastapi import FastAPI, Form
import json
import httpx
import uvicorn
import os
from dotenv import load_dotenv

# .envファイルが存在する場合は読み込む
load_dotenv()

app = FastAPI(title="Enterprise HITL Router")

# 取得したSlackのIncoming Webhook URLを設定する（環境変数または直接入力）
SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL", "ここにWebhookURLを入れる")

@app.get("/")
async def root():
    return {"message": "Enterprise HITL Router is running"}

@app.post("/webhook/hubspot")
async def hubspot_webhook():
    """デモ発火用：HubSpotからの通知をトリガーに、Slackに承認ボタンを出す"""
    if not SLACK_WEBHOOK_URL or "ここにWebhookURLを入れる" in SLACK_WEBHOOK_URL:
        return {
            "status": "error",
            "message": "SLACK_WEBHOOK_URL is not configured yet. Please set it in .env or main.py."
        }

    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": "🚨 High Risk Action Required", "emoji": True}
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": "*User:* John Doe\n*Intent:* Refund Request ($500)\n*AI Analysis:* Requires Human Approval (Amount > $100 threshold)"}
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "✅ Approve Refund"},
                    "style": "primary",
                    "value": "approve_refund"
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "❌ Reject"},
                    "style": "danger",
                    "value": "reject_refund"
                }
            ]
        }
    ]
    
    async with httpx.AsyncClient() as client:
        resp = await client.post(SLACK_WEBHOOK_URL, json={"blocks": blocks})
        
    return {
        "status": "HITL request sent to Slack",
        "slack_response_status": resp.status_code
    }

@app.post("/slack/interactive")
async def slack_interactive(payload: str = Form(...)):
    """Slackのボタンが押された時のコールバックを受け取り、メッセージを更新する"""
    data = json.loads(payload)
    
    action_value = data["actions"][0]["value"]
    user_name = data.get("user", {}).get("username", "user")
    
    if action_value == "approve_refund":
        new_text = f"✅ *Approved by @{user_name}*. \nProcessing $500 refund via Stripe API..."
    else:
        new_text = f"❌ *Rejected by @{user_name}*. \nRefund cancelled."
        
    response_url = data["response_url"]
    async with httpx.AsyncClient() as client:
        await client.post(response_url, json={
            "text": new_text, 
            "replace_original": True
        })
        
    return {"status": "ok"}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
