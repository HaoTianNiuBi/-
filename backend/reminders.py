"""Resolve reminder times against the local clock after intent extraction."""

import re
from datetime import datetime, timedelta


REQUEST = re.compile(r'^\s*(?P<prefix>[^。！？；：“”"「」\n]{0,36}?)提醒我\s*[：:，,]?\s*(?P<task>.*)$', re.S)
WEEKDAYS = '一二三四五六日'
CHINESE_HOURS = {hour: str(index) for index, hour in enumerate(
    ('零', '一', '二', '三', '四', '五', '六', '七', '八', '九', '十', '十一', '十二')
)}
CHINESE_HOURS['两'] = '2'


def request_candidate(text):
    match = REQUEST.fullmatch(text)
    if match and not re.search(r'(?:别|不要|不用|不必|无需|不需要)\s*$', match['prefix']):
        return match
    return None


def resolve_time(phrase, stamp):
    """Return (due milliseconds, date only), or None if the time needs clarification."""
    local = datetime.fromtimestamp(stamp / 1000)
    phrase = re.sub(r'\s+', '', phrase)
    phrase = re.sub(r'([十一二三四五六七八九两]{1,2})(?=[点时])',
                    lambda match: CHINESE_HOURS.get(match[1], match[1]),
                    phrase)
    if phrase in ('', '下次', '下次打开', '下次打开应用', '下次打开首页', '下次回到首页'):
        return stamp, False
    offset = re.fullmatch(r'(\d{1,4})(分钟|小时)后', phrase)
    if offset and int(offset[1]) > 0:
        unit = 60_000 if offset[2] == '分钟' else 3_600_000
        return stamp + int(offset[1]) * unit, False
    parts = re.fullmatch(
        r'(今天|明天|后天|(?:本|这|下|下下)(?:周|星期)[一二三四五六日天]|(?:\d{4}年)?\d{1,2}月\d{1,2}[日号]|\d{4}-\d{2}-\d{2})'
        r'(?:(凌晨|早上|上午|中午|下午|晚上)?(\d{1,2})(?:[:：](\d{2})|[点时](?:(\d{1,2})分|半)?))?', phrase,
    )
    if not parts:
        return None
    day, period, hour, colon_minutes, chinese_minutes = parts.groups()
    target = local.replace(hour=0, minute=0, second=0, microsecond=0)
    try:
        if day in ('今天', '明天', '后天'):
            target += timedelta(days=('今天', '明天', '后天').index(day))
        elif '周' in day or '星期' in day:
            weeks = 2 if day.startswith('下下') else int(day.startswith('下'))
            weekday = WEEKDAYS.index(day[-1].replace('天', '日'))
            target += timedelta(days=7 * weeks + weekday - local.weekday())
        elif '-' in day:
            target = datetime.strptime(day, '%Y-%m-%d')
        else:
            year, month, date = re.fullmatch(r'(?:(\d{4})年)?(\d{1,2})月(\d{1,2})[日号]', day).groups()
            target = target.replace(year=int(year or local.year), month=int(month), day=int(date))
        if hour is not None:
            hours = int(hour)
            if period:
                if not 1 <= hours <= 12:
                    return None
                if period in ('中午', '下午', '晚上'):
                    hours = hours % 12 + 12
                elif hours == 12:
                    hours = 0
            minutes = int(colon_minutes or chinese_minutes or ('30' if phrase.endswith('半') else '0'))
            target = target.replace(hour=hours, minute=minutes)
        if target.date() < local.date() or (hour is not None and target < local):
            return None
        return int(target.timestamp() * 1000), hour is None
    except (ValueError, OverflowError, OSError):
        return None
