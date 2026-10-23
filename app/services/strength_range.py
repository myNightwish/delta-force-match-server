from decimal import Decimal, ROUND_HALF_UP


def tolerance_bounds(score, percent=30):
    # Work in cents so slider preview and persisted matching boundaries agree.
    cents=int((Decimal(str(score))*100).quantize(Decimal('1'),rounding=ROUND_HALF_UP))
    return ((cents*(100-percent)+50)//100/100,(cents*(100+percent)+50)//100/100)
