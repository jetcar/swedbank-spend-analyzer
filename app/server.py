import uvicorn

from .logging_config import logging_config


if __name__ == "__main__":
    uvicorn.run(
        "app.main:app", host="0.0.0.0", port=8000,
        log_config=logging_config("app"), access_log=False,
    )
