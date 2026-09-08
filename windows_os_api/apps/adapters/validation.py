"""Which application are we talking about? — validation of `app_id`.

Every action in this layer is scoped to an application: an adapter is created
for one, actions are invoked on one, the agent drives one. So `app_id` is the
answer to "where does this happen", and there is no safe way to guess it.

Why this module exists
----------------------
`app_id` used to carry a default of `"contoso-crm"` — the fake backend's demo
CRM — on seven call sites, REST bodies and the MCP `agent_run` tool among them.
A caller who forgot to say which application they meant did not get an error;
they got the demo app, and the request succeeded against it. On the fake backend
that is merely wrong. Pointed at a real desktop it is a request applied to
whatever application happens to answer to that name.

Making the parameter required fixes the missing case. It does not fix the empty
one: `app_id=""` is the same mistake with a different spelling, and a required
parameter that accepts it is required in name only. So the check lives here, at
`create_adapter` — the point that acts — rather than in each caller, which is
the same reason sandbox enforcement lives inside `invoke_action` (#13): a check
the caller can forget is not a check.
"""
from __future__ import annotations


class AppIdRejected(ValueError):
    """Raised when an `app_id` cannot identify an application."""


def validate_app_id(app_id: object) -> str:
    """Return the `app_id` to use, or raise `AppIdRejected`.

    Deliberately narrow: this says "you named an application", not "that
    application exists". Whether the named app is present is the backend's
    answer to give, and a missing one already has a reply — `adapter not found`.
    """
    if app_id is None:
        raise AppIdRejected("app_id is required: name the application to act on")
    if not isinstance(app_id, str):
        raise AppIdRejected(
            f"app_id must be a string, got {type(app_id).__name__}"
        )
    if not app_id.strip():
        raise AppIdRejected(
            "app_id is empty: name the application to act on"
        )
    return app_id
