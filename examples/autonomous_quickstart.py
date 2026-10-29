"""List flows, journeys, and one journey's conversation.

Local stack: UNPOD_API_KEY=... UNPOD_ORG_HANDLE=... UNPOD_BASE_URL=http://localhost:8000 \
    python examples/autonomous_quickstart.py <space-slug>
"""

import sys

from unpod import Client

space = sys.argv[1] if len(sys.argv) > 1 else "test-calls-f1o3vprb"
client = Client()

print("connection:", client.journeys.ping())
flows = client.flows.list(space)
print("flows:", [(f.flow_id, f.status, f.version) for f in flows])
if flows:
    journeys = client.journeys.list(space, flow_id=flows[0].flow_id)
    print("journeys:", [(j.journey_id, j.status) for j in journeys[:5]])
    if journeys:
        for m in client.journeys.conversation(journeys[0]):
            print(f"[{m.direction}] {m.text[:80]}")
