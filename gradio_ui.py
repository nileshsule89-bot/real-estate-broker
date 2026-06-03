import uuid
from pathlib import Path

import gradio as gr
from app.database import SessionLocal
from app.services.agent_service import handle_user_message

CSS = """
body, .gradio-container {
  margin: 0;
  background: #000000;
  color: #ffffff;
  min-height: 100vh;
}

.gradio-container {
  max-width: 100% !important;
  min-height: 100vh;
  padding: 24px !important;
}

#shell {
  width: min(960px, 100%);
  min-height: calc(100vh - 48px);
  margin: 0 auto;
  padding: clamp(18px, 3vw, 28px);
  border: 1px solid #1f1f1f;
  border-radius: 28px;
  background: #000000;
  display: flex;
  flex-direction: column;
  justify-content: center;
}

#header-band {
  padding: 8px 4px 18px 4px;
  border-radius: 0;
  background: transparent;
  color: #ffffff;
  margin-bottom: 8px;
}

#header-band h1 {
  margin: 0;
  font-size: clamp(1.4rem, 2.8vw, 2rem);
  color: #ffffff;
  font-weight: 600;
}

#header-band p {
  margin: 6px 0 0 0;
  color: #b3b3b3;
  line-height: 1.45;
  max-width: 720px;
}

#chatbot {
  min-height: 62vh;
  border: 1px solid #1f1f1f !important;
  background: #000000 !important;
  border-radius: 24px !important;
  padding: 12px !important;
}

#chatbot .message {
  max-width: 82%;
  width: fit-content !important;
  min-width: 72px;
  border-radius: 22px !important;
  padding: 12px 14px !important;
  line-height: 1.5 !important;
  box-shadow: none !important;
  white-space: normal !important;
  word-break: keep-all !important;
  overflow-wrap: break-word !important;
}

#chatbot .message.user {
  background: #1b1b1b !important;
  color: #ffffff !important;
  border: 1px solid #2b2b2b !important;
}

#chatbot .message.bot {
  background: #0b0b0b !important;
  color: #ffffff !important;
  border: 1px solid #1f1f1f !important;
}

#chatbot .message * {
  color: inherit !important;
  word-break: keep-all !important;
  overflow-wrap: break-word !important;
}

#chatbot .message p,
#chatbot .message span,
#chatbot .message div {
  white-space: normal !important;
  width: fit-content;
  max-width: 100%;
  margin: 0 !important;
  word-break: keep-all !important;
  overflow-wrap: break-word !important;
}

#chatbot .message.bot.loading-bubble,
#chatbot .message.bot .loading-bubble {
  white-space: nowrap !important;
  min-width: 54px;
}

.loading-dots {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  white-space: nowrap;
  min-width: 42px;
  height: 12px;
}

.loading-dots span {
  width: 7px;
  height: 7px;
  border-radius: 999px;
  background: #9ca3af;
  display: inline-block;
  animation: loadingPulse 1s infinite ease-in-out;
}

.loading-dots span:nth-child(2) {
  animation-delay: 0.15s;
}

.loading-dots span:nth-child(3) {
  animation-delay: 0.3s;
}

@keyframes loadingPulse {
  0%, 80%, 100% {
    opacity: 0.28;
    transform: scale(0.82);
  }
  40% {
    opacity: 1;
    transform: scale(1);
  }
}

#composer-row {
  --composer-size: clamp(44px, 11vmin, 56px);
  --composer-icon: clamp(18px, 4.8vmin, 24px);
  --composer-pad-x: clamp(12px, 3.2vmin, 18px);
  --composer-pad-y: clamp(10px, 2.6vmin, 14px);
  --composer-font: clamp(0.95rem, 2.8vmin, 1rem);
  align-items: flex-end;
  gap: clamp(6px, 1.8vmin, 10px);
  margin-top: 16px;
}

#attach-btn,
#send-btn {
  flex: 0 0 auto;
  align-self: flex-end;
  min-width: 0 !important;
  width: var(--composer-size) !important;
  max-width: var(--composer-size) !important;
}

#attach-btn > .wrap,
#attach-btn > label,
#attach-btn button,
#send-btn > .wrap,
#send-btn button {
  margin: 0 !important;
  box-sizing: border-box !important;
  min-width: var(--composer-size) !important;
  max-width: var(--composer-size) !important;
  width: var(--composer-size) !important;
  height: var(--composer-size) !important;
  min-height: var(--composer-size) !important;
  max-height: var(--composer-size) !important;
  padding: 0 !important;
  border-radius: 999px !important;
  display: inline-flex !important;
  align-items: center !important;
  justify-content: center !important;
  box-shadow: none !important;
}

#attach-btn button,
#attach-btn label {
  background: transparent !important;
  border: 1px solid #3a3a3a !important;
  color: #ffffff !important;
  font-size: var(--composer-icon) !important;
  line-height: 1 !important;
}

#attach-btn button:hover {
  background: #1a1a1a !important;
  border-color: #4a4a4a !important;
}

#attach-btn .file-preview,
#attach-btn .file-name,
#attach-btn .upload-text {
  display: none !important;
}

#send-btn button {
  background: #25d366 !important;
  border: none !important;
  color: #ffffff !important;
}

#send-btn button:hover {
  background: #20bd5a !important;
}

#send-btn button::before {
  content: "";
  display: block;
  width: var(--composer-icon);
  height: var(--composer-icon);
  background: center / contain no-repeat url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='white'%3E%3Cpath d='M2.01 21 23 12 2.01 3 2 10l15 2-15 2z'/%3E%3C/svg%3E");
}

#send-btn button span,
#send-btn .icon,
#send-btn button svg {
  display: none !important;
}

#composer {
  flex: 1 1 auto;
  min-width: 0;
  align-self: flex-end;
}

#composer > .wrap,
#composer textarea,
#composer label {
  background: transparent !important;
}

#composer > .wrap {
  width: 100% !important;
}

#composer textarea {
  box-sizing: border-box !important;
  width: 100% !important;
  min-height: var(--composer-size) !important;
  border-radius: 999px !important;
  color: #ffffff !important;
  background: #111111 !important;
  border: 1px solid #2b2b2b !important;
  padding: var(--composer-pad-y) var(--composer-pad-x) !important;
  font-size: var(--composer-font) !important;
  line-height: 1.35 !important;
}

#composer textarea::placeholder {
  color: #8f8f8f !important;
}

footer,
.footer,
[class*="footer"],
[data-testid="footer"] {
  display: none !important;
}

@media (max-width: 768px) {
  #shell {
    min-height: calc(100vh - 24px);
    padding: 14px;
    border-radius: 20px;
  }

  #chatbot {
    min-height: 60vh;
  }

  #chatbot .message {
    max-width: 92%;
  }

  #composer-row {
    --composer-size: clamp(42px, 12vw, 52px);
    --composer-icon: clamp(17px, 5vw, 22px);
    margin-top: 12px;
  }
}
"""

