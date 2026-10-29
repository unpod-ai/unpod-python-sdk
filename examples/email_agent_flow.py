"""An email agent in a few lines: connect a Gmail mailbox, build the flow, turn it on.

New mail to the mailbox starts a journey and the agent answers it. With
``review_sensitive`` on, replies about orders, complaints, refunds and the
like are held as drafts for a person to approve in the Unpod inbox.

Needs proxy-mode auth (a user's platform JWT and the org):

    UNPOD_TOKEN=... UNPOD_ORG_HANDLE=acme UNPOD_BASE_URL=https://... \\
        python examples/email_agent_flow.py <space handle or token> <agent id>

``<agent id>`` is one of ``client.flows.agents()``: a pilot handle, or
``sv:<agent_id>`` for an agent made with ``client.agents``.
"""

import os
import sys

from unpod import Client, JWTAuth
from unpod.flows import AgentStep, Flow

space, agent = sys.argv[1], sys.argv[2]
client = Client(
    auth=JWTAuth(os.environ["UNPOD_TOKEN"], org_handle=os.environ["UNPOD_ORG_HANDLE"]),
    base_url=os.environ.get("UNPOD_BASE_URL"),
)

# 1. Pick the mailbox: a ready one if the space has it, else connect one.
channels = client.flows.channels.list(space)
gmail = next((c for c in channels if c.type == "gmail" and c.ready), None)
if gmail is None:
    # A mailbox connected in the org but not this space is linked at once; a
    # new one needs Google's sign-in, so the URL is printed and this waits.
    mailboxes = [c for c in channels if c.type == "gmail"]
    candidate = next((c for c in mailboxes if c.connection_id), mailboxes[-1])
    gmail = client.flows.channels.connect(
        candidate,
        wait=True,
        on_sign_in_url=lambda url: print("Sign in to Gmail to connect the mailbox:\n ", url),
    ).channel
print("mailbox:", gmail.address)

# 2. Build and save the flow.
flow = client.flows.create(
    space,
    Flow(
        name="Support inbox",
        goal="Answer customer emails helpfully and briefly.",
        steps=[AgentStep(channel=gmail, agent=agent)],
        review_sensitive=True,
        review_topics=["pricing negotiations"],
    ),
)

# 3. New mail to the mailbox starts journeys; then switch it on.
client.flows.link(flow, gmail)
client.flows.activate(flow)
print("flow on:", flow.flow_id)
