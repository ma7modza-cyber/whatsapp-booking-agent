# WhatsApp Booking Agent (prototype)

An AI receptionist that lives on a business's WhatsApp number. Customers message
it in Hebrew, Arabic, or English; it answers questions, shows prices, and books
appointments into a real calendar of available slots. First use case: a men's
hair salon, but the salon details are just one editable file (`salon.json`).

Built with: Python + Flask (webhook), DeepSeek (the AI brain), SQLite (bookings),
Twilio WhatsApp Sandbox (the phone number - no Meta business account needed).

## What you need

- A Mac with Python 3.10+ (check with `python3 --version`)
- Your DeepSeek API key
- A free Twilio account (steps below - takes about 5 minutes, no credit card)

## Setup (do these one at a time, copy-paste each line)

```bash
git clone https://github.com/ma7modza-cyber/whatsapp-booking-agent.git
cd whatsapp-booking-agent
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Now put your keys into `.env`. Easiest way:

```bash
nano .env
```

Fill in `DEEPSEEK_API_KEY` now, and the three `TWILIO_...` values after step 2
below, then save (`Ctrl+O`, Enter, `Ctrl+X`).

## Try it first (no WhatsApp needed)

```bash
python chat.py
```

Talk to it like a customer would - in any language. Try things like:
"hi, how much is a haircut?" / "can I come tomorrow at 5?" / "book it, my name is Ahmad".

Type `reset` to start the conversation over, `quit` to exit.

## Put it on real WhatsApp (Twilio Sandbox)

### 1. Create the Twilio account

Go to https://www.twilio.com/try-twilio and sign up (free, no credit card).
Verify your email and your phone number when it asks.

### 2. Turn on the WhatsApp sandbox

1. In the Twilio Console, open **Messaging**. Under **Popular channels**, click
   **WhatsApp**, then **Try out WhatsApp**.
2. Accept the sandbox terms.
3. The page shows a sandbox number (usually `+1 415 523 8886`) and your personal
   join code (two words, like `join happy-tiger`).
4. From your own WhatsApp, send `join your-two-words` to that number. Twilio
   replies to confirm you joined.
5. Still on the Console home page, find the **Account Info** box and copy your
   **Account SID** and **Auth Token** into `.env`:

   ```
   TWILIO_ACCOUNT_SID=ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
   TWILIO_AUTH_TOKEN=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
   TWILIO_WHATSAPP_FROM=whatsapp:+14155238886
   ```

### 3. Start the server on your Mac

```bash
source .venv/bin/activate
nohup python server.py > server.log 2>&1 &
```

(`nohup ... &` keeps it running even if your Termius session drops.)

### 4. Expose it to the internet with Tailscale Funnel

You already have Tailscale running on the Mac, so this is one command:

```bash
tailscale funnel --bg 5000
```

It prints an https address like `https://mahmoods-mac.tail1234.ts.net/` -
copy it. (`--bg` keeps the tunnel alive in the background. To stop it later:
`tailscale funnel --https=443 off`.)

If the command says Funnel is not enabled, do this one-time fix: open the
Tailscale admin console (https://login.tailscale.com/admin/acls), and in the
policy file add this block, then save:

```json
"nodeAttrs": [
  { "target": ["autogroup:member"], "attr": ["funnel"] }
],
```

No Tailscale? Fallback with ngrok (`brew install ngrok` if you don't have it):

```bash
ngrok http 5000
```

and copy the https URL it shows instead.

### 5. Point Twilio at your server

1. Back in the Twilio Console, open the sandbox page again
   (**Messaging** -> **Try it out** -> **Send a WhatsApp message**) and go to
   the **Sandbox settings** tab.
2. In the field **"When a message comes in"**, paste your tunnel URL with
   `/webhook` at the end, for example:
   `https://mahmoods-mac.tail1234.ts.net/webhook`
   Leave the method as **HTTP POST**. Save.

### 6. Talk to your agent

WhatsApp any message to the sandbox number - the agent answers. You're live.

## Sandbox limitations (trial account) - good to know

- **Only people who joined can message it.** Anyone else who wants to try the
  demo must first send the same `join your-two-words` code to the sandbox
  number. Messages from non-joined numbers silently fail (Twilio error 63015).
- **24-hour reply window.** The agent can reply freely for 24 hours after the
  customer's last message. Starting a conversation yourself (the customer went
  quiet for a day) needs pre-approved message templates on a trial account.
  For a demo where customers message first, this doesn't matter.
- **It's Twilio's shared number**, not yours. Replies come from
  `+1 415 523 8886`, and the chat is marked as a Twilio sandbox. Fine for a
  demo; a real business number comes later with a paid upgrade.
- The sandbox is for **testing only** - no load testing.

## Make it yours

Everything about the business is in `salon.json` - the name, opening hours per
weekday, services, durations, and prices. Edit it with `nano salon.json` and
restart the server. No code changes needed.

## Files

| File | What it does |
|---|---|
| `chat.py` | Terminal chat with the agent (test mode) |
| `server.py` | Webhook Twilio (or Meta) calls when a WhatsApp message arrives |
| `agent.py` | The AI brain: DeepSeek + booking tools |
| `bookings.py` | Slot math and the SQLite bookings database |
| `whatsapp.py` | Sends replies - Twilio if configured, Meta otherwise |
| `salon.json` | The business: name, hours, services, prices |
| `.env` | Your secrets (never committed to git) |

## Notes

- `.env` is gitignored - your keys never leave your Mac.
- Bookings are stored in a local `bookings.db` file. Delete it to wipe all bookings.
- The AI model comes from `DEEPSEEK_MODEL` in `.env`. If you ever get a
  "model not found" error, try `deepseek-chat`.
- The Meta Cloud API path still works: leave the `TWILIO_...` values empty and
  fill in the `META_...` ones instead.
