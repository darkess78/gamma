# Twitch Operator Setup Checklist

This is the practical setup list for making Shana's Twitch integration work end to end.

## Required Accounts And Access
- Twitch broadcaster account for the channel Shana will watch.
- Twitch bot account, if you want IRC chat ingestion to use a separate identity.
- Twitch Developer application with a Client ID.
- User access token for the bot/broadcaster flow.
- The broadcaster's numeric Twitch user ID.
- The moderator's numeric Twitch user ID for follow events. This can usually be the broadcaster ID if the token belongs to the broadcaster.

Official Twitch references:
- EventSub overview: https://dev.twitch.tv/docs/eventsub/
- EventSub subscription types and scopes: https://dev.twitch.tv/docs/eventsub/eventsub-subscription-types/
- OAuth scopes: https://dev.twitch.tv/docs/authentication/scopes/

## Config Values
Set these in `config/app.local.toml` or environment variables:

```toml
twitch_channel = "your_channel_name"
twitch_bot_username = "your_bot_login"
twitch_oauth_token = "oauth:your_user_access_token"
twitch_owner_user_id = "your_owner_user_id"

twitch_client_id = "your_twitch_developer_client_id"
twitch_broadcaster_user_id = "your_broadcaster_user_id"
twitch_moderator_user_id = "your_moderator_user_id"
twitch_eventsub_enabled = true
```

Optional but recommended:

```toml
twitch_ignored_bots = "Nightbot,StreamElements,Streamlabs"
twitch_dry_run = true
twitch_voice_enabled = false
twitch_subtitles_enabled = true
twitch_min_speech_gap_seconds = 5
twitch_max_speech_seconds_per_minute = 20
stream_filtered_audio_path = "./assets/audio/system/filtered.wav"
```

## Token Scopes
IRC chat ingestion requires a token that can connect to Twitch chat for the configured account.

EventSub features need a user access token with these scopes:
- Follows: `moderator:read:followers`
- Cheers/bits: `bits:read`
- Subscriptions and resubs: `channel:read:subscriptions`
- Channel point redeems: `channel:read:redemptions` or `channel:manage:redemptions`

If a subscription fails, check the EventSub dashboard status first. The worker keeps running when individual subscriptions fail, and the status panel shows subscription success/error counts. Twitch will reject individual subscriptions when the token lacks the required scope.

## Local Runtime Requirements
- Shana API running.
- Dashboard running.
- `.venv` dependencies installed, including `websockets`.
- Twitch IRC worker started from the Stream tab for chat messages.
- Twitch EventSub worker started from the Stream tab for follows, raids, bits, subs, and channel point redeems.
- `assets/audio/system/filtered.wav` present if you want the filtered fallback to play audio instead of text-only fallback.

## Stream Readiness Checks
Before turning voice on:
- Keep `twitch_dry_run = true`.
- Start IRC worker and confirm Twitch chat appears in Stream Activity.
- Start EventSub worker and confirm EventSub status becomes connected.
- Run replay tests from the dashboard.
- Confirm Safety Log records filtered/skipped decisions.
- Confirm Stop Speech clears subtitles and cancels active live speech.
- Confirm ignored bots do not create stream events.

Only after those pass:
- Turn on subtitles.
- Turn on voice.
- Reduce dry-run usage gradually.

## Current Non-Goals
- Shana does not post Twitch chat messages.
- Shana does not control OBS.
- Shana does not perform autonomous moderation.
- Public chat does not write permanent memory.
- Self-goals require dashboard approval before becoming active.
