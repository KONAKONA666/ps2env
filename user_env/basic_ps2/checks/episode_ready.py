def check(ctx):
    return bool(ctx.game_alive and ctx.frame.size > 0), {}
