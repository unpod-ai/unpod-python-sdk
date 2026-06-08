// playground/web/src/components/Orb.tsx
import { useEffect, useRef } from "react";

export type OrbState = "idle" | "connecting" | "active";

interface OrbProps {
  state: OrbState;
}

export function Orb({ state }: OrbProps) {
  const barsRef = useRef<(HTMLDivElement | null)[]>([]);
  const frameRef = useRef<number>(0);

  useEffect(() => {
    if (state !== "active") {
      cancelAnimationFrame(frameRef.current);
      return;
    }
    const animate = (t: number) => {
      barsRef.current.forEach((bar, i) => {
        if (!bar) return;
        const h = 16 + 32 * Math.abs(Math.sin(t / 400 + i * 0.9));
        bar.style.height = `${h}px`;
      });
      frameRef.current = requestAnimationFrame(animate);
    };
    frameRef.current = requestAnimationFrame(animate);
    return () => cancelAnimationFrame(frameRef.current);
  }, [state]);

  return (
    <div className={`orb orb--${state}`}>
      {state === "connecting" && <div className="orb__spinner" />}
      {state === "active" && (
        <div className="orb__bars">
          {Array.from({ length: 5 }, (_, i) => (
            <div
              key={i}
              className="orb__bar"
              ref={(el) => { barsRef.current[i] = el; }}
            />
          ))}
        </div>
      )}
    </div>
  );
}