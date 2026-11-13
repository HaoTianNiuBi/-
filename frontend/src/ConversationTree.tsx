import { useEffect, useRef, useState } from "react";

/** A decorative, content-sized branch; scroll only changes its reveal, never chat state. */
export default function ConversationTree({ rounds }: { rounds: number }) {
  const ref = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ height: 620, reveal: 400 });
  useEffect(() => {
    const host = ref.current?.parentElement;
    if (!host) return;
    let frame = 0;
    const measure = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => {
        const rect = host.getBoundingClientRect();
        const height = Math.max(450, host.offsetHeight - 80);
        setSize({
          height,
          reveal: Math.max(160, Math.min(height, innerHeight - rect.top - 40)),
        });
      });
    };
    const observer = new ResizeObserver(measure);
    observer.observe(host);
    window.addEventListener("scroll", measure, { passive: true });
    window.addEventListener("resize", measure);
    measure();
    return () => {
      observer.disconnect();
      cancelAnimationFrame(frame);
      window.removeEventListener("scroll", measure);
      window.removeEventListener("resize", measure);
    };
  }, [rounds]);
  const count = Math.max(4, rounds + 3);
  const step = (size.height - 100) / count;
  const trunk = `M108 -20 ${Array.from({ length: count + 1 }, (_, i) => `Q${i % 2 ? 78 : 128} ${i * step + 20} ${i % 2 ? 101 : 112} ${(i + 1) * step}`).join(" ")}`;
  return (
    <div
      ref={ref}
      className="conversation-tree"
      aria-hidden="true"
      style={{
        height: size.height,
        clipPath: `inset(0 0 ${Math.max(0, size.height - size.reveal)}px 0)`,
      }}
    >
      <svg
        viewBox={`0 0 210 ${size.height}`}
        width="210"
        height={size.height}
        fill="none"
      >
        <path
          d={trunk}
          stroke="#b4a188"
          strokeWidth="6"
          strokeLinecap="round"
        />
        <path
          d={trunk}
          stroke="#786b50"
          strokeWidth="1.3"
          strokeDasharray="19 7 4 11"
        />
        <path
          d={trunk}
          transform="translate(2 0)"
          stroke="#dfcdb0"
          strokeWidth="1.2"
        />
        {Array.from({ length: count }, (_, i) => (
          <g
            key={i}
            transform={`translate(${i % 2 ? 103 : 110},${65 + i * step}) scale(${i % 2 ? -1 : 1},1)`}
          >
            <path
              d="M0 30Q-15 1-47-20Q-62-29-76-54M-26 3Q-25-18-33-38"
              stroke="#8b7957"
              strokeWidth="2"
            />
            <path
              d="M-30-7C-61-6-87-27-83-52C-51-48-31-30-30-7Z"
              fill={i % 3 === 0 ? "#b7bc8a" : "#94ad8b"}
              stroke="#708a65"
              strokeWidth="1"
            />
            <path
              d="M-47-22C-53-48-39-68-24-72C-17-46-29-26-47-22Z"
              fill="#ccd0a2"
              stroke="#829475"
              strokeWidth=".8"
            />
            <path
              d="M-30-7-78-48M-46-23-27-66M-45-19-48-33M-56-28-57-39M-65-36-76-35M-39-38-42-49M-34-48-28-49"
              stroke="#637e5e"
              strokeWidth=".8"
            />
            <path d="M-5 19Q14-4 35-9" stroke="#97805b" strokeWidth="1.2" />
            <path
              d="M7 7C11-12 27-21 40-18C37-1 23 9 7 7Z"
              fill="#c2b078"
              stroke="#9d8e5d"
              strokeWidth=".8"
            />
            <path d="m9 5 26-19" stroke="#8c855a" strokeWidth=".8" />
            {i % 2 === 0 && (
              <>
                <path d="M-57-34-67-65M-65-55-55-66" stroke="#95724e" />
                <circle cx="-69" cy="-68" r="3.5" fill="#b77a57" />
                <circle cx="-53" cy="-68" r="2.8" fill="#c99a61" />
                <circle cx="-63" cy="-75" r="2.2" fill="#d3af74" />
              </>
            )}
            <path d="m1 32 4 9m-8-18-3-8" stroke="#88785d" strokeWidth="1" />
          </g>
        ))}
      </svg>
    </div>
  );
}