WAITING_HTML = (
    '<span class="loading-bubble">'
    '<span class="loading-dots" aria-label="Waiting for response">'
    "<span></span><span></span><span></span>"
    "</span>"
    "</span>"
)


def _new_session_phone() -> str:
    return f"web-{uuid.uuid4().hex[:12]}"


def _fetch_reply(message: str, session_phone: str, image_paths: list[str] | None = None) -> dict:
    db = SessionLocal()
    try:
        return handle_user_message(db, session_phone, message, image_paths=image_paths or [])
    except Exception as exc:
        return {"reply": f"Something went wrong: {exc}", "media": []}
    finally:
        db.close()


def _append_user_message(message: str, images, history: list) -> tuple[list, str, str, list, None]:
    image_paths = [str(Path(item)) for item in (images or []) if item]
    if not message.strip() and not image_paths:
        return history, "", "", [], None

    updated_history = list(history)
    updated_history.append({"role": "user", "content": message or "Uploaded property photos"})
    for image_path in image_paths:
        updated_history.append(
            {
                "role": "user",
                "content": gr.Image(value=image_path, show_label=False, interactive=False),
            }
        )
    updated_history.append({"role": "assistant", "content": WAITING_HTML})
    return updated_history, "", message, image_paths, None


def _append_assistant_reply(history: list, pending_message: str, pending_images: list[str], session_phone: str) -> tuple[list, str, list]:
    if not pending_message and not pending_images:
        return history, "", []

    result = _fetch_reply(pending_message, session_phone, pending_images)
    reply = result.get("reply", "No reply")
    media = result.get("media", [])

    updated_history = list(history)
    if updated_history and updated_history[-1].get("role") == "assistant":
        updated_history[-1] = {"role": "assistant", "content": reply}
    else:
        updated_history.append({"role": "assistant", "content": reply})

    for item in media:
        if item.get("type") == "image" and item.get("url"):
            updated_history.append(
                {
                    "role": "assistant",
                    "content": gr.Image(value=item["url"], show_label=False, interactive=False),
                }
            )
    return updated_history, "", []


def create_demo() -> gr.Blocks:
    theme = gr.themes.Soft(
        primary_hue="gray",
        secondary_hue="gray",
        neutral_hue="gray",
        radius_size=gr.themes.sizes.radius_sm,
    )

    with gr.Blocks(title="Real Estate Broker Agent", theme=theme, css=CSS) as demo:
        pending_message = gr.State("")
        pending_images = gr.State([])
        session_phone = gr.State(_new_session_phone)

        with gr.Column(elem_id="shell"):
            gr.Markdown(
                """
                <div id="header-band">
                  <h1>Chat With Nilesh</h1>
                  <p>Tell Nilesh what you want, where you want it, and your budget. He will help you find options, compare them, share photos, and move you toward a walkthrough or agreement.</p>
                </div>
                """
            )

            chatbot = gr.Chatbot(
                show_label=False,
                height="58vh",
                buttons=["copy"],
                layout="bubble",
                elem_id="chatbot",
                placeholder="Send a message to start chatting with Nilesh.",
                avatar_images=(None, None),
                group_consecutive_messages=False,
            )

            with gr.Row(elem_id="composer-row"):
                images = gr.UploadButton(
                    "+",
                    file_count="multiple",
                    file_types=["image"],
                    scale=0,
                    min_width=0,
                    elem_id="attach-btn",
                )
                msg = gr.Textbox(
                    show_label=False,
                    placeholder="Message",
                    lines=1,
                    max_lines=4,
                    scale=1,
                    elem_id="composer",
                )
                send_btn = gr.Button(
                    "",
                    scale=0,
                    min_width=0,
                    elem_id="send-btn",
                )

        submit_inputs = [msg, images, chatbot]
        submit_outputs = [chatbot, msg, pending_message, pending_images, images]

        submit_event = msg.submit(
            _append_user_message,
            submit_inputs,
            submit_outputs,
            show_progress="hidden",
        )
        submit_event.then(
            _append_assistant_reply,
            [chatbot, pending_message, pending_images, session_phone],
            [chatbot, pending_message, pending_images],
            show_progress="hidden",
        )

        send_event = send_btn.click(
            _append_user_message,
            submit_inputs,
            submit_outputs,
            show_progress="hidden",
        )
        send_event.then(
            _append_assistant_reply,
            [chatbot, pending_message, pending_images, session_phone],
            [chatbot, pending_message, pending_images],
            show_progress="hidden",
        )

    return demo


if __name__ == "__main__":
    create_demo().launch(server_name="0.0.0.0")
