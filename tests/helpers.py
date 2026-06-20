from app.models import OfoxModel


def model(
    model_id: str,
    *,
    released_at: int = 1,
    input_price: str | None = "0.000002",
    output_price: str | None = "0.000008",
    cache_read_price: str | None = None,
) -> OfoxModel:
    provider = model_id.split("/", 1)[0] if "/" in model_id else "unknown"
    return OfoxModel(
        id=model_id,
        name=model_id,
        provider=provider,
        released_at=released_at,
        input_price=input_price,
        output_price=output_price,
        cache_read_price=cache_read_price,
    )
