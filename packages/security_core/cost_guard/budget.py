from __future__ import annotations


class TokenBudgetExceeded(Exception):
    def __init__(
        self,
        *,
        budget_limit: int,
        requested_tokens: int,
        remaining_tokens: int,
        correlation_id: str | None,
    ) -> None:
        super().__init__("The request token budget would be exceeded.")
        self.budget_limit = budget_limit
        self.requested_tokens = requested_tokens
        self.remaining_tokens = remaining_tokens
        self.correlation_id = correlation_id


class TokenReservation:
    __slots__ = ("_budget", "_settled", "_token_count")

    def __init__(self, budget: TokenBudget, token_count: int) -> None:
        self._budget = budget
        self._token_count = token_count
        self._settled = False

    @property
    def token_count(self) -> int:
        return self._token_count

    def settle(self, actual_tokens: int) -> None:
        self._budget._settle(self, actual_tokens)

    def cancel(self) -> None:
        self._budget._cancel(self)


class TokenBudget:
    def __init__(self, limit: int, *, correlation_id: str | None = None) -> None:
        if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
            raise ValueError("Token budget limit must be a positive integer.")
        self._limit = limit
        self.correlation_id = correlation_id
        self._consumed_tokens = 0
        self._reserved_tokens = 0

    @property
    def limit(self) -> int:
        return self._limit

    @property
    def consumed_tokens(self) -> int:
        return self._consumed_tokens

    @property
    def remaining_tokens(self) -> int:
        return self.limit - self._consumed_tokens - self._reserved_tokens

    def consume(self, token_count: int) -> None:
        self._validate_token_count(token_count, allow_zero=True)
        if token_count > self.remaining_tokens:
            raise self._exceeded(token_count)
        self._consumed_tokens += token_count

    def reserve(self, token_count: int) -> TokenReservation:
        self._validate_token_count(token_count)
        if token_count > self.remaining_tokens:
            raise self._exceeded(token_count)
        self._reserved_tokens += token_count
        return TokenReservation(self, token_count)

    def _settle(self, reservation: TokenReservation, actual_tokens: int) -> None:
        self._validate_reservation(reservation)
        self._validate_token_count(actual_tokens, allow_zero=True)
        additional_tokens = actual_tokens - reservation.token_count
        if additional_tokens > self.remaining_tokens:
            raise self._exceeded(actual_tokens)
        self._reserved_tokens -= reservation.token_count
        self._consumed_tokens += actual_tokens
        reservation._settled = True

    def _cancel(self, reservation: TokenReservation) -> None:
        self._validate_reservation(reservation)
        self._reserved_tokens -= reservation.token_count
        reservation._settled = True

    def _validate_reservation(self, reservation: TokenReservation) -> None:
        if reservation._budget is not self:
            raise ValueError("Token reservation belongs to a different budget.")
        if reservation._settled:
            raise ValueError("Token reservation has already been settled or cancelled.")

    def _validate_token_count(self, token_count: int, *, allow_zero: bool = False) -> None:
        minimum = 0 if allow_zero else 1
        if isinstance(token_count, bool) or not isinstance(token_count, int) or token_count < minimum:
            qualifier = "non-negative" if allow_zero else "positive"
            raise ValueError(f"Token count must be a {qualifier} integer.")

    def _exceeded(self, requested_tokens: int) -> TokenBudgetExceeded:
        return TokenBudgetExceeded(
            budget_limit=self.limit,
            requested_tokens=requested_tokens,
            remaining_tokens=self.remaining_tokens,
            correlation_id=self.correlation_id,
        )
