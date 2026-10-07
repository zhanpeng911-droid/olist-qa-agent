"""Explicit ingestion semantics; source event time is not snapshot freshness."""
from datetime import datetime, timezone

STAGES = {'created': 0, 'approved': 1, 'processing': 2, 'invoiced': 2,
          'shipped': 3, 'delivered': 4}
TERMINAL = {'delivered', 'canceled', 'unavailable'}


def transition_allowed(old, new):
    if old in TERMINAL:
        return old == new
    if new in {'canceled', 'unavailable'}:
        return True
    return old in STAGES and new in STAGES and STAGES[new] >= STAGES[old]


def snapshot_time(value):
    if not value:
        return None
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except (TypeError, ValueError) as error:
        raise ValueError('快照时间必须为带时区的 ISO 8601 时间') from error
    if result.tzinfo is None:
        raise ValueError('快照时间必须明确时区，例如 2026-10-03T10:00:00+08:00')
    if result > datetime.now(timezone.utc):
        raise ValueError('快照时间不能晚于当前时间')
    return result.astimezone(timezone.utc).isoformat()


def validate_policy(mode='append_only', snapshot_at=None, previous=None):
    if mode not in ('append_only', 'upsert'):
        raise ValueError('仅支持追加或带版本快照更新；不支持通过缺行或空值推断删除')
    stamp = snapshot_time(snapshot_at)
    if mode == 'upsert':
        if not stamp:
            raise ValueError('快照更新必须提供可信的源快照时间；没有更新时间请使用仅追加模式')
        if previous and datetime.fromisoformat(stamp) <= datetime.fromisoformat(previous):
            raise ValueError('快照时间不晚于已发布版本，拒绝旧快照或同时间更新')
    return stamp
