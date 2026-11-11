"""Calibration records on instruments: certificate identity, validity and operation uncertainty."""
from datetime import date, datetime, timezone


def moment(value, key='valid_until'):
    """Read an ISO date or an ISO time with offset. A date alone ends at 23:59:59 UTC that day."""
    if not isinstance(value, str):
        raise ValueError(f'calibration.{key} must be an ISO date such as 2026-12-31')
    try:
        if len(value) == 10:
            day = date.fromisoformat(value)
            return datetime(day.year, day.month, day.day, 23, 59, 59, tzinfo=timezone.utc)
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        raise ValueError(f'calibration.{key} must be an ISO date such as 2026-12-31') from None
    if result.tzinfo is None:
        raise ValueError(f'calibration.{key} needs a UTC offset when it includes a time')
    return result


def check(value):
    if not isinstance(value, dict):
        raise ValueError('calibration must be an object')
    if not isinstance(value.get('id'), str) or not value['id'].strip():
        raise ValueError('calibration.id names the certificate or calibration record')
    for key in ('date', 'valid_until'):
        if key in value:
            moment(value[key], key)
    for key in ('certificate', 'by', 'notes'):
        if key in value and not isinstance(value[key], str):
            raise ValueError(f'calibration.{key} must be text')
    if not isinstance(value.get('enforce', False), bool):
        raise ValueError('calibration.enforce must be true or false')
    if value.get('enforce') and 'valid_until' not in value:
        raise ValueError('calibration.enforce needs valid_until')
    uncertainty = value.get('uncertainty', {})
    if not isinstance(uncertainty, dict) or any(not isinstance(item, dict) for item in uncertainty.values()):
        raise ValueError('calibration.uncertainty maps operation names to uncertainty objects')
    return value


def expired(value, now=None):
    if not value or 'valid_until' not in value:
        return False
    return (now or datetime.now(timezone.utc)) > moment(value['valid_until'])


def operation_fields(value, operation):
    """Fields copied into an operation record so each result names the calibration it relied on."""
    if not value:
        return {}
    fields = {'calibration': value['id']}
    if operation in value.get('uncertainty', {}):
        fields['uncertainty'] = value['uncertainty'][operation]
    if expired(value):
        fields['calibration_expired'] = True
    return fields
