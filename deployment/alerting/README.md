# Telegram alerts

The production applications send operational alerts directly to Telegram through
the reusable `automation.notifications` module. No vmalert or Alertmanager
service is required.

## Create the Telegram destination

1. Open a chat with `@BotFather`, create a bot with `/newbot`, and copy its bot
   token.
2. Send a message to the new bot from the Telegram account or group that should
   receive alerts.
3. Open `https://api.telegram.org/bot<BOT_TOKEN>/getUpdates` and copy the
   `message.chat.id` value. Group chat IDs are usually negative.

Keep both values out of the repository and application logs.

## Configure CapRover

Set these environment variables on both `classical-bot` and
`classical-crawler-factory`:

```text
TELEGRAM_ALERT_BOT_TOKEN=<BotFather token>
TELEGRAM_ALERT_CHAT_ID=<destination chat ID>
```

The two applications may use the same bot and chat. Deploy each application
after setting the variables.

When Codex authentication fails, the component first creates its persistent
pause marker and then attempts the Telegram notification. A Telegram network or
configuration failure cannot prevent the component from stopping. One recovery
message is sent after the replacement authentication passes the real Codex smoke
test.

Delivery lifecycle events remain visible in VictoriaLogs:

- `notification_delivered`
- `notification_delivery_failed`
- `notification_not_configured`

## Test before relying on it

Run this inside either deployed application container:

```bash
python -c 'from automation.notifications import Notification, send_notification; raise SystemExit(0 if send_notification(Notification("ClassicalBot test", "Telegram alerts are configured correctly.", "info")) else 1)'
```

Confirm that Telegram receives the message and that the command exits with
status 0. The command does not expose either secret.
