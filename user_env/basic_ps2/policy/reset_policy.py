from ps2env.policy_runtime import Policy


class ResetPolicy(Policy):
    def get_action(self, ctx, action=None):
        return None

    def take_action(self, ctx, action):
        return None
