"""Form components for user input."""

from fasthtml.common import Button, Form, Input


def chat_input_form() -> Form:
    return Form(
        Input(
            id="message-input",
            type="text",
            name="message",
            placeholder="Type your message...",
            autofocus=True,
            autocomplete="off",
        ),
        Button("Send", type="submit"),
        cls="input-form",
        id="chat-form",
        onsubmit="sendMessage(); return false;"
    )
