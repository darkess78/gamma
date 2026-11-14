"""Exact, dependency-free admission for an isolated private Discord adapter."""


def discord_id(value):
    """Return an exact ASCII Discord snowflake string or raise ValueError."""
    if (type(value) is not str or not 1 <= len(value) <= 20 or value[0] == '0'
            or not all('0' <= char <= '9' for char in value)
            or int(value) > 2**64 - 1):
        raise ValueError('Discord ID must be an exact positive ASCII snowflake')
    return value


def validate_scope(owner_user_id, dm_channel_id, voice_guild_id=None, voice_channel_id=None):
    """Validate an owner/DM pair and optional complete private voice pair."""
    owner_user_id, dm_channel_id = discord_id(owner_user_id), discord_id(dm_channel_id)
    if voice_guild_id is not None or voice_channel_id is not None:
        voice_guild_id, voice_channel_id = discord_id(voice_guild_id), discord_id(voice_channel_id)
    return {'owner_user_id': owner_user_id, 'dm_channel_id': dm_channel_id,
            'voice_guild_id': voice_guild_id, 'voice_channel_id': voice_channel_id}


def admit_event(scope, *, user_id, channel_id, guild_id=None, is_bot=False, voice=False):
    """Return only pinned owner events; flags must be real booleans."""
    if type(scope) is not dict or type(is_bot) is not bool or type(voice) is not bool or is_bot:
        return False
    try:
        valid = validate_scope(**scope)
        user_id, channel_id = discord_id(user_id), discord_id(channel_id)
        if scope != valid or user_id != valid['owner_user_id']:
            return False
        if voice:
            return (valid['voice_guild_id'] is not None and guild_id == valid['voice_guild_id']
                    and channel_id == valid['voice_channel_id'])
        return guild_id is None and channel_id == valid['dm_channel_id']
    except (TypeError, ValueError):
        return False
