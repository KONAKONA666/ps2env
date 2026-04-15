from ps2env.policy_runtime import Policy


ACTION_KEYS = {
    0: None,
    1: "Up",
    2: "Down",
    3: "Left",
    4: "Right",
    5: "z",
    6: "x",
    7: "c",
    8: "s",
    9: "Return",
}


class StepPolicy(Policy):
    def get_action(self, ctx, action=None):
        return int(action or 0)

    def take_action(self, ctx, action):
        key = ACTION_KEYS.get(int(action))
        if key is not None:
            ctx.base_actions.press_key(key)
