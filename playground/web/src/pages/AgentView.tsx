/** AgentView — single-agent playground: connect, talk, watch the transcript. */

import { useCallback, useRef, useState } from "react";

import { SupervoiceClient } from "../client/SupervoiceClient";
import { SupervoiceWSTransport } from "../transport/SupervoiceWSTransport";
import { ConnectButton } from "../components/ConnectButton";
import { Turn, TranscriptView } from "../components/TranscriptView";

export function AgentView() {
  const [connected, setConnected] = useState(false);
  const [connecting, setConnecting] = useState(false);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [flowNode, setFlowNode] = useState<string>("");
  const clientRef = useRef<SupervoiceClient | null>(null);

  const addTurn = useCallback((turn: Turn) => {
    setTurns((prev) => [...prev, turn]);
  }, []);

  const connect = useCallback(async () => {
    // Idempotency guard: the relay is single-session, so a second concurrent
    // /ws/audio socket would make the two bridge connections evict each other
    // forever. Tear down any existing client first, nulling the ref
    // synchronously so a re-entrant call doesn't open a parallel socket.
    const stale = clientRef.current;
    clientRef.current = null;
    if (stale) await stale.disconnect();

    setConnecting(true);
    setTurns([]);
    setFlowNode("");

    // Claim the slot synchronously (before any await) so a re-entrant call
    // sees a live client and bails above.
    const client = new SupervoiceClient({
      transport: new SupervoiceWSTransport(),
    });
    clientRef.current = client;

    client.on("connected", () => {
      setConnected(true);
      setConnecting(false);
      addTurn({ role: "system", text: "Connected — speak now." });
    });
    client.on("disconnected", () => {
      setConnected(false);
      setConnecting(false);
      addTurn({ role: "system", text: "Disconnected." });
    });
    client.on("user_turn", (d) => addTurn({ role: "user", text: String(d.text ?? "") }));
    client.on("agent_turn", (d) => addTurn({ role: "agent", text: String(d.text ?? "") }));
    client.on("flow_node_changed", (d) => setFlowNode(String(d.node_id ?? "")));
    client.on("error", (d) =>
      addTurn({ role: "system", text: `Error: ${String(d.message ?? "unknown")}` }),
    );

    try {
      await client.connect();
    } catch (err) {
      setConnecting(false);
      addTurn({ role: "system", text: `Connect failed: ${(err as Error).message}` });
      clientRef.current = null;
    }
  }, [addTurn]);

  const disconnect = useCallback(async () => {
    await clientRef.current?.disconnect();
    clientRef.current = null;
    setConnected(false);
  }, []);

  return (
    <div className="app">
      <header className="header">
        <h1>Unpod Voice Playground</h1>
        <ConnectButton
          connected={connected}
          connecting={connecting}
          onConnect={connect}
          onDisconnect={disconnect}
        />
      </header>
      {flowNode && <div className="flow-node">Flow node: {flowNode}</div>}
      <TranscriptView turns={turns} />
    </div>
  );
}
