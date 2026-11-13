# Proactive concierge

Curie's proactive concierge fills the gap between urgent alerts and social
check-ins. It can offer four kinds of initiative:

- `help`: grounded next-step or planning help based on an explicit recent user
  message.
- `assist`: drafting and decision-support offers based on explicit context.
- `ease`: occasional context-free offers to organize, draft, remind, or make a
  tedious task smaller.
- `fun`: occasional original riddles, hypotheticals, miniature stories, and
  creative diversions.

The concierge is separately opt-in. It does not perform an external action,
infer hidden activity or mood, or turn a command/acknowledgement into a personal
claim. Agenda items, reminders, deadlines, and grounded practical help outrank
entertainment. Generic `ease` and `fun` prompts each have an 18-hour minimum
category cooldown in addition to the normal quiet hours, message caps, topic
cooldowns, snooze, and explicit negative-feedback adaptation.

Owner controls:

- `/proactive concierge on|off`
- `/proactive categories help assist ease fun companion`
- `/proactive status`
- `/proactive snooze 1d`
- `/proactive exclude <topic>`
- `/proactive disable`

The `companion` category retains Curie's natural caring messages. Category
selection enables only the named lanes; it does not weaken any delivery or
safety boundary.
