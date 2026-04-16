from __future__ import annotations

from ps2env.policy_runtime import Policy


_MOVE_DIRECTIONS = {
    0: "press_left_stick_up",
    1: "press_left_stick_down",
    2: "press_left_stick_left",
    3: "press_left_stick_right",
}


def _validate_payload(action):
    if not isinstance(action, (list, tuple)):
        raise TypeError("Legacy step_policy expects [action_idx, *action_args].")
    if not action:
        raise ValueError("Legacy step_policy requires at least an action index.")
    action_index = action[0]
    if isinstance(action_index, bool) or not isinstance(action_index, int):
        raise TypeError("Legacy step_policy action index must be an integer.")
    return action_index, tuple(action[1:])


def _apply_jump(ctx, args):
    if len(args) != 1 or not isinstance(args[0], bool):
        raise ValueError("jump expects exactly one boolean hold_r1 argument.")
    if args[0]:
        ctx.base_actions.press_r1()
    ctx.base_actions.tap_triangle()
    ctx.base_actions.tap_l1()


def _apply_move(ctx, args):
    if len(args) < 2 or len(args) > 5:
        raise ValueError("move expects hold_r1 plus one to four directions.")
    hold_r1 = args[0]
    if not isinstance(hold_r1, bool):
        raise TypeError("move hold_r1 must be a boolean.")
    directions = list(dict.fromkeys(args[1:]))
    for direction in directions:
        if isinstance(direction, bool) or not isinstance(direction, int):
            raise TypeError("move directions must be integers in the range 0..3.")
        if direction not in _MOVE_DIRECTIONS:
            raise ValueError(f"Unsupported move direction: {direction}")
    if hold_r1:
        ctx.base_actions.press_r1()
    for direction in directions:
        getattr(ctx.base_actions, _MOVE_DIRECTIONS[direction])()
    ctx.base_actions.tap_l1()


def _apply_combat(ctx, args):
    if args:
        raise ValueError("combat takes no arguments.")
    ctx.base_actions.tap_square()
    ctx.base_actions.tap_l1()


class StepPolicy(Policy):
    def get_action(self, ctx, action=None):
        del ctx
        return _validate_payload(action)

    def take_action(self, ctx, action):
        action_index, action_args = action
        if action_index == 0:
            _apply_jump(ctx, action_args)
            return
        if action_index == 1:
            _apply_move(ctx, action_args)
            return
        if action_index == 2:
            _apply_combat(ctx, action_args)
            return
        raise IndexError(f"Unsupported legacy action index: {action_index}")
