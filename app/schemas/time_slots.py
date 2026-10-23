from typing import Literal, get_args

TimeSlot = Literal['现在起一小时','今天上午','今天下午','今天晚间','今天深夜','明天上午','明天下午','明天晚间']
TIME_SLOT_OPTIONS = list(get_args(TimeSlot))
