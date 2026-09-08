"""Local test mode: chat with your receptionist in the terminal - no WhatsApp needed.

Run:  python chat.py
Type 'quit' to exit, 'reset' to start the conversation over.
"""
from dotenv import load_dotenv

load_dotenv()

import agent  # noqa: E402

ME = "local-test-user"


def main():
    print("Salon booking agent - local test chat.")
    print("Type like a customer would (Hebrew / Arabic / English all work). 'quit' exits.\n")
    while True:
        try:
            text = input("you: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not text:
            continue
        if text.lower() in ("quit", "exit"):
            break
        if text.lower() == "reset":
            agent._conversations.pop(ME, None)
            print("(conversation reset)\n")
            continue
        try:
            print(f"agent: {agent.reply(ME, text)}\n")
        except Exception as exc:
            print(f"!! error: {exc}\n")


if __name__ == "__main__":
    main()
