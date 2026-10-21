# Heating and hot water automations

Home Assistant automations for a heat pump with underfloor heating and a solar
battery system, exported as JSON. Entity names are placeholders. Rename them to
yours.

## Heating

Underfloor heating reacts slowly, so waiting until the rooms are cold means a day
of discomfort. The goal is to heat only when needed, but to start before the cold
arrives.

- `heating-switch-from-forecast.json`: every evening at 18:00 it reads the
  daily forecast. If a night in the next two days is forecast at 4 C or colder,
  or tomorrow's average is 7 C or lower, it switches the heat pump from Summer to
  Winter a day ahead. It does nothing otherwise.
- `heating-switch-to-winter.json`: a backstop. It switches to Winter when the
  outdoor temperature stays under 6 C for 6 hours, or the hallway or a child's
  room stays under its comfort level for 3 hours.
- `heating-child-room-alert.json`: a notification if the child's room stays cold
  while heating is on, with a reminder that a dial change needs a day to show.

## Hot water

`hot-water-solar-aware-schedule.json` sets the tank target at fixed times. From
11:00 it heats to 55 C when the solar forecast for today is 10 kWh or more
(storing solar heat) and to 50 C otherwise. From 19:30 it holds 45 C overnight,
or 42 C when tomorrow's forecast is 10 kWh or more. It also restores the right
target after a restart.

The forecast sensors come from the Forecast.Solar integration.

## Notes

- A fixed electricity tariff means there is no price to shift to. The savings come
  from heating during the warmest hours and using the home's own solar power.
- These automations were checked by running them against the live system. There
  is no simulation or test harness.
