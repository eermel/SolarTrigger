from datetime import datetime, timedelta, timezone

def calculate_timezone_from_coords(lat, lon, eclipse_date=None, log=None):
    if isinstance(eclipse_date,str):
        try: ref=datetime.strptime(eclipse_date[:10],"%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError: ref=datetime.now(timezone.utc)
    elif isinstance(eclipse_date,datetime): ref=eclipse_date if eclipse_date.tzinfo else eclipse_date.replace(tzinfo=timezone.utc)
    else: ref=datetime.now(timezone.utc)
    try:
        from timezonefinder import TimezoneFinder
        import pytz
        name=TimezoneFinder().timezone_at(lat=lat,lng=lon)
        if name:
            off=ref.astimezone(pytz.timezone(name)).utcoffset().total_seconds()/3600
            if log: log.info(f"Timezone : {name} → UTC{off:+.1f}")
            return off
    except Exception: pass
    import calendar
    def last_sunday(y,m):
        d=datetime(y,m,calendar.monthrange(y,m)[1],tzinfo=timezone.utc); return d-timedelta(days=(d.weekday()+1)%7)
    dst=last_sunday(ref.year,3).replace(hour=1)<=ref<last_sunday(ref.year,10).replace(hour=1)
    base=max(-12,min(14,round(lon/15.0)))
    if -10<=lon<=40 and lat>35: return (1 if lon<22 else 2)+(1 if dst else 0)
    if -130<=lon<=-60 and 25<=lat<=70: return base+(1 if dst else 0)
    return base
