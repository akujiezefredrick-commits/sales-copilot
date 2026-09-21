"""Settings loaded from .env. Read lazily so tests can override the environment."""

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


@dataclass(frozen=True)
class FieldRefs:
    full_name: str
    email: str
    phone: str
    website: str
    linkedin: str
    founder_linkedin: str = ""  # optional question: the founder's personal profile

    @classmethod
    def from_env(cls) -> "FieldRefs":
        return cls(
            full_name=env("TF_REF_FULL_NAME"),
            email=env("TF_REF_EMAIL"),
            phone=env("TF_REF_PHONE"),
            website=env("TF_REF_WEBSITE"),
            linkedin=env("TF_REF_LINKEDIN"),
            founder_linkedin=env("TF_REF_FOUNDER_LINKEDIN"),
        )
