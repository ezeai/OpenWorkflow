"""Shared test setup. The temporary exit-hang diagnostic was removed: it forced exit code 1 on every run.
A lingering non-daemon thread still delays the process exit by about 35 s; that is not yet traced."""
