import atexit

from django.apps import AppConfig  # type: ignore[import-untyped]

_atexit_registered = False


class LlmPersonaDjviewConfig(AppConfig):
    name = 'llemon_djview'
    label = 'hty7_llemon_djview'
    verbose_name = 'LLemon Django Views'

    def ready(self) -> None:
        """Register SSH host-tunnel cleanup once per interpreter.

        Django's autoreloader is not the risk here — a reload spawns a new
        process with its own atexit table. The guard is for a single
        interpreter populating the app registry more than once (e.g. a test
        runner, or a process that loads more than one Django project) that
        would otherwise install a duplicate handler. A second registration
        would be harmless on its own (close_host_tunnels() against an empty
        cache is a no-op) but the guard avoids paying for it twice.
        """
        global _atexit_registered
        if _atexit_registered:
            return
        _atexit_registered = True
        from hty7.llemon.persona import discover
        atexit.register(discover.close_host_tunnels)
