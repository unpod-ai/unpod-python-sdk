/** ConnectButton — toggles the call connection. */

interface ConnectButtonProps {
  connected: boolean;
  connecting: boolean;
  onConnect: () => void;
  onDisconnect: () => void;
}

export function ConnectButton({
  connected,
  connecting,
  onConnect,
  onDisconnect,
}: ConnectButtonProps) {
  if (connected) {
    return (
      <button className="btn btn-danger" onClick={onDisconnect}>
        Disconnect
      </button>
    );
  }
  return (
    <button className="btn btn-primary" disabled={connecting} onClick={onConnect}>
      {connecting ? "Connecting…" : "Connect"}
    </button>
  );
}
