# WhatsApp Booking Agent (prototype)

An AI receptionist that lives on a business's WhatsApp number. Customers message
it in Hebrew, Arabic, or English; it answers questions, shows prices, and books
appointments into a real calendar of available slots. First use case: a men's
hair salon, but the salon details are just one editable file (`salon.json`).

Built with: Python + Flask (webhook), DeepSeek (the AI brain), SQLite (bookings),
Meta WhatsApp Business Cloud API (the phone number).

## What you need

- A Mac with Python 3.10+ (check with `python3 --version`)
- Your DeepSeek API key
- Later, for real WhatsApp: a free Meta developer account (steps below)

## Setup (do these one at a time, copy-paste each line)

```bash
git clone https://github.com/ma7modza-cyber/whatsapp-booking-agent.git
cd whatsapp-booking-agent
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Now put your DeepSeek key into `.env`. Easiest way:

```bash
nano .env
```

Replace `paste-your-deepseek-key-here` with your real key, then save
(`Ctrl+O`, Enter, `Ctrl+X`).

## Try it tonight (no WhatsApp needed)

```bash
python chat.py
```

Talk to it like a customer would - in any language. Try things like:
"hi, how much is a haircut?" / "can I come tomorrow at 5?" / "book it, my name is Ahmad".

Type `reset` to start the conversation over, `quit` to exit.

## Make it yours

Everything about the business is in `salon.json` - the name, opening hours per
weekday, services, durations, and prices. Edit it with `nano salon.json` and
restart. No code changes needed.

## Putting it on real WhatsApp (later, with the Meta sandbox)

1. Create a free app at https://developers.facebook.com/ and add the **WhatsApp** product.
2. Meta gives you a **test number**, a temporary **access token**, and a
   **phone number ID**. Put all three in `.env`
   (`META_ACCESS_TOKEN`, `META_PHONE_NUMBER_ID`, and make up any word for
   `META_VERIFY_TOKEN`).
3. Start the server:

   ```bash
   source .venv/bin/activate
   python server.py
   ```

4. In a second window, expose it to the internet with ngrok
   (`brew install ngrok` if you don't have it):

   ```bash
   ngrok http 5000
   ```

5. Copy the https URL ngrok shows. In the Meta app dashboard under
   WhatsApp -> Configuration, set the webhook to `https://YOUR-URL/webhook`
   with your verify token, and subscribe to **messages**.
6. Add your own phone as a recipient on the test number, then WhatsApp the test
   number. You're talking to your agent.

## Files

| File | What it does |
|---|---|
| `chat.py` | Terminal chat with the agent (test mode) |
| `server.py` | Webhook Meta calls when a WhatsApp message arrives |
| `agent.py` | The AI brain: DeepSeek + booking tools |
| `bookings.py` | Slot math and the SQLite bookings database |
| `whatsapp.py` | Sends replies through the Meta API |
| `salon.json` | The business: name, hours, services, prices |
| `.env` | Your secrets (never committed to git) |

## Notes

- `.env` is gitignored - your keys never leave your Mac.
- Bookings are stored in a local `bookings.db` file. Delete it to wipe all bookings.
- The AI model comes from `DEEPSEEK_MODEL` in `.env`. If you ever get a
  "model not found" error, try `deepseek-chat`.
