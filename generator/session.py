"""Build the events of a single session (one visit)."""

import uuid
from datetime import timedelta

from common import ConfigError, format_instant

# The funnel, in order. Every session starts at PAGE_VIEW; each later stage is
# reached only by passing one draw against its configured rate.
FIRST_EVENT_TYPE = "PAGE_VIEW"
FUNNEL_STEPS = (
    ("PRODUCT_VIEW", "page_view_to_product_view"),
    ("ADD_TO_CART", "product_view_to_add_to_cart"),
    ("CHECKOUT", "add_to_cart_to_checkout"),
    ("PURCHASE", "checkout_to_purchase"),
)
EVENT_TYPES = (FIRST_EVENT_TYPE,) + tuple(event_type for event_type, _ in FUNNEL_STEPS)
RATE_KEYS = tuple(rate_key for _, rate_key in FUNNEL_STEPS)

# Gap between two consecutive events of a session. Never zero: two events sharing
# a timestamp cannot be ordered, which would make ordering untestable later on.
MIN_GAP_SECONDS = 10
MAX_GAP_SECONDS = 120


def funnel_rates(config):
    """Read the funnel rates, rejecting anything that is not a probability.

    Validated up front so an impossible rate fails before a single event is
    produced, rather than silently skewing the whole file.
    """
    funnel = config.get("funnel") or {}
    rates = {}
    for key in RATE_KEYS:
        if key not in funnel:
            raise ConfigError(f"funnel.{key} is missing from the config")
        value = funnel[key]
        # bool is a subclass of int, so True would otherwise pass as the rate 1.
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ConfigError(
                f"funnel.{key} must be a number in [0, 1], got {value!r} ({type(value).__name__})"
            )
        if not 0.0 <= value <= 1.0:
            raise ConfigError(f"funnel.{key} must be a number in [0, 1], got {value!r}")
        rates[key] = float(value)
    return rates


def rng_uuid(rng):
    """A UUID4-shaped id drawn from the seeded RNG.

    uuid.uuid4() reads the OS entropy pool, so it would give different ids on
    every run and destroy reproducibility.
    """
    return str(uuid.UUID(int=rng.getrandbits(128), version=4))


def build_session(rng, start_time, user_ids, product_ids, rates):
    """Build one session's events, in chronological order.

    `rng` carries all the randomness of this session, so a session's output
    depends only on its own seed and start time -- not on how many sessions ran
    before it.

    The session walks the funnel from PAGE_VIEW, drawing once per step; the
    first failed draw ends the visit there. One product is chosen at
    PRODUCT_VIEW and carried through every later stage, so a purchase always
    names a product the session actually viewed.
    """
    session_id = rng_uuid(rng)
    user_id = rng.choice(user_ids)

    event_time = start_time
    product_id = None
    events = [_event(rng, session_id, user_id, FIRST_EVENT_TYPE, event_time, None)]

    for event_type, rate_key in FUNNEL_STEPS:
        if rng.random() >= rates[rate_key]:
            break
        if product_id is None:
            product_id = rng.choice(product_ids)
        event_time += timedelta(seconds=rng.randint(MIN_GAP_SECONDS, MAX_GAP_SECONDS))
        events.append(_event(rng, session_id, user_id, event_type, event_time, product_id))

    return events


def _event(rng, session_id, user_id, event_type, event_time, product_id):
    """One event. Dimensions such as country or price stay in the reference
    files: the pipeline joins them, an event stream does not carry them."""
    return {
        "event_id": rng_uuid(rng),
        "session_id": session_id,
        "user_id": user_id,
        "event_type": event_type,
        "event_time": format_instant(event_time),
        "product_id": product_id,
    }
