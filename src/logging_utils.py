"""Presentation and per-client log summaries; no automation state or I/O reads."""
from time import monotonic
from loguru import logger


LEVEL_NAMES = dict(DEBUG='调试', INFO='信息', SUCCESS='成功', WARNING='警告', ERROR='错误', CRITICAL='严重')


def daily_text(record):
    timestamp = record['time'].strftime('%H:%M:%S') if record.get('time') else ''
    level = LEVEL_NAMES.get(record['level'].name, record['level'].name)
    client = record.get('extra', {}).get('client', '')
    return f"{timestamp} [{level}]" + (f" [{client}]" if client else '') + f" {record['message']}\n"


def daily_format(record):
    record['extra']['daily_text'] = daily_text(record)
    return '{extra[daily_text]}'  # Deliberately omit the detailed exception footer.


def detailed_format(record):
    client = record.get('extra', {}).get('client', '')
    record['extra']['client_label'] = f' [{client}]' if client else ''
    return ('<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | <level>{level: <8}</level> | '
            '<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan>{extra[client_label]} - '
            '<level>{message}</level>\n{exception}')


def log_blocked(client, task, reason, *, delay=None, channel='quest'):
    states = getattr(client, '_logging_blocks', None)
    if states is None:
        states = client._logging_blocks = {}
    previous = states.get(channel)
    # XYZ jitter must not create a new logging identity; quest/goal/zone do.
    key = ((task[0], task[1], task[3] if len(task) > 3 else task[2] if len(task) > 2 else None)
           if task is not None else previous['task'] if previous else None)
    now = monotonic()
    bound = logger.bind(client=client.title)
    if previous is None or previous['task'] != key or previous['reason'] != reason:
        states[channel] = dict(task=key, reason=reason, since=now, repeats=0)
        retry = f'，{delay:g} 秒后重试' if delay is not None else ''
        bound.warning('任务阻塞：{}{}', reason, retry)
        return
    previous['repeats'] += 1
    bound.debug('阻塞持续：{}；重复 {} 次', reason, previous['repeats'])
    if now - previous['since'] >= 30:
        bound.warning('任务仍受阻：{}（重复 {} 次）', reason, previous['repeats'])
        previous.update(since=now, repeats=0)


def log_recovered(client, result, *, channel='quest'):
    states = getattr(client, '_logging_blocks', {})
    previous = states.pop(channel, None)
    if previous is not None:
        logger.bind(client=client.title).info('恢复结果：{}；原阻塞：{}；未汇总重复 {} 次',
            result, previous['reason'], previous['repeats'])
        return True
    return False


def log_exception(client, context, exc, *, level='WARNING'):
    """One detailed traceback per exception object, even when propagated outward."""
    bound = logger.bind(client=getattr(client, 'title', client) or '')
    if level != 'DEBUG':
        bound.opt(depth=1).log(level, '{}：{}: {}', context, type(exc).__name__, exc)
    if not getattr(exc, '_xuanshu_trace_logged', False):
        exc._xuanshu_trace_logged = True
        bound.opt(exception=(type(exc), exc, exc.__traceback__), depth=1).debug(
            '{}：{}: {}', context, type(exc).__name__, exc)


def log_following(client, quester):
    if getattr(client, '_logging_following', None) != quester.title:
        client._logging_following = quester.title
        logger.bind(client=client.title).info('跟随关系：跟随 {}', quester.title)


def log_toggle(clients, enabled, source):
    names = ','.join(c.title for c in clients)
    logger.bind(client=names).info('自动任务已{}，来源：{}', '启动' if enabled else '停止', source)
    if not enabled:
        for client in clients:
            getattr(client, '_logging_blocks', {}).clear()
            client._logging_following = None
            client._logging_follow_task = None
