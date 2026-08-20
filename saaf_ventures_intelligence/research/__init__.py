def historical_replay_report(*args, **kwargs):
    from .backtest import historical_replay_report as implementation
    return implementation(*args, **kwargs)


def temporal_drift_report(*args, **kwargs):
    from .drift import temporal_drift_report as implementation
    return implementation(*args, **kwargs)

__all__ = ["historical_replay_report", "temporal_drift_report"]
