# ESX price bot

Fetches listed-security prices from esx.et and posts them to Telegram with emoji status,
rotating through five message layouts. Runs free on GitHub Actions every weekday at
3:15 PM EAT, just after the market closes.

## 1. Create the Telegram bot (one time)
1. In Telegram, message **@BotFather** -> `/newbot` -> follow the prompts.
   It replies with a token like `123456789:AAH...`. **This is your API key. Keep it private.**
2. Create a channel (or group), add your bot to it as an **admin** with permission to post.
3. Your chat id is the channel's handle, e.g. `@my_esx_channel`.
   (For a private channel/group, forward a message from it to @userinfobot, or ask me for
   the other ways to get the numeric id, which starts with `-100`.)

## 2. Put the code on GitHub
1. Create a **public** repository (Actions minutes are unlimited for public repos; your
   token stays hidden because it lives in Secrets, not in the code).
2. Upload `esx_bot.py`, `requirements.txt`, `README.md`, and `.github/workflows/esx-post.yml`
   (keep that exact folder path).

## 3. Add the API key  <-- this is where the token goes
Repository -> **Settings** -> **Secrets and variables** -> **Actions** -> **New repository secret**

| Name                 | Value                            |
|----------------------|----------------------------------|
| `TELEGRAM_BOT_TOKEN` | the token from @BotFather        |
| `TELEGRAM_CHAT_ID`   | e.g. `@my_esx_channel`           |

## 4. Test it
Repository -> **Actions** -> **ESX price post** -> **Run workflow**. Check your channel.
After that it runs on its own every weekday.

## Run it on your own computer
```
pip install -r requirements.txt
python esx_bot.py --post                       # preview the post, no sending
export TELEGRAM_BOT_TOKEN="123456789:AAH..."    # PowerShell: $env:TELEGRAM_BOT_TOKEN="..."
export TELEGRAM_CHAT_ID="@my_esx_channel"
python esx_bot.py --send
```

## Changing things
- Post time/days: the `cron` line in `.github/workflows/esx-post.yml` (it's in UTC; EAT = UTC+3).
- Message layouts and emojis: `TEMPLATES`, `UP`/`DOWN`/`FLAT` in `esx_bot.py`.
- Company names shown next to tickers: `NAMES` in `esx_bot.py`.
