from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class EmailAccount:
    id: str
    provider: str

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("Account ID cannot be empty")

        if not self.provider.strip():
            raise ValueError("Provider cannot be empty")
