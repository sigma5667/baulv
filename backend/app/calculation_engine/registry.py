from app.calculation_engine.base_trade import TradeCalculator


class TradeRegistry:
    """Registry for trade-specific calculation modules.

    Adding a new trade is as simple as:
    1. Create a new file in trades/
    2. Decorate the class with @TradeRegistry.register
    """

    _calculators: dict[str, type[TradeCalculator]] = {}

    @classmethod
    def register(cls, calculator_class: type[TradeCalculator]) -> type[TradeCalculator]:
        """Decorator to register a TradeCalculator implementation."""
        instance = calculator_class()
        cls._calculators[instance.trade_name] = calculator_class
        return calculator_class

    @classmethod
    def get(cls, trade_name: str) -> TradeCalculator:
        """Get a calculator instance by trade name."""
        if trade_name not in cls._calculators:
            available = ", ".join(cls._calculators.keys())
            raise ValueError(
                f"No calculator registered for trade '{trade_name}'. "
                f"Available trades: {available}"
            )
        return cls._calculators[trade_name]()

    @classmethod
    def available_trades(cls) -> list[str]:
        """List all registered trade names."""
        return list(cls._calculators.keys())
