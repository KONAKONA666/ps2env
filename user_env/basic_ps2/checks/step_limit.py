def check(ctx):
    if ctx.step_count >= 16:
        return True, {"truncated": True, "reason": "step_limit"}
    return False, {}
