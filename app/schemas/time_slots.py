from typing import Literal, get_args

TimeSlot = Literal['slot.next_hour','slot.today_morning','slot.today_afternoon','slot.today_evening','slot.today_late_night','slot.tomorrow_morning','slot.tomorrow_afternoon','slot.tomorrow_evening']
TIME_SLOT_OPTIONS = list(get_args(TimeSlot))
