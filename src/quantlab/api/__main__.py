"""python -m quantlab.api: one loopback-only local process, no access payload logs."""
import logging
import uvicorn

from .app import create_app
from .config import APISettings


def main():
    settings = APISettings.from_env()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    server_logging = {"version": 1, "disable_existing_loggers": False,
        "formatters": {"safe": {"()": "quantlab.api.security.SafeServerFormatter"}},
        "handlers": {"safe": {"class": "logging.StreamHandler", "formatter": "safe"}},
        "loggers": {"uvicorn": {"handlers": ["safe"], "level": "INFO", "propagate": False},
            "uvicorn.error": {"level": "INFO"}, "uvicorn.access": {"handlers": [], "propagate": False}}}
    uvicorn.run(create_app(settings), host="127.0.0.1", port=settings.port,
        workers=1, reload=False, access_log=False, proxy_headers=False, log_config=server_logging)


if __name__ == "__main__":
    main()
