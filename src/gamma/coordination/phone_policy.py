"""Mechanical contact vetoes. Eligibility alone never authorizes a message."""
import math


def _number(value):
    return type(value) in {int, float} and math.isfinite(value) and value >= 0


def contact_block_reason(brain, device, policy, now, contacts=()):
    if not all(isinstance(v, dict) for v in (brain, device, policy)) or not _number(now):
        return 'invalid_contact_state'
    if (type(brain.get('awake')) != bool or not isinstance(brain.get('focus'), str)
            or brain.get('focus') not in {'relaxed', 'focused'}
            or type(device.get('connected')) != bool or not _number(device.get('expires_at'))
            or type(policy.get('enabled')) != bool or not _number(policy.get('quiet_until'))
            or type(policy.get('minimum_interval')) != int or not 60 <= policy['minimum_interval'] <= 86400
            or type(policy.get('maximum_per_day')) != int or not 1 <= policy['maximum_per_day'] <= 12
            or not isinstance(contacts, (list, tuple)) or len(contacts) > 100
            or any(not _number(value) or value > now for value in contacts)):
        return 'invalid_contact_state'
    if not policy['enabled']:
        return 'contact_disabled'
    if not brain['awake']:
        return 'asleep'
    if brain['focus'] == 'focused':
        return 'focused'
    if not device['connected'] or device['expires_at'] <= now:
        return 'offline'
    if device.get('availability') != 'available':
        return 'unavailable'
    if policy['quiet_until'] > now:
        return 'quiet_period'
    if sum(value > now - 86400 for value in contacts) >= policy['maximum_per_day']:
        return 'daily_contact_limit'
    if contacts and now - max(contacts) < policy['minimum_interval']:
        return 'contact_cooldown'
    return None
